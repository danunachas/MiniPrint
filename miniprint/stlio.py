"""Reading and writing STL files (binary and ASCII) using only numpy."""
from __future__ import annotations

import os

import numpy as np

_BIN_DTYPE = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])


def _looks_like_ascii(head: bytes) -> bool:
    head = head[:1024]
    if not head.lstrip().lower().startswith(b"solid"):
        return False
    text = sum(32 <= c < 127 or c in (9, 10, 13) for c in head)
    return text > 0.97 * len(head) and b"facet" in head.lower()


def _binary_count(buf) -> int | None:
    """Number of triangles if the data is a binary STL, else None."""
    size = len(buf)
    if size < 84:
        return None
    n = int(np.frombuffer(buf, dtype="<u4", count=1, offset=80)[0])
    if 84 + 50 * n == size:
        return n
    if _looks_like_ascii(bytes(buf[:1024])):
        return None
    # mismatch: stale bytes after the data (trust the header, as slicers do) or a header count that
    # promises more than the file holds (truncated / wrong header: read what is there)
    return n if 84 + 50 * n < size else (size - 84) // 50


def read_stl(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (vertices (V,3) float64, faces (F,3) int64) with shared vertices welded."""
    with open(path, "rb") as f:
        buf = f.read()
    return read_stl_bytes(buf, os.path.basename(path))


def read_stl_bytes(buf, name: str = "model") -> tuple[np.ndarray, np.ndarray]:
    """Same as read_stl for the file's contents (e.g. straight from an upload, no temporary file)."""
    n = _binary_count(buf)
    if n is not None:
        data = np.frombuffer(buf, dtype=_BIN_DTYPE, offset=84, count=n)
        tris = data["v"].astype(np.float64)
        tris = tris[np.isfinite(tris).all(axis=(1, 2))]
    else:
        verts = []
        for line in bytes(buf).decode("utf-8", errors="ignore").splitlines():
            parts = line.split()
            if parts and parts[0] == "vertex":
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        tris = np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
    if not len(tris):
        raise ValueError(f"no triangles found in {name} (not an STL file, or empty)")
    return weld(tris)


def weld(tris: np.ndarray, decimals: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Merge identical vertices of a triangle soup (F,3,3) -> (V,3), (F,3)."""
    pts = tris.reshape(-1, 3)
    q = np.round(pts * 10.0 ** decimals).astype(np.int64)
    # one sort on a mixed 64-bit key instead of a 3-key sort (several times faster); if two different
    # points ever share a key (practically never) fall back to the exact 3-key sort
    key = q[:, 0] * np.int64(73856093) ^ q[:, 1] * np.int64(19349663) ^ q[:, 2] * np.int64(83492791)
    order = np.argsort(key)
    ks = key[order]
    qs = q[order]
    same_key = ks[1:] == ks[:-1]
    diff_q = np.any(qs[1:] != qs[:-1], axis=1)
    if (same_key & diff_q).any():
        order = np.lexsort((q[:, 2], q[:, 1], q[:, 0]))
        qs = q[order]
        diff_q = np.any(qs[1:] != qs[:-1], axis=1)
    new = np.empty(len(qs), bool)
    new[0] = True
    new[1:] = diff_q
    gid = np.cumsum(new) - 1
    inverse = np.empty(len(pts), np.int64)
    inverse[order] = gid
    uniq = pts[order[new]].astype(np.float64)
    del q, qs
    faces = inverse.reshape(-1, 3)
    # drop degenerate (zero-index-distinct) faces
    ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    return uniq, drop_duplicate_faces(faces[ok])


def drop_duplicate_faces(F: np.ndarray) -> np.ndarray:
    """Triangles listed twice: two copies facing opposite ways cancel out (an internal double wall),
    copies facing the same way are kept once.  Both are common export glitches that slicers flag as
    non-manifold edges."""
    if not len(F):
        return F
    s = np.sort(F, 1)
    # quick test first: no two triangles with the same three corners -> nothing to do (the usual case)
    key = s[:, 0] * np.int64(73856093) ^ s[:, 1] * np.int64(19349663) ^ s[:, 2] * np.int64(83492791)
    o = np.argsort(key)
    ks = key[o]
    same = ks[1:] == ks[:-1]
    if not same.any():
        return F
    ss = s[o]
    if (same & np.any(ss[1:] != ss[:-1], axis=1)).any():      # key clash between different triangles
        _, inv, cnt = np.unique(s, axis=0, return_inverse=True, return_counts=True)
    else:
        g = np.concatenate([[0], np.cumsum(~same)])
        inv = np.empty(len(F), np.int64)
        inv[o] = g
        cnt = np.bincount(g)
    inv = inv.ravel()
    if (cnt == 1).all():
        return F
    a, b, c = F[:, 0], F[:, 1], F[:, 2]
    sg = np.where(((a < b) & (b < c)) | ((b < c) & (c < a)) | ((c < a) & (a < b)), 1, -1)
    tot = np.bincount(inv, weights=sg).astype(int)
    want = np.abs(tot)
    m = sg == np.sign(tot)[inv]
    idx = np.where(m)[0]
    idx = idx[np.argsort(inv[idx], kind="stable")]
    g = inv[idx]
    first = np.r_[0, np.flatnonzero(g[1:] != g[:-1]) + 1]
    pos = np.arange(len(idx)) - np.repeat(first, np.diff(np.r_[first, len(idx)]))
    keep = np.zeros(len(F), bool)
    keep[idx[pos < want[g]]] = True
    return F[keep]


def write_stl(path: str, vertices: np.ndarray, faces: np.ndarray) -> None:
    """Write a binary STL."""
    tri = vertices[faces].astype(np.float32)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1
    n = (n / ln).astype(np.float32)
    rec = np.zeros(len(faces), dtype=_BIN_DTYPE)
    rec["n"] = n
    rec["v"] = tri
    with open(path, "wb") as f:
        f.write(b"MiniPrint".ljust(80, b" "))
        f.write(np.uint32(len(faces)).tobytes())
        rec.tofile(f)
