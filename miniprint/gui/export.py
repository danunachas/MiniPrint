"""Downloads: one part as 3MF / STL, or every build plate as 3MF.  Each function returns
(file bytes, file name, content type) and expects the caller to hold ``S.lock``."""
from __future__ import annotations

import io
import os
import re
import tempfile
import zipfile

import numpy as np

from ..stlio import write_stl
from ..threemf import write_3mf, write_3mf_groups
from . import layout
from .session import S


def _written(write, *args, **kw):
    """The bytes of a file produced by one of the writers (they write to a path)."""
    fd, tmp = tempfile.mkstemp(prefix="miniprint_", suffix=".tmp")
    os.close(fd)
    try:
        write(tmp, *args, **kw)
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(tmp)


def part_file(part, fmt="3mf", full=False, anchor=True, centre=None):
    """The part alone, centred on the plate.  fmt: "3mf" (model + supports as one object), "model" or
    "supports" (STL)."""
    V, F = part.current_full() if full else part.current()
    sup = part.supports
    has_sup = sup is not None and len(sup.faces) > 0
    if anchor and sup is not None:
        V, F = part.with_anchor((V, F))
    everything = np.vstack([V, sup.vertices]) if has_sup else V
    cx, cy = centre or (S.plate["w"] / 2, S.plate["d"] / 2)
    shift = np.array([cx - (everything[:, 0].min() + everything[:, 0].max()) / 2,
                      cy - (everything[:, 1].min() + everything[:, 1].max()) / 2, 0.0])
    name = part.name
    if fmt == "3mf":
        objs = [(f"{name} model", V + shift, F)]
        if has_sup:
            objs.append((f"{name} supports", sup.vertices + shift, sup.faces))
        return _written(write_3mf, objs, group=name if len(objs) > 1 else None), f"{name}_supported.3mf", "model/3mf"
    if fmt == "model":
        return _written(write_stl, V + shift, F), f"{name}_model.stl", "model/stl"
    if fmt == "supports":
        if not has_sup:
            raise ValueError("no supports generated yet")
        return _written(write_stl, sup.vertices + shift, sup.faces), f"{name}_supports.stl", "model/stl"
    raise ValueError("unknown format")


def plate_files(project, full=False, anchor=True):
    """One 3MF per plate (every part = one object made of a model part and a supports part, already in
    place); several plates come as one ZIP."""
    project = re.sub(r"[^A-Za-z0-9_.-]+", "_", project or "")[:60] or "MiniPrint_project"
    files = []
    for k in range(S.nplates):
        groups = []
        for p, model, sup, _ in layout.plate_meshes(k, full, anchor=anchor):
            pieces = [(f"{p.name} model", *model)]
            if sup is not None:
                pieces.append((f"{p.name} supports", *sup))
            groups.append((p.name, pieces))
        files.append((f"{project}_plate{k + 1}.3mf", _written(write_3mf_groups, groups)))
    if len(files) == 1:
        return files[0][1], files[0][0], "model/3mf"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue(), f"{project}_{len(files)}_plates.zip", "application/zip"
