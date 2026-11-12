"""HTTP interface between the page and the session: one small function per address.

A route function takes a ``Request`` and returns

  * a dict or list  -> sent as JSON,
  * a ``Reply``     -> sent as it is (meshes, pictures, downloads),

or raises ``ApiError(status, message)``.  Work that takes longer than a moment is started as a background
job and the route returns ``{"job": id}``; the page then polls ``GET /api/job``.

To add a feature: write a function here with ``@get("/api/...")`` or ``@post("/api/...")``, keep the real work
in ``jobs.py`` / ``layout.py`` / the core modules, and call it from the page with ``api()`` or ``job()``
(``web/core.js``).
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import numpy as np

from .. import __version__
from ..geometry import merge
from ..stlio import read_stl_bytes
from . import export, jobs, layout, presets
from .session import S, mesh_bytes

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(PACKAGE, "web")
EXAMPLES = os.path.join(os.path.dirname(PACKAGE), "examples")
MAX_UPLOAD = 2 * 1024 ** 3                       # bytes; a sculpt of 10 million triangles is 0.5 GB
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon"}
ROUTES: dict[tuple[str, str], object] = {}


def host_allowed(host, local_only):
    """May a request addressed to `host` (the Host header, without port) be answered?

    Started for this computer only: just the loopback names.  Started with --host for the home network or a
    container: also plain IP addresses, names without a dot, names of the usual home-network kinds, and
    whatever MINIPRINT_HOSTS lists - but not arbitrary internet domain names."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    if local_only:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    extra = {h.strip().lower() for h in os.environ.get("MINIPRINT_HOSTS", "").split(",") if h.strip()}
    return "." not in host or host.endswith((".local", ".lan", ".home", ".home.arpa", ".internal")) or host in extra


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Reply:
    """A response that is not JSON."""

    def __init__(self, body, ctype="application/octet-stream", filename=None):
        self.body, self.ctype, self.filename = body, ctype, filename


