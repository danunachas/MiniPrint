"""Single straight supports: a tilted head on the contact point, then a vertical pillar.

Tree supports (tree.py) carry almost every tip.  A tip that no branch can reach still gets one of these:

  * head + pillar down to the plate (tapered when tall, with a foot and a support raft),
  * a short post straight down for an overhang just above the plate (``stub``),
  * with "on build plate only" switched off: a pillar or a double-tipped pin that stands on the model.

``mode="classic"`` in SupportParams builds every support this way.
"""
from __future__ import annotations

import numpy as np

from .geometry import frustum, sphere
from .overhangs import candidate_dirs


class PillarBuilder:
    def __init__(self, p, kd, ray, face_normals):
        self.p, self.kd, self.ray, self.fn = p, kd, ray, face_normals
        self.r_tip = p.tip_diameter / 2
        self.r_pillar = p.pillar_diameter / 2
        self.pad_meshes = []     # support raft discs, kept apart so their volume is not counted twice
        self.bases = []          # (x, y, raft radius, raft thickness) of every pillar that stands on the plate
        self.lengths = []

    def support(self, pt, n, why):
        """Best single support for contact point pt with surface normal n: (mesh parts, kind) or None.
        ``why`` collects the reasons attempts failed (for the report)."""
        p = self.p
        for d in candidate_dirs(n, p.max_tilt):
            for part in (1.0, 0.7, 0.45):                       # shorter heads where the full one does not fit
                length = p.head_length * part
                if d[2] < -1e-6 and pt[2] + d[2] * length < 0.2:
                    length = max(0.2, (pt[2] - 0.2) / -d[2])
                result = self.head_pillar(pt, n, d, length, why)
                if result:
                    return result
        if pt[2] < 2.5:
            result = self.stub(pt, why)
            if result:
                return result
        if p.land_on_model:
            return self.strut(pt, why)
        return None

    def head_pillar(self, pt, n, d, length, why):
        """Tilted head, then a vertical pillar to the plate or onto the model."""
        p, kd, r_t, r_p, pen = self.p, self.kd, self.r_tip, self.r_pillar, self.p.tip_penetration
        e = pt + d * length
        if e[2] < 0.1:
            why["too_low"] = why.get("too_low", 0) + 1
            return None
        dn = float(d @ n)
        s_ = np.array([0.5, 1.0]) * length
        dist, _ = kd.query(pt[None, :] + s_[:, None] * d[None, :])
        # the head starts ON the model: demand clearance from the contact surface only in
        # proportion to how far we are from it (s * d.n); everything else must clear the cone.
        need_mid = min(r_t + (r_p - r_t) * (pen + s_[0]) / (pen + length) + 0.1, 0.5 * s_[0] * dn)
        need_end = min(r_p + 0.15, 0.8 * s_[1] * dn)
        if dist[0] < need_mid or dist[1] < need_end:
            why["head_blocked"] = why.get("head_blocked", 0) + 1
            return None
        zh, th = self.ray.first_hit_below(e[None, :], eps=0.0)
        zh, th = float(zh[0]), int(th[0])
        if zh > p.bed_tolerance:
            if not p.land_on_model:
                why["no_land"] = why.get("no_land", 0) + 1
                return None
            if self.fn[th, 2] < p.land_min_normal_z:
                why["land_too_steep"] = why.get("land_too_steep", 0) + 1
                return None
            kind, z_end, zone = "model", zh, zh + p.head_length + 0.2
        else:
            kind, z_end, zone = "bed", 0.0, p.pad_thickness + p.base_height + 0.2
        if e[2] - z_end < 0.25:
            why["too_short"] = why.get("too_short", 0) + 1
            return None
        # tall pillars are tapered: thin at the head, thicker towards the plate so they can stand
        r_bot = r_p
        if kind == "bed":
            r_bot = float(np.clip(e[2] / (2.0 * p.max_slenderness), r_p, p.base_diameter / 2 - 0.3))
        zs = np.arange(e[2] - 0.4, zone, -0.4)
        if len(zs):
            dc, _ = kd.query(np.column_stack([np.full(len(zs), e[0]), np.full(len(zs), e[1]), zs]))
            rz = r_p + (r_bot - r_p) * np.clip((e[2] - zs) / max(e[2] - zone, 1e-6), 0.0, 1.0)
            if np.any(dc < rz + p.clearance):
                why["pillar_blocked"] = why.get("pillar_blocked", 0) + 1
                return None
        x, y = e[0], e[1]
        parts = [frustum(pt - d * pen, r_t, e, r_p, p.segments)]
        if e[2] - r_p > 0.02:
            parts.append(sphere(e, r_p, 4, 8))   # smooth joint (skipped if it would dip below the plate)
        if kind == "bed":
            pad_t = min(p.pad_thickness, max(0.0, e[2] - 0.3))   # the raft can't be taller than the pillar allows
            foot_h = min(p.base_height, max(0.0, e[2] - pad_t - 0.3))
            df, _ = kd.query(np.array([[x, y, 0.3], [x, y, pad_t + foot_h * 0.5 + 0.1]]))
            r_foot = float(np.clip(df.min() - 0.15, r_bot, p.base_diameter / 2))
            z0 = 0.0
            if pad_t > 0.05:
                r_pad = r_foot + p.pad_brim
                self.pad_meshes.append(frustum([x, y, 0], r_pad, [x, y, pad_t], r_pad, p.segments))
                self.bases.append((float(x), float(y), r_pad, pad_t))
                z0 = pad_t
            if foot_h > 0.05:
                parts.append(frustum([x, y, z0], r_foot, [x, y, z0 + foot_h], r_bot, p.segments))
                z0 += foot_h
            if e[2] - z0 > 0.02:
                parts.append(frustum([x, y, z0], r_bot, e, r_p, p.segments))
        else:
            tip_top = min(e[2], z_end + p.head_length)
            parts.append(frustum([x, y, z_end - pen], r_t, [x, y, tip_top], r_p, p.segments))
            if tip_top < e[2] - 1e-6:
                parts.append(frustum([x, y, tip_top], r_p, e, r_p, p.segments))
        self.lengths.append(float(e[2] - z_end))
        return parts, kind

    def strut(self, pt, why):
        """Small gap between this surface and an upward facing surface right below: a short vertical
        double-tipped pin (tip on the model above, tip on the model below)."""
        p, r_t, pen = self.p, self.r_tip, self.p.tip_penetration
        zh, th = self.ray.first_hit_below((pt + np.array([0, 0, -1e-3]))[None, :], eps=0.0)
        zh, th = float(zh[0]), int(th[0])
        if th < 0 or zh <= p.bed_tolerance:
            return None
        if self.fn[th, 2] < p.land_min_normal_z:
            why["strut_land_steep"] = why.get("strut_land_steep", 0) + 1
            return None
        h = pt[2] - zh
        if h < 1.4:
            why["gap_too_small"] = why.get("gap_too_small", 0) + 1
            return None
        hl = min(p.head_length, 0.4 * h)
        # clearance is measured along the neck only (between the two cones), since the cones
        # necessarily touch the surfaces they connect.
        zs = np.linspace(zh + hl, pt[2] - hl, max(2, int((h - 2 * hl) / 0.4) + 1))
        dc, _ = self.kd.query(np.column_stack([np.full(len(zs), pt[0]), np.full(len(zs), pt[1]), zs]))
        r_mid = min(self.r_pillar, 0.8 * float(dc.min()) - 0.05)
        if r_mid < r_t:
            why["strut_tight"] = why.get("strut_tight", 0) + 1
            return None
        x, y = pt[0], pt[1]
        parts = [
            frustum([x, y, pt[2] + pen], r_t, [x, y, pt[2] - hl], r_mid, p.segments),
            frustum([x, y, pt[2] - hl], r_mid, [x, y, zh + hl], r_mid, p.segments),
            frustum([x, y, zh + hl], r_mid, [x, y, zh - pen], r_t, p.segments),
        ]
        self.lengths.append(float(h))
        return parts, "strut"

    def stub(self, pt, why):
        """Low overhang just above the plate (no room for a head + foot): a short tapered post straight down."""
        r_t = self.r_tip
        x, y, z = pt
        r_b = min(0.8, max(r_t + 0.05, z * 0.5))
        zs = np.linspace(0.05, max(0.06, z - 0.35), 4)
        d, _ = self.kd.query(np.column_stack([np.full(len(zs), x), np.full(len(zs), y), zs]))
        if np.any(d < np.linspace(r_b, r_t, len(zs)) * 0.9):
            why["stub_blocked"] = why.get("stub_blocked", 0) + 1
            return None
        self.lengths.append(float(z))
        return [frustum([x, y, 0.0], r_b, [x, y, z + self.p.tip_penetration], r_t, self.p.segments)], "bed"
