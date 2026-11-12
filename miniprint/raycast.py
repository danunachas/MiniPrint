"""Vertical ray casting against a triangle mesh using a 2D (XY) grid. Pure numpy."""
from __future__ import annotations

import numpy as np


class DownRaycaster:
    """Answers: 'looking straight down from (x, y, z), what is the first surface I hit?'"""

    def __init__(self, V: np.ndarray, F: np.ndarray, cell: float = 0.5):
        self.T = V[F]
        self.n_tri = len(F)
        T = self.T
        mn = T.min(1)[:, :2]
        mx = T.max(1)[:, :2]
        self.origin = mn.min(0) - 1e-6
        self.cell = cell
        i0 = np.floor((mn - self.origin) / cell).astype(np.int64)
        i1 = np.floor((mx - self.origin) / cell).astype(np.int64)
        self.nx = int(i1[:, 0].max()) + 1
        self.ny = int(i1[:, 1].max()) + 1
        wx = i1[:, 0] - i0[:, 0] + 1
        wy = i1[:, 1] - i0[:, 1] + 1
        cnt = wx * wy
        tri_ids = np.repeat(np.arange(self.n_tri), cnt)
        start = np.cumsum(cnt) - cnt
        local = np.arange(int(cnt.sum())) - np.repeat(start, cnt)
        wxr = np.repeat(wx, cnt)
        cx = np.repeat(i0[:, 0], cnt) + local % wxr
        cy = np.repeat(i0[:, 1], cnt) + local // wxr
        cell_id = cx * self.ny + cy
        order = np.argsort(cell_id, kind="stable")
        self.cell_tris = tri_ids[order]
        counts = np.bincount(cell_id, minlength=self.nx * self.ny)
        self.cell_start = np.concatenate([[0], np.cumsum(counts)])

    def first_hit_below(self, pts: np.ndarray, eps: float = 1e-6, chunk: int = 1500):
        """For each point return (z_hit, tri_index). z_hit = -inf and tri = -1 if nothing is below."""
        pts = np.atleast_2d(np.asarray(pts, float))
        n = len(pts)
        z_out = np.full(n, -np.inf)
        t_out = np.full(n, -1, dtype=np.int64)
        for s in range(0, n, chunk):
            p = pts[s : s + chunk]
            m = len(p)
            cx = np.floor((p[:, 0] - self.origin[0]) / self.cell).astype(np.int64)
            cy = np.floor((p[:, 1] - self.origin[1]) / self.cell).astype(np.int64)
            ok = (cx >= 0) & (cx < self.nx) & (cy >= 0) & (cy < self.ny)
            cid = np.where(ok, cx * self.ny + cy, 0)
            st = self.cell_start[cid]
            c = np.where(ok, self.cell_start[cid + 1] - st, 0)
            total = int(c.sum())
            if total == 0:
                continue
            qi = np.repeat(np.arange(m), c)
            local = np.arange(total) - np.repeat(np.cumsum(c) - c, c)
            ti = self.cell_tris[np.repeat(st, c) + local]
            A, B, C = self.T[ti, 0], self.T[ti, 1], self.T[ti, 2]
            px, py = p[qi, 0], p[qi, 1]
            d = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
            good = np.abs(d) > 1e-14
            d = np.where(good, d, 1.0)
            l1 = ((B[:, 1] - C[:, 1]) * (px - C[:, 0]) + (C[:, 0] - B[:, 0]) * (py - C[:, 1])) / d
            l2 = ((C[:, 1] - A[:, 1]) * (px - C[:, 0]) + (A[:, 0] - C[:, 0]) * (py - C[:, 1])) / d
            l3 = 1.0 - l1 - l2
            tol = -1e-9
            inside = good & (l1 >= tol) & (l2 >= tol) & (l3 >= tol)
            z = l1 * A[:, 2] + l2 * B[:, 2] + l3 * C[:, 2]
            valid = inside & (z < p[qi, 2] - eps)
            if not valid.any():
                continue
            zb = np.full(m, -np.inf)
            np.maximum.at(zb, qi[valid], z[valid])
            best = valid & (z >= zb[qi] - 1e-12)
            tb = np.full(m, -1, dtype=np.int64)
            tb[qi[best]] = ti[best]
            z_out[s : s + m] = zb
            t_out[s : s + m] = tb
        return z_out, t_out
