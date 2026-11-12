"""State of the running GUI: the parts of the project and everything MiniPrint worked out for them.

MiniPrint is a single-user program that runs on the user's own computer, so there is exactly one session
(``S``).  Background jobs and HTTP requests run on different threads; anything that reads or changes parts
takes ``S.lock``.
"""
from __future__ import annotations

import dataclasses
import struct
import threading
import time
import traceback

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from .. import __version__
from ..anchor import anchor_cube
from ..geometry import merge, rotation_matrix, signed_volume
from ..plate import rot2
from ..supports import SupportParams
from . import logfile


def params_from(cls, values):
    """A parameter dataclass (SupportParams, OrientParams) from the dict the page sends; unknown keys are
    ignored, so an old profile or a newer page never breaks a request."""
    names = {f.name for f in dataclasses.fields(cls)}
    obj = cls(**{k: v for k, v in (values or {}).items() if k in names})
    if cls is SupportParams:
        obj.mode = "tree"                       # the GUI only offers tree supports
    return obj


def mesh_bytes(V, F):
    """Compact binary mesh for the page: uint32 vertex count, uint32 triangle count, float32 xyz, uint32 ijk."""
    V = np.asarray(V, np.float32)
    F = np.asarray(F, np.uint32)
    return struct.pack("<II", len(V), len(F)) + V.tobytes() + F.tobytes()


class Job:
    """One background task; the page polls its log until it is done."""

    def __init__(self):
        self.log: list[str] = []
        self.done = False
        self.error = None
        self.result = None
        self.started = time.time()


