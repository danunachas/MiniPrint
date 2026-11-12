"""Tiny dependency-light preview renderer (needs Pillow). Orthographic, z-buffer by splatting."""
from __future__ import annotations

import numpy as np

try:
    from PIL import Image
except ImportError:  # preview is optional
    Image = None


def _split_long(T, colr, maxlen):
    """Bisect the longest edge (measured in XY of T) until every edge is <= maxlen."""
    outT, outC = [], []
    while len(T):
        e = np.stack([np.linalg.norm(T[:, b, :2] - T[:, a, :2], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))], 1)
        big = e.max(1) > maxlen
        outT.append(T[~big])
        outC.append(colr[~big])
        T, colr, e = T[big], colr[big], e[big]
        if not len(T):
            break
        k = e.argmax(1)                                   # edge k goes from vertex k to vertex (k+1)%3
        r = np.arange(len(T))
        a, b, c = T[r, k], T[r, (k + 1) % 3], T[r, (k + 2) % 3]
        m = (a + b) / 2
        T = np.concatenate([np.stack([a, m, c], 1), np.stack([m, b, c], 1)])
        colr = np.concatenate([colr, colr])
    return np.concatenate(outT), np.concatenate(outC)


def _splat(S, colr, img, zbuf):
    """S: triangles in screen space (x px, y px, depth). Fill img where the depth is the nearest so far."""
    H, W = zbuf.shape
    S, colr = _split_long(S, colr, 8.0)              # small triangles: the sample count follows the area
    edges = np.stack([S[:, 1, :2] - S[:, 0, :2], S[:, 2, :2] - S[:, 1, :2], S[:, 0, :2] - S[:, 2, :2]], 1)
    k = np.clip(np.ceil(np.linalg.norm(edges, axis=2).max(1) / 0.9).astype(int), 1, 12)
    for kk in np.unique(k):
        idx = np.where(k == kk)[0]
        bary = [(i / kk, j / kk) for i in range(kk + 1) for j in range(kk + 1 - i)] if kk > 1 else [(1 / 3, 1 / 3)]
        bary = np.array(bary)
        w0 = 1 - bary[:, 0] - bary[:, 1]
        step = max(1, 2_000_000 // len(bary))
        for lo in range(0, len(idx), step):
            sub = idx[lo:lo + step]
            t = S[sub]
            pts = w0[None, :, None] * t[:, 0:1] + bary[None, :, 0:1] * t[:, 1:2] + bary[None, :, 1:2] * t[:, 2:3]
            pts = pts.reshape(-1, 3)
            col = np.repeat(colr[sub], len(bary), axis=0)
            pix = np.clip(pts[:, 1].astype(np.int64), 0, H - 1) * W + np.clip(pts[:, 0].astype(np.int64), 0, W - 1)
            d = pts[:, 2]
            zf = zbuf.reshape(-1)
            np.maximum.at(zf, pix, d)                   # nearest depth per pixel (z-buffer)
            win = d >= zf[pix]
            img.reshape(-1, 3)[pix[win]] = col[win].astype(np.uint8)
    return img


def _render(meshes, view, px_per_mm, max_px=1200):
    az, el = np.radians(view[0]), np.radians(view[1])
    fwd = np.array([np.cos(el) * np.sin(az), -np.cos(el) * np.cos(az), np.sin(el)])
    right = np.cross([0, 0, 1], fwd)
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    light = np.array([0.4, -0.5, 0.8])
    light /= np.linalg.norm(light)
    tris, cols = [], []
    for V, F, col in meshes:
        if len(F) == 0:
            continue
        T = V[F]
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        keep = (n @ fwd) > 0.0
        shade = 0.30 + 0.70 * np.clip(n @ (0.6 * fwd + 0.4 * light), 0, 1)
        colr = np.clip(np.array(col)[None, :] * shade[:, None], 0, 255)
        tris.append(T[keep])
        cols.append(colr[keep])
    T = np.concatenate(tris)
    colr = np.concatenate(cols)
    x, y, D = T @ right, T @ up, T @ fwd
    px_per_mm = min(px_per_mm, max_px / max(np.ptp(x), np.ptp(y), 1e-6))    # big parts: keep the image sane
    x0, x1, y0, y1 = x.min() - 2, x.max() + 2, y.min() - 2, y.max() + 2
    W, H = int((x1 - x0) * px_per_mm), int((y1 - y0) * px_per_mm)
    S = np.stack([(x - x0) * px_per_mm, H - 1 - (y - y0) * px_per_mm, D], axis=2)
    img = np.full((H, W, 3), 245, np.uint8)
    zbuf = np.full((H, W), -np.inf)
    _splat(S, colr, img, zbuf)
    return Image.fromarray(img)


def _marker(c, r=0.5):
    c = np.asarray(c, float)
    V = c + r * np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]])
    F = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    return V, F


def save_preview(path, model, supports, failed, px_per_mm=24):
    """model/supports: (V, F). Writes a contact sheet of 4 views. Returns False if Pillow missing."""
    if Image is None:
        return False
    meshes = [(model[0], model[1], (110, 135, 205)), (supports[0], supports[1], (235, 140, 40))]
    for f in failed:
        mv, mf = _marker(f)
        meshes.append((mv, mf, (230, 30, 30)))
    views = [(0, 55), (180, 55), (90, 25), (0, 89)]
    ims = [_render(meshes, v, px_per_mm) for v in views]
    h = max(i.height for i in ims)
    sheet = Image.new("RGB", (sum(i.width for i in ims), h), (245, 245, 245))
    x = 0
    for i in ims:
        sheet.paste(i, (x, 0))
        x += i.width
    sheet.save(path)
    return True


def save_orientation_sheet(path, finalists, px_per_mm=11, chosen=0):
    """One tile per orientation candidate (rank order): model blue, supports orange, failures red."""
    if Image is None or not finalists:
        return False
    try:
        from PIL import ImageDraw
    except ImportError:
        ImageDraw = None
    tiles = []
    for k, r in enumerate(finalists, 1):
        res = r["res"]
        meshes = [(r["P"], r["Fe"], (110, 135, 205)), (res.vertices, res.faces, (235, 140, 40))]
        for f in res.failed:
            mv, mf = _marker(f, 0.9)
            meshes.append((mv, mf, (230, 30, 30)))
        a = _render(meshes, (35, 22), px_per_mm, 700)
        b = _render(meshes, (215, 22), px_per_mm, 700)
        w, h = a.width + b.width, max(a.height, b.height) + 46
        tile = Image.new("RGB", (w, h), (245, 245, 245))
        tile.paste(a, (0, 46))
        tile.paste(b, (a.width, 46))
        if ImageDraw is not None:
            d = ImageDraw.Draw(tile)
            mark = "  <- chosen" if k - 1 == chosen else ""
            d.text((8, 6), f"#{k}{mark}   score {r['total']:.0f}   height {r['height']:.0f} mm   tips {r['tips']}"
                           f"   unsupported {r['failed']}", fill=(20, 20, 20))
            d.text((8, 22), f"surface cost {r['surface']:.0f}   support cost {r['support']:.0f}"
                            f"   grip on plate {r['grip']:.0f} mm2   balance margin {r['margin']:.1f} mm"
                            f"   support volume {r['volume']:.0f} mm3", fill=(60, 60, 60))
        tiles.append(tile)
    cols = 2
    rows = (len(tiles) + cols - 1) // cols
    cw, ch = max(t.width for t in tiles), max(t.height for t in tiles)
    sheet = Image.new("RGB", (cw * cols, ch * rows), (245, 245, 245))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * cw, (i // cols) * ch))
    sheet.save(path)
    return True
