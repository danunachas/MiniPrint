"""Per-face 'detail' weight: how much fine surface structure (relief, fingers, folds) a face carries.

Idea: on a plain surface (flat plate, smooth sleeve) the normals of neighbouring faces agree.
On engraved/embossed relief, fingers, rims they do not. We average the normals inside small
cells (default ~0.9 mm) and use how much the average falls short of unit length.
Result is rotation invariant, so it is computed once per model.
"""
from __future__ import annotations

import numpy as np

from .geometry import face_normals_areas


def face_detail(V: np.ndarray, F: np.ndarray, cell: float = 0.9, full_scale: float = 0.2):
    """Return dict(normals, areas, centroids, detail) where detail is in [0, 1] per face.

    full_scale: a cell whose mean normal is this much shorter than 1 counts as maximal detail.
    """
    n, a = face_normals_areas(V, F)
    c = V[F].mean(1)
    lo = c.min(0)
    acc = np.zeros(len(F))
    # two grids shifted by half a cell, averaged, to soften cell-border artefacts
    for shift in (0.0, 0.5):
        idx = np.floor((c - lo) / cell + shift).astype(np.int64)
        dims = idx.max(0) + 2
        key = (idx[:, 0] * dims[1] + idx[:, 1]) * dims[2] + idx[:, 2]
        _, inv = np.unique(key, return_inverse=True)
        inv = inv.reshape(-1)
        m = inv.max() + 1
        sa = np.bincount(inv, weights=a, minlength=m)
        sn = np.stack([np.bincount(inv, weights=a * n[:, k], minlength=m) for k in range(3)], 1)
        R = np.linalg.norm(sn, axis=1) / np.maximum(sa, 1e-15)
        acc += (1.0 - R)[inv]
    acc /= 2.0
    detail = np.clip(acc / full_scale, 0.0, 1.0)
    return {"normals": n, "areas": a, "centroids": c, "detail": detail}