class Part:
    """One model file of the project (a figurine part) with everything MiniPrint worked out for it."""

    def __init__(self, pid, name, V, F):
        self.id = pid
        self.name = name
        self.orig = (V, F)        # exactly as loaded
        self.base = None          # reduced + scaled, in the orientation of the file
        self.R = np.eye(3)        # orientation applied on top of base
        self.off = np.zeros(2)    # sideways shift after turning by hand (it turns about its own middle)
        self.finalists = None     # orientation candidates of the last search (see orient.choose_orientation)
        self.cand = None          # summary rows of those candidates, for the page
        self.sel = -1             # chosen candidate (-1 = turned by hand / not searched)
        self.sheet = None         # path of the comparison picture
        self.supports = None      # SupportResult for the current orientation
        self.sup_params = None
        self.stats = None
        self.check = None         # result of the slice check
        self.scale = 1.0
        self.lift = 0.0           # mm the model is raised above the plate (no bed contact)
        self.float_at = None      # calibration pieces: always float this high (mm) on their supports
        self.place = None         # {"plate", "angle", "tx", "ty"}: where it sits on which plate
        self.last_place = None    # where it sat before its pose or supports changed
        self.footprint_cache = None
        self.anchor_cache = None
        self.processing = None    # "oriented" / "supported" while the batch works on it (then nothing else may)
        self.queue = None         # "orient" / "supports": waiting for the batch
        self.touched = False      # the user changed it while the batch runs: the batch leaves it alone
        self.pose_v = 0           # counts changes of the pose (the page reloads the view when it changes)
        self.posed = False        # an orientation was chosen (search or by hand) - the batch keeps it

    # ---- geometry --------------------------------------------------------------------------------
    def current(self):
        """The working mesh (reduced, scaled) in its current pose, standing ``lift`` above the plate."""
        V, F = self.base
        P = V @ self.R.T
        P = P - np.array([-self.off[0], -self.off[1], P[:, 2].min() - self.lift])
        return P, F

    def current_full(self):
        """The ORIGINAL (unreduced) mesh in the current pose and scale, standing at the same height."""
        V, F = self.orig
        P = (np.asarray(V, float) * self.scale) @ self.R.T
        P = P - np.array([-self.off[0], -self.off[1], P[:, 2].min() - self.lift])
        return P, F

    def anchor(self):
        """Anchor block (V, F) under a model that floats above the plate, so it stays in place after
        "Split to objects" in the slicer (see anchor.py); None when the model touches the plate."""
        if self.anchor_cache is None:
            sup = self.supports
            cube, _ = anchor_cube(self.current(), (sup.vertices, sup.faces) if sup is not None else (None, []))
            self.anchor_cache = (cube,)
        return self.anchor_cache[0]

    def with_anchor(self, mesh):
        a = self.anchor()
        return merge([mesh, a]) if a is not None else mesh

    def footprint(self):
        """Top-view outline of model + supports + anchor block (convex hull points, part coordinates)."""
        if self.footprint_cache is None:
            pts = [self.current()[0][:, :2]]
            sup = self.supports
            if sup is not None and len(sup.vertices):
                pts.append(sup.vertices[:, :2])
            a = self.anchor()
            if a is not None:
                pts.append(a[0][:, :2])
            P = np.vstack(pts)
            try:
                P = P[ConvexHull(P).vertices]
            except QhullError:                        # degenerate (a line): the points themselves will do
                pass
            self.footprint_cache = P
        return self.footprint_cache

    def placed_outline(self):
        """Top-view outline on its plate (plate coordinates)."""
        return self.footprint() @ rot2(self.place["angle"]).T + [self.place["tx"], self.place["ty"]]

    # ---- changes ---------------------------------------------------------------------------------
    def changed(self):
        """Pose or supports changed: the plate layout no longer fits it (remember where it was, so it can go
        back to the same spot if it still fits there)."""
        if self.place is not None:
            P = self.placed_outline()
            c = (P.min(0) + P.max(0)) / 2
            self.last_place = dict(self.place, cx=float(c[0]), cy=float(c[1]))
        self.place = None
        self.footprint_cache = None
        self.anchor_cache = None

    def drop_supports(self):
        """Forget the supports (and the lift that came with them): the model stands on the plate again."""
        self.supports = self.stats = self.check = None
        self.lift = 0.0
        self.changed()

    def set_base(self, V, F, scale):
        """A freshly prepared (reduced, scaled) mesh: everything worked out before is gone."""
        self.base = (np.asarray(V, float) * scale, F)
        self.scale = scale
        self.R = np.eye(3)
        self.off = np.zeros(2)
        self.pose_v += 1
        self.finalists = self.cand = None
        self.sel = -1
        self.posed = False
        self.drop_supports()

    def set_pose(self, R, sel):
        """Stand the part in orientation R (candidate number ``sel`` of the search, or -1)."""
        self.R = np.array(R, float)
        self.sel = sel
        self.pose_v += 1
        self.posed = True
        self.drop_supports()

    def rotate(self, axis, deg):
        """Turn by hand about its own middle.  A turn about the vertical axis does not change how the part
        prints, so its supports turn along; any other turn removes them."""
        before = self.current()[0]
        c0 = (before[:, :2].min(0) + before[:, :2].max(0)) / 2
        Rm = rotation_matrix(axis, deg)
        self.R = Rm @ self.R
        self._keep_centre(c0)
        sup = self.supports
        if axis == "z" and sup is not None:
            after = self.current()[0]
            move = after[0] - Rm @ before[0]                # exact rigid move of every model point
            sup.vertices = sup.vertices @ Rm.T + move
            for name in ("points", "failed", "candidates"):
                a = getattr(sup, name, None)
                if a is not None and len(a):
                    setattr(sup, name, np.asarray(a).reshape(-1, 3) @ Rm.T + move)
            self.check = None
            self.changed()
        else:
            self.drop_supports()

    def reset_rotation(self):
        """Back to the orientation of the file."""
        before = self.current()[0]
        c0 = (before[:, :2].min(0) + before[:, :2].max(0)) / 2
        self.R = np.eye(3)
        self._keep_centre(c0)
        self.drop_supports()

    def _keep_centre(self, c0):
        self.pose_v += 1
        after = self.current()[0]
        c1 = (after[:, :2].min(0) + after[:, :2].max(0)) / 2
        self.off = self.off + (c0 - c1)
        self.sel = -1
        self.posed = True

    # ---- what the page shows ------------------------------------------------------------------------
    def state(self):
        """Short machine-readable state for the parts list."""
        if self.processing:
            return "orienting" if self.processing == "oriented" else "supporting"
        if self.base is None:
            return "loading"
        if self.supports is not None:
            return "supported"
        if self.queue == "orient":
            return "queued"
        if self.posed:
            return "waiting" if self.queue == "supports" else "oriented"
        return "new"

    def status(self):
        """The same for people."""
        if self.base is None:
            return "loaded"
        if self.supports is not None:
            st = self.supports.stats
            failed = st.get("failed", 0)
            return f"supported ({st.get('supported', 0)} tips" + (f", {failed} unsupported)" if failed else ")")
        if self.finalists is not None:
            return f"pose #{self.sel + 1}" if self.sel >= 0 else "turned by hand"
        return "turned by hand" if self.posed else "ready"

    def summary(self):
        """One row of the parts list."""
        d = {"id": self.id, "name": self.name, "orig_tris": len(self.orig[1]), "status": self.status(),
             "has_supports": self.supports is not None, "lift": self.lift, "posed": self.posed,
             "state": self.state(), "pose_v": self.pose_v,
             "plate": None if self.place is None else self.place["plate"] + 1}
        if self.base is not None:
            P, F = self.current()
            d.update(tris=len(F), size=[round(float(x), 1) for x in np.ptp(P, axis=0)])
        return d

    def info(self):
        """Everything the page shows about the part that is being edited."""
        info = {"id": self.id, "name": self.name, "loaded": self.base is not None, "version": __version__,
                "orig_tris": len(self.orig[1]), "cand": self.cand, "sel": self.sel,
                "stats": self.stats, "check": self.check, "has_supports": self.supports is not None,
                "state": self.state(), "pose_v": self.pose_v, "lift": self.lift, "failed": []}
        if self.base is not None:
            P, F = self.current()
            info.update(tris=len(F), size=[round(float(x), 2) for x in np.ptp(P, axis=0)],
                        volume=round(float(signed_volume(P, F)), 1), scale=self.scale)
        if self.supports is not None:
            info["failed"] = np.asarray(self.supports.failed).reshape(-1, 3).round(3).tolist()
        return info


