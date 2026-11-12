"""Small watertight mesh primitives (no boolean operations needed).

Supports are built as a pile of simple closed shells (cone frusta, spheres).
Slicers union overlapping shells inside one part, so we never need a CSG library.
"""
from __future__ import annotations

import numpy as np


def _basis(u: np.ndarray):
    u = np.asarray(u, float)
    u = u / np.linalg.norm(u)
    h = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    t1 = np.cross(u, h)
    t1 /= np.linalg.norm(t1)
    t2 = np.cross(u, t1)
    return u, t1, t2


def frustum(a, ra: float, b, rb: float, seg: int = 12):
    """Closed cone frustum from centre a (radius ra) to centre b (radius rb)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    _, t1, t2 = _basis(b - a)
    th = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    ring = np.cos(th)[:, None] * t1 + np.sin(th)[:, None] * t2
    va = a + ra * ring
    vb = b + rb * ring
    verts = np.vstack([va, vb, a[None], b[None]])
    ca, cb = 2 * seg, 2 * seg + 1
    i = np.arange(seg)
    j = (i + 1) % seg
    faces = np.concatenate(
        [
            np.stack([i, j, seg + j], 1),
            np.stack([i, seg + j, seg + i], 1),
            np.stack([np.full(seg, cb), seg + i, seg + j], 1),
            np.stack([np.full(seg, ca), j, i], 1),
        ]
    )
    return verts, faces


def sphere(c, r: float, lat: int = 5, lon: int = 10):
    c = np.asarray(c, float)
    verts = [c + [0, 0, r]]
    for k in range(1, lat):
        phi = np.pi * k / lat
        for m in range(lon):
            th = 2 * np.pi * m / lon
            verts.append(c + r * np.array([np.sin(phi) * np.cos(th), np.sin(phi) * np.sin(th), np.cos(phi)]))
    verts.append(c + [0, 0, -r])
    verts = np.array(verts)
    faces = []
    for m in range(lon):
        faces.append([0, 1 + m, 1 + (m + 1) % lon])
    for k in range(lat - 2):
        for m in range(lon):
            a = 1 + k * lon + m
            b = 1 + k * lon + (m + 1) % lon
            c2 = 1 + (k + 1) * lon + m
            d = 1 + (k + 1) * lon + (m + 1) % lon
            faces.append([a, c2, b])
            faces.append([b, c2, d])
    last = len(verts) - 1
    base = 1 + (lat - 2) * lon
    for m in range(lon):
        faces.append([last, base + (m + 1) % lon, base + m])
    return verts, np.array(faces)


def hz_tube(P, R, seg: int = 12):
    """Closed tube along a polyline whose z strictly decreases (top -> bottom), built from HORIZONTAL rings.
    Horizontal rings make every joint seamless (no kinks or gaps between tilted cylinders), the tube can
    never dip below its lowest point, and each printed layer sees a clean round section."""
    P = np.asarray(P, float)
    R = np.asarray(R, float)
    n = len(P)
    th = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    cs = np.column_stack([np.cos(th), np.sin(th)])
    rings = np.empty((n, seg, 3))
    rings[:, :, :2] = P[:, None, :2] + R[:, None, None] * cs[None]
    rings[:, :, 2] = P[:, 2][:, None]
    V = np.vstack([rings.reshape(-1, 3), P[0][None], P[-1][None]])
    ct, cb = n * seg, n * seg + 1
    k = np.arange(seg)
    k1 = (k + 1) % seg
    F = []
    for i in range(n - 1):
        a, b = i * seg, (i + 1) * seg
        F.append(np.stack([a + k, b + k, b + k1], 1))
        F.append(np.stack([a + k, b + k1, a + k1], 1))
    F.append(np.stack([np.full(seg, ct), k, k1], 1))                       # top cap, normal up
    last = (n - 1) * seg
    F.append(np.stack([np.full(seg, cb), last + k1, last + k], 1))         # bottom cap, normal down
    return V, np.vstack(F).astype(np.int64)


def merge(meshes):
    """Concatenate (V, F) pairs into one (V, F)."""
    vs, fs, off = [], [], 0
    for v, f in meshes:
        vs.append(v)
        fs.append(f + off)
        off += len(v)
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(vs), np.vstack(fs).astype(np.int64)


def merge_shells(meshes, jitter=1e-3, seed=7):
    """Like merge, but nudges every closed shell sideways by a random micron-sized offset (default up to 1 um) so no two
    shells share an exact vertex position.  Slicers weld identical positions (always for STL), and two shells
    that touch at the very same points would turn into non-manifold edges after welding."""
    if not meshes:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    rng = np.random.default_rng(seed)
    off = rng.uniform(-jitter, jitter, size=(len(meshes), 3))
    off[:, 2] = 0.0                     # sideways only: nothing may sink below the bed
    return merge([(np.asarray(v, float) + o, f) for (v, f), o in zip(meshes, off, strict=True)])


def signed_volume(V: np.ndarray, F: np.ndarray) -> float:
    t = V[F]
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


def face_normals_areas(V: np.ndarray, F: np.ndarray):
    t = V[F]
    cr = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(cr, axis=1)
    area = 0.5 * ln
    n = cr / np.maximum(ln, 1e-15)[:, None]
    return n, area


def box(lo, hi):
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    V = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    )
    F = np.array(
        [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    )
    return V, F


def rotation_matrix(axis: str, deg: float) -> np.ndarray:
    """3x3 rotation about the x, y or z axis (right-handed, degrees)."""
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