class Request:
    def __init__(self, handler, query):
        self._handler = handler
        self.query = query
        self._json = None

    def body(self):
        n = int(self._handler.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ApiError(413, "file too large")
        return self._handler.rfile.read(n) if n else b""

    @property
    def json(self):
        if self._json is None:
            raw = self.body()
            self._json = json.loads(raw) if raw else {}
        return self._json

    def flag(self, name, default="0"):
        return self.query.get(name, default) == "1"


def get(path):
    return lambda fn: ROUTES.setdefault(("GET", path), fn)


def post(path):
    return lambda fn: ROUTES.setdefault(("POST", path), fn)


def _mesh(V, F):
    return Reply(mesh_bytes(V, F))


def _active_part(req=None, prepared=True, editing=False):
    """The part the page works on.  editing=True: the request changes it, which is refused while the batch
    has it in hand and otherwise tells the batch to leave this part to the user."""
    part = S.part(req.query.get("id") if req else None)
    if part is None:
        raise ApiError(400, "load a model first")
    if editing:
        if part.processing:
            raise ApiError(409, f"{part.name} is being {part.processing} right now - pick it again in a moment")
        part.touched = True
        part.queue = None
    if prepared and part.base is None:
        raise ApiError(400, "this part is not prepared yet")
    return part


# ---------------------------------------------------------------------------------------------
# project, parts
# ---------------------------------------------------------------------------------------------
@get("/api/health")
def health(req):
    """Cheap "is it running" answer (used by the Docker health check)."""
    return {"ok": True, "version": __version__}


@get("/api/info")
def info(req):
    with S.lock:
        return S.info()


@get("/api/job")
def job_state(req):
    job = S.jobs.get(req.query.get("id", ""))
    if job is None:
        raise ApiError(404, "unknown job")
    since = int(req.query.get("since", 0))
    return {"done": job.done, "error": job.error, "log": job.log[since:], "n": len(job.log),
            "result": job.result if job.done else None}


@post("/api/load")
def load(req):
    """Upload one STL file (the request body); ``?name=`` is its file name."""
    filename = req.query.get("name", "model")
    V, F = read_stl_bytes(req.body(), filename)
    with S.lock:
        part = S.add(os.path.splitext(os.path.basename(filename))[0] or "model", V, F)
    return {"orig_tris": len(F), "name": part.name, "id": part.id}


def _examples():
    """Example models shipped next to the program (missing when MiniPrint was installed as a bare package)."""
    if not os.path.isdir(EXAMPLES):
        return {}
    return {os.path.splitext(f)[0]: os.path.join(EXAMPLES, f) for f in sorted(os.listdir(EXAMPLES))
            if f.lower().endswith(".stl")}


@get("/api/examples")
def examples(req):
    return [{"name": name, "mb": round(os.path.getsize(path) / 1048576, 1)} for name, path in _examples().items()]


@post("/api/load_example")
def load_example(req):
    """Add one of the example models to the project: {name}."""
    path = _examples().get(req.json.get("name"))
    if path is None:
        raise ApiError(404, "no such example")
    with open(path, "rb") as fh:
        V, F = read_stl_bytes(fh.read(), os.path.basename(path))
    with S.lock:
        part = S.add(req.json["name"], V, F)
    return {"orig_tris": len(F), "name": part.name, "id": part.id}


@post("/api/select")
def select(req):
    with S.lock:
        if req.json.get("id") not in S.parts:
            raise ApiError(404, "no such part")
        S.active = req.json["id"]
        return S.info()


@post("/api/remove")
def remove(req):
    with S.lock:
        part = S.parts.get(req.json.get("id"))
        if part is not None and part.processing:
            raise ApiError(409, f"{part.name} is being {part.processing} right now")
        S.remove(req.json.get("id"))
        return S.info()


@post("/api/touch")
def touch(req):
    """The user started to edit the active part by hand: the batch leaves it alone from now on."""
    with S.lock:
        _active_part(prepared=False, editing=True)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# work on the active part
# ---------------------------------------------------------------------------------------------
@post("/api/prepare")
def prepare(req):
    with S.lock:
        part = _active_part(prepared=False, editing=True)
    return {"job": S.start(jobs.job_prepare(part, req.json))}


@post("/api/orient")
def orient(req):
    with S.lock:
        part = _active_part(editing=True)
    return {"job": S.start(jobs.job_orient(part, req.json))}


@post("/api/apply_orient")
def apply_orient(req):
    """Stand the part in candidate ``index`` of the last orientation search."""
    with S.lock:
        part = _active_part(editing=True)
        if part.finalists is None:
            raise ApiError(400, "run the orientation search first")
        i = int(req.json["index"])
        part.set_pose(part.finalists[i]["R"], i)
        return S.info()


@post("/api/rotate")
def rotate(req):
    """Turn by hand: {axis: "x" | "y" | "z", deg} or {reset: true}."""
    with S.lock:
        part = _active_part(editing=True)
        if req.json.get("reset"):
            part.reset_rotation()
        else:
            part.rotate(req.json.get("axis", "z"), float(req.json.get("deg", 90)))
        return S.info()


@post("/api/supports")
def supports(req):
    with S.lock:
        part = _active_part(editing=True)
    return {"job": S.start(jobs.job_supports(part, req.json))}


@post("/api/clear_supports")
def clear_supports(req):
    with S.lock:
        _active_part(editing=True).drop_supports()
        return S.info()


@post("/api/check")
def check(req):
    with S.lock:
        part = _active_part()
        if part.processing:
            raise ApiError(409, f"{part.name} is being {part.processing} right now - pick it again in a moment")
    return {"job": S.start(jobs.job_check(part))}


@get("/api/mesh")
def mesh(req):
    with S.lock:
        part = S.part(req.query.get("id"))
        if part is None or part.base is None:
            raise ApiError(404, "no model")
        m = part.current()
        if req.flag("anchor") and part.supports is not None:
            m = part.with_anchor(m)
        return _mesh(*m)


@get("/api/supports_mesh")
def supports_mesh(req):
    with S.lock:
        part = S.part(req.query.get("id"))
        if part is None or part.supports is None:
            raise ApiError(404, "no supports")
        return _mesh(part.supports.vertices, part.supports.faces)


@get("/api/orient_sheet")
def orient_sheet(req):
    part = S.part(req.query.get("id"))
    if part is None or not part.sheet or not os.path.exists(part.sheet):
        raise ApiError(404, "no comparison picture")
    with open(part.sheet, "rb") as fh:
        return Reply(fh.read(), "image/png")


# ---------------------------------------------------------------------------------------------
# everything at once
# ---------------------------------------------------------------------------------------------
@post("/api/process_all")
def process_all(req):
    with S.lock:
        if not S.parts:
            raise ApiError(400, "load some models first")
        if S.batch is not None:
            raise ApiError(409, "already running")
        S.batch = {"phase": "starting", "n": 0, "i": 0}
    return {"job": S.start(jobs.job_process_all(req.json))}


@post("/api/calibration")
def calibration(req):
    if S.batch is not None:
        raise ApiError(409, "wait until “Orient + support all parts” is done")
    return {"job": S.start(jobs.job_calibration(req.json))}


# ---------------------------------------------------------------------------------------------
# build plates
# ---------------------------------------------------------------------------------------------
@post("/api/arrange")
def arrange(req):
    lines = []
    result = layout.arrange(req.json, lines.append, keep=bool(req.json.get("keep")))
    return dict(result, message=" ".join(lines))


@post("/api/place")
def place(req):
    """Move / turn one part on its plate by hand: {id, dx, dy, dangle (degrees), plate}."""
    j = req.json
    with S.lock:
        part = S.parts.get(j.get("id"))
        if part is None or part.base is None:
            raise ApiError(400, "no such part")
        if part.processing:
            raise ApiError(409, f"{part.name} is being {part.processing} right now")
        layout.move_part(part, float(j.get("dx", 0.0)), float(j.get("dy", 0.0)), float(j.get("dangle", 0.0)),
                         int(j.get("plate", 0)))
        overlaps, outside = layout.plate_problems()
        return {"ok": True, "overlaps": overlaps, "outside": outside}


@get("/api/plate_mesh")
def plate_mesh(req):
    """All models (what=model) or all supports (what=supports) of a plate as one mesh."""
    k = int(req.query.get("plate", 0))
    models = req.query.get("what", "model") == "model"
    with S.lock:
        items = layout.plate_meshes(k, anchor=req.flag("anchor"), staged=True)
        meshes = [model if models else sup for _, model, sup, _ in items]
        meshes = [m for m in meshes if m is not None]
        if not meshes:
            return _mesh(np.zeros((0, 3)), np.zeros((0, 3), np.int64))
        return _mesh(*merge(meshes))


@get("/api/plate_parts")
def plate_parts(req):
    with S.lock:
        parts = layout.plate_index(int(req.query.get("plate", 0)), anchor=req.flag("anchor"))
        overlaps, outside = layout.plate_problems()
        return {"parts": parts, "overlaps": overlaps, "outside": outside}


# ---------------------------------------------------------------------------------------------
# downloads, settings
# ---------------------------------------------------------------------------------------------
@get("/api/export")
def export_part(req):
    q = req.query
    with S.lock:
        part = S.part()
        if part is None or part.base is None:
            raise ApiError(400, "no model")
        centre = (float(q["bx"]), float(q["by"])) if "bx" in q and "by" in q else None
        try:
            return Reply(*_swap(export.part_file(part, q.get("fmt", "3mf"), req.flag("full"),
                                                 req.flag("anchor", "1"), centre)))
        except ValueError as ex:
            raise ApiError(400, str(ex)) from None


@get("/api/export_plates")
def export_plates(req):
    with S.lock:
        if not S.arranged():
            raise ApiError(400, "arrange the plates first")
        return Reply(*_swap(export.plate_files(req.query.get("name"), req.flag("full"), req.flag("anchor", "1"))))


def _swap(result):
    data, filename, ctype = result
    return data, ctype, filename


@get("/api/presets")
def presets_get(req):
    return presets.load()


@post("/api/presets")
def presets_post(req):
    try:
        return presets.update(req.json.get("action"), req.json.get("name"), req.json.get("values"))
    except ValueError as ex:
        raise ApiError(400, str(ex)) from None


# ---------------------------------------------------------------------------------------------
# the server side of a request
# ---------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "MiniPrint/" + __version__
    local_only = True            # set by server.main(): only answer requests addressed to this computer

    def log_message(self, *args):                    # quiet: the console shows MiniPrint's own messages
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        try:
            self._check_caller(method)
            if method == "GET" and url.path in ("/", "/index.html"):
                return self._file("index.html")
            if method == "GET" and url.path.startswith("/web/"):
                return self._file(os.path.basename(url.path))
            fn = ROUTES.get((method, url.path))
            if fn is None:
                raise ApiError(404, "not found")
            result = fn(Request(self, {k: v[0] for k, v in parse_qs(url.query).items()}))
            if isinstance(result, Reply):
                self._send(200, result.body, result.ctype, result.filename)
            else:
                self._send(200, json.dumps(result).encode(), "application/json")
        except ApiError as ex:
            self._send(ex.status, json.dumps({"error": str(ex)}).encode(), "application/json")
        except (BrokenPipeError, ConnectionResetError):
            pass                                     # the page was closed while we answered
        except Exception as ex:
            traceback.print_exc()
            self._send(500, json.dumps({"error": f"{type(ex).__name__}: {ex}"}).encode(), "application/json")

    def _check_caller(self, method):
        """MiniPrint has no log-in, so it must not answer to other websites: a page somewhere on the internet
        could otherwise make the browser talk to MiniPrint behind the user's back (by pointing a domain name
        of its own at this computer, or by posting a form)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if not host_allowed(host, self.local_only):
            raise ApiError(403, "MiniPrint was started for use on this computer only (see --host); to reach it under "
                                f"the name {host!r}, list that name in the environment variable MINIPRINT_HOSTS")
        origin = self.headers.get("Origin")
        if method == "POST" and origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise ApiError(403, "request from another website refused")

    def _file(self, name):
        path = os.path.join(WEB, name)
        if not os.path.isfile(path):
            raise ApiError(404, "not found")
        with open(path, "rb") as fh:
            self._send(200, fh.read(), CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))

    def _send(self, status, body, ctype, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            safe = re.sub(r'[^A-Za-z0-9 ._()+-]', "_", filename)
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()
        self.wfile.write(body)