class Session:
    """All parts of the project; the page edits one 'active' part at a time."""

    def __init__(self):
        self.lock = threading.RLock()
        self.parts: dict[str, Part] = {}
        self.active = None
        self.next_id = 0
        self.nplates = 0
        self.plate = {"w": 256.0, "d": 256.0, "gap": 4.0, "margin": 5.0, "turn": True}
        self.too_big: list[str] = []   # ids of parts that fit no plate, even turned
        self.batch = None              # {"phase", "n", "i"} while "Orient + support all parts" runs
        self.jobs: dict[str, Job] = {}
        self.job_count = 0

    def part(self, pid=None):
        return self.parts.get(pid or self.active)

    def add(self, name, V, F):
        self.next_id += 1
        pid = f"p{self.next_id}"
        base, k = name, 2
        while any(p.name == name for p in self.parts.values()):
            name = f"{base} ({k})"
            k += 1
        self.parts[pid] = Part(pid, name, V, F)
        self.active = pid
        return self.parts[pid]

    def remove(self, pid):
        self.parts.pop(pid, None)
        if self.active == pid:
            self.active = next(iter(self.parts), None)
        self.too_big = [k for k in self.too_big if k != pid]
        if not any(p.place is not None for p in self.parts.values()):
            self.nplates = 0

    def clear(self):
        """Start over with an empty project (plate settings stay)."""
        self.parts.clear()
        self.active = None
        self.nplates = 0
        self.too_big = []

    def set_plate(self, cfg):
        """Take over the plate settings the page sent (size, gap, margin, turning allowed)."""
        self.plate.update({k: cfg[k] for k in ("w", "d", "gap", "margin", "turn") if k in (cfg or {})})

    def arranged(self):
        return self.nplates > 0 and any(q.place is not None for q in self.parts.values())

    def info(self):
        p = self.part()
        info = p.info() if p else {"loaded": False, "version": __version__}
        info["parts"] = [q.summary() for q in self.parts.values()]
        info["active"] = self.active
        used = [q.place["plate"] for q in self.parts.values() if q.place is not None]
        self.nplates = max(used) + 1 if used else 0          # plates that still hold something
        info["nplates"] = self.nplates
        info["batch"] = self.batch
        info["too_big"] = [self.parts[k].name for k in self.too_big if k in self.parts]
        return info

    def start(self, fn):
        """Run fn(log) on a background thread; returns the job id the page polls."""
        self.job_count += 1
        jid = str(self.job_count)
        job = Job()
        self.jobs[jid] = job

        def log(*a):
            line = " ".join(str(x) for x in a)
            job.log.append(line)
            logfile.write(line)

        def run():
            try:
                job.result = fn(log)
            except MemoryError:
                job.error = ("Not enough memory for this part. Import it again with fewer triangles "
                             "(“Keep triangles” 20–40 % is usually invisible on an FDM print).")
                logfile.write("job failed: out of memory")
            except Exception as ex:                     # shown in the page's log
                job.error = f"{type(ex).__name__}: {ex}"
                job.log.append(traceback.format_exc())
                logfile.write(traceback.format_exc())
            job.done = True

        threading.Thread(target=run, daemon=True).start()
        return jid


S = Session()
