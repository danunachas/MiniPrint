"""Virtual slice check: voxelise model + supports exactly as a slicer would see them (union of all shells),
walk the layers from the bed up and report material that has nothing underneath it.

  island    a region that appears in a layer without touching anything of the layer below (prints in mid-air)
  overhang  part of a layer that sticks out further than the allowed angle beyond the layer below
            (only clusters bigger than `min_area` are reported; they are what the supports failed to reach)

Inside/outside is decided with the winding number along vertical lines (a down-facing face enters material,
an up-facing face leaves it), so overlapping shells - sculpts, supports touching the model - are handled.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


def _subdivide(T, max_cells, pitch):
    """Split triangles (n,3,3) until their xy bounding box is at most max_cells wide."""
    out = []
    while len(T):
        ext = (T[:, :, :2].max(1) - T[:, :, :2].min(1)).max(1) / pitch
        big = ext > max_cells
        out.append(T[~big])
        B = T[big]
        if not len(B):
            break
        a, b, c = B[:, 0], B[:, 1], B[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        T = np.concatenate([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1),
                            np.stack([ca, bc, c], 1), np.stack([ab, bc, ca], 1)])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def voxelize(meshes, pitch=0.1, layer=0.2, max_cells=7):
    """meshes: list of (V, F) with outward oriented closed shells. Returns (occ[ix,iy,k], origin_xy, nlayers)."""
    allv = np.vstack([m[0] for m in meshes])
    lo = allv.min(0)[:2] - 2 * pitch
    hi = allv.max(0)[:2] + 2 * pitch
    nx, ny = (np.ceil((hi - lo) / pitch).astype(int) + 1)
    nz = int(np.ceil(allv[:, 2].max() / layer)) + 1
    jit = np.array([0.3183, 0.2718]) * pitch * 0.01      # keep sample points off exact vertex/edge positions
    ev_cell, ev_z, ev_s = [], [], []
    for V, F in meshes:
        if not len(F):
            continue
        T = V[F]
        # drop faces that are vertical (no height crossing)
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        keep = np.abs(n[:, 2]) > 1e-12
        T = _subdivide(T[keep], max_cells, pitch)
        if not len(T):
            continue
        a, b, c = T[:, 0], T[:, 1], T[:, 2]
        nn = np.cross(b - a, c - a)
        nzv = nn[:, 2]
        xy = T[:, :, :2] - lo
        i0 = np.floor(xy.min(1) / pitch).astype(int)
        i1 = np.ceil(xy.max(1) / pitch).astype(int)
        w = (i1 - i0).max(0) + 1
        det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        for dx in range(int(w[0]) + 1):
            for dy in range(int(w[1]) + 1):
                ix, iy = i0[:, 0] + dx, i0[:, 1] + dy
                ok = (ix <= i1[:, 0]) & (iy <= i1[:, 1])
                if not ok.any():
                    continue
                px = lo[0] + ix * pitch + jit[0]
                py = lo[1] + iy * pitch + jit[1]
                u = ((px - a[:, 0]) * (c[:, 1] - a[:, 1]) - (py - a[:, 1]) * (c[:, 0] - a[:, 0])) / det
                v = ((b[:, 0] - a[:, 0]) * (py - a[:, 1]) - (b[:, 1] - a[:, 1]) * (px - a[:, 0])) / det
                ins = ok & (u >= 0) & (v >= 0) & (u + v <= 1)
                if not ins.any():
                    continue
                k = np.where(ins)[0]
                z = a[k, 2] + u[k] * (b[k, 2] - a[k, 2]) + v[k] * (c[k, 2] - a[k, 2])
                good = (ix[k] >= 0) & (ix[k] < nx) & (iy[k] >= 0) & (iy[k] < ny)
                k, z = k[good], z[good]
                ev_cell.append(ix[k] * ny + iy[k])
                ev_z.append(z)
                ev_s.append(np.where(nzv[k] < 0, 1, -1))
    if not ev_cell:
        return np.zeros((nx, ny, nz), bool), lo, nz
    cell = np.concatenate(ev_cell)
    z = np.concatenate(ev_z)
    s = np.concatenate(ev_s).astype(np.int32)
    order = np.lexsort((z, cell))
    cell, z, s = cell[order], z[order], s[order]
    # cumulative winding inside every column
    cs = np.cumsum(s)
    start = np.r_[True, cell[1:] != cell[:-1]]
    first = np.maximum.accumulate(np.where(start, np.arange(len(cs)), 0))
    base = (cs - s)[first]
    w_after = cs - base                       # winding number just above each event
    w_before = w_after - s
    enter = (w_before <= 0) & (w_after > 0)
    leave = (w_before > 0) & (w_after <= 0)
    # pair enters and leaves inside each column (they alternate)
    diff = np.zeros((nx * ny, nz + 1), np.int16)
    ze = z[enter]
    ce = cell[enter]
    zl = z[leave]
    cl = cell[leave]
    # layer k has its centre at (k + 0.5) * layer; material covers it when enter <= centre < leave
    ke = np.clip(np.ceil(ze / layer - 0.5).astype(int), 0, nz)
    kl = np.clip(np.ceil(zl / layer - 0.5).astype(int), 0, nz)
    np.add.at(diff, (ce, ke), 1)       # (an unclosed shell simply stays solid up to the top)
    np.add.at(diff, (cl, kl), -1)
    occ = np.cumsum(diff, axis=1)[:, :nz] > 0
    return occ.reshape(nx, ny, nz), lo, nz


def slice_check(model, supports, pitch=0.1, layer=0.2, overhang_angle=45.0, min_area=0.3, max_span=2.2,
                minor_area=0.1, log=print):
    """model, supports: (V, F). Returns dict(ok, islands, overhangs, layers)."""
    from .geometry import signed_volume
    meshes = []
    for m in [model] + ([supports] if supports is not None and len(supports[1]) else []):
        V, F = m
        if signed_volume(V, F) < 0:            # inward oriented file: flip
            F = F[:, ::-1]
        meshes.append((V, F))
    zmax = max(m[0][:, 2].max() for m in meshes)
    ext = np.ptp(np.vstack([m[0] for m in meshes])[:, :2], axis=0)
    while (ext / pitch).prod() * (zmax / layer) > 120e6:
        pitch *= 1.25
    log(f"  slice check: {pitch:.2f} mm columns, {layer:.2f} mm layers")
    occ, lo, nz = voxelize(meshes, pitch, layer)
    reach = layer * np.tan(np.radians(overhang_angle)) + 0.5 * pitch
    r = max(1, int(round(reach / pitch)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disc = (xx * xx + yy * yy) <= r * r + 0.5
    cell_area = pitch * pitch
    islands, overhangs = [], []
    for k in range(1, nz):
        cur, prev = occ[:, :, k], occ[:, :, k - 1]
        if not cur.any():
            continue
        lab, n = ndi.label(cur)
        if n == 0:
            continue
        touch = np.unique(lab[cur & ndi.binary_dilation(prev, structure=disc)])
        touched = set(touch.tolist()) - {0}
        z = (k + 0.5) * layer
        direct = np.unique(lab[cur & prev])
        direct = set(direct.tolist()) - {0}
        for i in range(1, n + 1):
            if i not in touched:
                m = lab == i
                a = float(m.sum() * cell_area)
                if a >= 0.02:
                    cx, cy = (np.argwhere(m).mean(0) * pitch + lo)
                    islands.append({"z": round(z, 2), "x": round(float(cx), 2), "y": round(float(cy), 2),
                                    "area": round(a, 3)})
        # overhang pixels: further from the layer below than the printable reach; what matters is how far
        # such a patch has to bridge (distance to the nearest material below), not the patch area alone
        dist = ndi.distance_transform_edt(~prev) * pitch
        bad = cur & (dist > reach)
        if bad.any():
            bl, bn = ndi.label(bad)
            idx = np.arange(1, bn + 1)
            sizes = ndi.sum(bad, bl, index=idx) * cell_area
            spans = ndi.maximum(dist, bl, index=idx)
            for j in np.where((sizes >= min_area) & (spans >= max_span))[0]:
                m = bl == j + 1
                cx, cy = (np.argwhere(m).mean(0) * pitch + lo)
                overhangs.append({"z": round(z, 2), "x": round(float(cx), 2), "y": round(float(cy), 2),
                                  "area": round(float(sizes[j]), 3), "span": round(float(spans[j]), 2)})
    # merge reports that are stacked on top of each other (same spot, consecutive layers)
    def squash(items, dz=0.7, dxy=1.0):
        out = []
        for it in sorted(items, key=lambda d: d["z"]):
            for o in out:
                stacked = abs(o["z"] - it["z"]) <= dz + (o["layers"] - 1) * 0.2
                if stacked and abs(o["x"] - it["x"]) < dxy and abs(o["y"] - it["y"]) < dxy:
                    o["layers"] += 1
                    o["area"] = max(o["area"], it["area"])
                    o["span"] = max(o.get("span", 0), it.get("span", 0))
                    o["z_top"] = it["z"]
                    break
            else:
                out.append({**it, "layers": 1, "z_top": it["z"]})
        return out
    islands, overhangs = squash(islands), squash(overhangs)
    # specks smaller than about one 0.3 mm dot that start for a single sampled layer are sculpt noise (the
    # nozzle bridges them from the neighbouring wall); they are reported separately and do not fail the check
    minor = [it for it in islands if it["area"] < minor_area and it["layers"] == 1]
    islands = [it for it in islands if not (it["area"] < minor_area and it["layers"] == 1)]
    return {"ok": not islands, "islands": islands, "minor_islands": minor, "overhangs": overhangs, "layers": nz,
            "pitch": pitch, "layer": layer, "solid_volume": float(occ.sum() * pitch * pitch * layer)}


def flat_shoulder_area(model, supports, pitch=0.1, layer=0.1, z_min=1.2, min_width=0.5):
    """mm^2 of flat, upward facing ledges ("plateaus") inside the supports: support material with air above
    it that is at least `min_width` wide.  Supports should flow from the tips to the plate - a branch that
    runs into the flat top of a thicker trunk, or a foot that sits like a drum beside a leaning trunk, shows
    up here.  The support raft itself (below `z_min`) and the tips inside the model do not count."""
    union, lo, _ = voxelize([model, supports], pitch, layer)
    only, lo_s, _ = voxelize([supports], pitch, layer)
    ox, oy = (int(round(v)) for v in (lo_s - lo) / pitch)
    sup = np.zeros_like(union)
    nx, ny, nz = only.shape
    sup[ox:ox + nx, oy:oy + ny, :nz] = only
    erode = max(1, int(round(min_width / pitch / 2 - 0.5)))
    cells = 0
    for k in range(int(z_min / layer), union.shape[2] - 1):
        top = sup[:, :, k] & ~union[:, :, k + 1]
        if top.any():
            cells += int(ndi.binary_erosion(top, iterations=erode).sum())
    return cells * pitch * pitch

