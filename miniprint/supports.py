"""Support generation: from a model in its print pose to one pile of closed support shells.

  1. overhangs.py   find what would print in mid-air and put contact points on it
  2. tree.py        grow trees from the points down to the plate (pillars.py for the few a tree cannot reach)
  3. rafts.py       join the support rafts under the trunks
  4. slicecheck.py  slice the result virtually; ``build_checked`` adds tips where material still floats

All sizes are millimetres of the final print.  The settings are ``SupportParams`` (params.py).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.spatial import cKDTree

from .geometry import face_normals_areas, merge, merge_shells, signed_volume
from .overhangs import candidate_dirs, find_overhang_points, surface_samples, vertex_normals
from .params import SupportParams, SupportResult
from .pillars import PillarBuilder
from .rafts import pad_area, pad_network
from .raycast import DownRaycaster
from .slicecheck import slice_check
from .tree import TreeBuilder

__all__ = ["SupportParams", "SupportResult", "build_checked", "build_supports", "flat_base_area", "plate_params",
           "surface_samples"]


def flat_base_area(V, F, tol=0.2, cos_max=0.985):
    """mm^2 of model faces that lie flat (within ~10 degrees) on the lowest level of the model: what it would
    stand on."""
    T = V[F]
    z0 = float(V[:, 2].min())
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    flat = (T[:, :, 2].max(1) <= z0 + tol) & (n[:, 2] < -cos_max * np.maximum(ln, 1e-30))
    return float(0.5 * ln[flat].sum())


def plate_params(V, F, p: SupportParams):
    """The settings this pose is really built with: with "no bed contact" a part with a big flat face on the
    plate stands on that face instead of floating (when flat_on_plate is on).
    Returns (params, flat base area in mm^2)."""
    a = flat_base_area(V, F)
    if p.raft and p.flat_on_plate and a >= p.flat_min_area:
        return replace(p, raft=False), a
    return p, a


def _contact_points(V, F, p, ray, log, manual_add, manual_erase):
    """Automatic contact points, minus the user's keep-clear zones, plus the user's own tips.
    Returns (points, normals, is_island, forced)."""
    pts, nrm, isl = find_overhang_points(V, F, replace(p, spacing=p.tree_spacing) if p.mode == "tree" else p, ray, log)
    if manual_erase and len(pts):
        keep = np.ones(len(pts), bool)
        for c, r in manual_erase:
            keep &= np.linalg.norm(pts - np.asarray(c, float), axis=1) > r
        log(f"  manual erase removed {int((~keep).sum())} automatic points")
        pts, nrm, isl = pts[keep], nrm[keep], isl[keep]
    forced = np.zeros(len(pts), bool)
    if manual_add:
        mp = np.array([a[0] for a in manual_add], float).reshape(-1, 3)
        mn = np.array([a[1] for a in manual_add], float).reshape(-1, 3)
        mn = mn / np.maximum(np.linalg.norm(mn, axis=1, keepdims=True), 1e-12)
        if len(pts):
            d, _ = cKDTree(mp).query(pts)
            keep = d > 0.8 * p.spacing          # a hand-placed tip replaces its automatic neighbours
            pts, nrm, isl, forced = pts[keep], nrm[keep], isl[keep], forced[keep]
        pts = np.vstack([mp, pts]) if len(pts) else mp
        nrm = np.vstack([mn, nrm]) if len(nrm) else mn
        isl = np.concatenate([np.zeros(len(mp), bool), isl])
        forced = np.concatenate([np.ones(len(mp), bool), forced])
        log(f"  manual tips: {len(mp)}")
    return pts, nrm, isl, forced


def build_supports(V, F, p: SupportParams, log=print, manual_add=None, manual_erase=None) -> SupportResult:
    """V, F: the model in final print coordinates, resting on z = (elevation >= 0), plate at z = 0.

    manual_add:   list of (point xyz, surface normal xyz) the user wants a tip on (always attempted)
    manual_erase: list of (centre xyz, radius) where automatic tips are not wanted
    """
    ray = DownRaycaster(V, F, cell=0.5)
    if p.raft:
        p = replace(p, pad_thickness=p.raft_thickness, pad_merge=max(p.pad_merge, 1.0))
    pts, nrm, isl, forced = _contact_points(V, F, p, ray, log, manual_add, manual_erase)
    log(f"  support points to place: {len(pts)}")
    fn, _ = face_normals_areas(V, F)
    kd = cKDTree(surface_samples(V, F, 0.25))
    # upward facing or vertical surfaces can carry an overhang right above them
    carrying = F[fn[:, 2] > -0.2]
    kd_sup = cKDTree(surface_samples(V, carrying, 0.25)) if len(carrying) and len(pts) else None

    pillars = PillarBuilder(p, kd, ray, fn)
    trees = TreeBuilder(V, kd, p, log) if p.mode == "tree" and len(pts) else None
    meshes = []
    ok_pts, kinds, failed, fail_info = [], [], [], []
    skipped = []                                   # (point, reason) of candidates deliberately left without a tip

    for k in range(len(pts)):
        pt, n = pts[k], nrm[k]
        if pt[2] < 0.25 and not forced[k]:          # inside the squashed first layers: prints on the plate
            skipped.append((pt, "near_bed"))
            continue
        if pt[2] < 0.2:
            failed.append(pt)
            fail_info.append((pt, n, False, {"too_low": 1}))
            continue
        if kd_sup is not None and not forced[k]:
            # a true island (lowest point of a protruding feature) starts printing in mid-air unless a surface is
            # practically touching it, so it only gets the short-lip exemption for a much smaller reach
            near = kd_sup.query_ball_point(pt, r=(0.35 if isl[k] else p.self_support_reach))
            if near and np.any(kd_sup.data[near, 2] < pt[2] - 0.1):
                skipped.append((pt, "short_lip"))   # short lip right above a wall/floor: prints fine without support
                continue
        if trees is not None and trees.add_tip(pt, n, candidate_dirs(n, p.max_tilt)):
            ok_pts.append(pt)
            kinds.append("tree")
            continue
        why = {}
        result = pillars.support(pt, n, why)
        if result:
            parts, kind = result
            meshes.extend(parts)
            ok_pts.append(pt)
            kinds.append(kind)
        elif "gap_too_small" in why:
            skipped.append((pt, "tiny_gap"))        # rests <1.4 mm above another surface: a short bridge
        elif pt[2] < 0.6 and not forced[k]:
            skipped.append((pt, "near_bed"))        # a few layers above the plate next to the model's own footprint
        elif kd_sup is not None and not forced[k] and not isl[k] and kd_sup.query(pt)[0] < p.clearance + 0.3:
            skipped.append((pt, "hugs_wall"))       # the head cannot fit, and the wall holds the lip up
        else:
            failed.append(pt)
            fail_info.append((pt, n, bool(isl[k]), why))

    pad_meshes, bases, lengths = pillars.pad_meshes, pillars.bases, pillars.lengths
    if trees is not None:
        trees.grow()
        meshes.extend(trees.build())
        pad_meshes.extend(trees.pad_meshes)
        bases.extend(trees.bases)
        lengths.extend(trees.lengths)
        log(f"  tree: {trees.tip_count} tips -> {trees.trunks} trunks, {trees.merges} merges, {trees.braces} braces")

    # ---- join the support rafts into connected plates (adhesion), including the model's own footprint
    links, raft_area, raft_t = 0, 0.0, 0.0
    net = []
    if bases:
        raft_t = float(np.median([b[3] for b in bases]))
        if p.pad_merge > 0:
            net, links, raft_area = pad_network(V, F, fn, bases, p, log)
        else:
            centres = np.array([[b[0], b[1]] for b in bases])
            radii = np.array([b[2] for b in bases])
            raft_area = pad_area(centres, radii, np.ones(len(bases), bool), set(), [], pad_merge_on=False)
    upright = merge(meshes)
    upright_volume = abs(signed_volume(*upright)) if len(upright[1]) else 0.0

    Vs, Fs = merge_shells(meshes + pad_meshes + net)
    if len(Vs):
        Vs[:, 2] = np.maximum(Vs[:, 2], 0.0)       # guard: nothing below the build plate, ever
    reasons = [why for _, why in skipped]
    stats = {
        "contact_points": len(pts),
        "supported": len(ok_pts),
        "to_bed": kinds.count("bed") + kinds.count("tree"),
        "trunks": trees.trunks if trees is not None else 0,
        "braces": trees.braces if trees is not None else 0,
        "on_model": kinds.count("model"),
        "struts_in_gaps": kinds.count("strut"),
        "skipped_near_bed": reasons.count("near_bed"),
        "skipped_tiny_gap": reasons.count("tiny_gap"),
        "skipped_short_lip": reasons.count("short_lip") + reasons.count("hugs_wall"),
        "pad_links": links,
        "pad_area_mm2": round(raft_area, 1),
        "support_volume": round(upright_volume + raft_area * raft_t, 1),
        "failed": len(failed),
        "island_points": int(isl.sum()),
        "support_triangles": len(Fs),
    }
    return SupportResult(
        vertices=Vs, faces=Fs, points=np.array(ok_pts).reshape(-1, 3), kinds=kinds,
        failed=np.array(failed).reshape(-1, 3), stats=stats, candidates=pts, fail_info=fail_info,
        pillar_lengths=np.array(lengths), bases=np.array(bases).reshape(-1, 4)[:, :3], skipped=skipped)


# --------------------------------------------------------------------------------------
# build, slice-check, and add tips where material would still start in mid-air
# --------------------------------------------------------------------------------------
def _island_tip(V, vn, island):
    """Lowest model vertex at a reported island -> (point, normal) for a forced tip, or None."""
    q = np.array([island["x"], island["y"], island["z"]])
    dxy = np.linalg.norm(V[:, :2] - q[:2], axis=1)
    # the island starts inside the sampled layer (centre q.z, 0.2 mm thick): look there first, close by
    for r, below, above in ((0.5, 0.25, 0.15), (0.8, 0.4, 0.2), (1.2, 0.6, 0.3)):
        sel = (dxy < r) & (V[:, 2] > q[2] - below) & (V[:, 2] < q[2] + above)
        if sel.any():
            k = np.where(sel)[0][np.argmin(V[sel, 2])]
            n = vn[k] if vn[k, 2] < -0.2 else np.array([0.0, 0.0, -1.0])
            return V[k].copy(), n
    return None


def build_checked(V, F, p: SupportParams, log=print, manual_add=None, manual_erase=None, rounds=2):
    """build_supports + virtual slice check; islands the check finds get a forced tip and the supports are
    rebuilt (up to `rounds` times).  Returns (result, check, added tips)."""
    fn, area = face_normals_areas(V, F)
    vn = vertex_normals(V, F, fn, area)
    add = list(manual_add or [])
    added = []
    res = build_supports(V, F, p, log, manual_add=add, manual_erase=manual_erase)
    for rnd in range(rounds + 1):
        check = slice_check((V, F), (res.vertices, res.faces), overhang_angle=p.overhang_angle, log=log)
        if not check["islands"] or rnd == rounds:
            break
        new = [t for t in (_island_tip(V, vn, it) for it in check["islands"]) if t is not None]
        new = [t for t in new if all(np.linalg.norm(t[0] - a[0]) > 0.3 for a in add)]
        if not new:
            break
        log(f"  slice check found {len(check['islands'])} floating island(s): adding {len(new)} tip(s) and rebuilding")
        add += new
        added += new
        res = build_supports(V, F, p, log, manual_add=add, manual_erase=manual_erase)
    res.stats["auto_island_tips"] = len(added)
    return res, check, added
