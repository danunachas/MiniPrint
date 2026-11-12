"""Support settings and the result of a support run.

All sizes are millimetres of the FINAL print (after the print scale has been applied).  The starting values
come from tested resin-style support settings (PrusaSlicer SLA values scaled by 1.2 for a 0.2 mm FDM nozzle).

Naming: what the GUI calls a "support raft" is a ``pad`` here (the flat disc under a trunk, joined with its
neighbours), and "no bed contact" is ``raft`` (the model floats above one raft that joins all trunks).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class SupportParams:
    # --- what needs support
    overhang_angle: float = 45.0   # deg from a vertical wall; faces steeper than this get support (90 = flat ceiling)
    min_region_area: float = 0.3   # mm^2, ignore overhang patches smaller than this (sculpt texture noise)
    island_radius: float = 0.6     # mm, a local low point must be the lowest within this radius to count as an island
    bed_tolerance: float = 0.15    # mm, anything this close to the bed is considered touching it
    self_support_reach: float = 0.3  # mm, an overhang with a wall/floor this close below prints unsupported (lip)
    # --- spacing
    spacing: float = 2.4           # mm, tip spacing of single pillars (set from tree_spacing when trees are built)
    # --- tip / head
    tip_diameter: float = 0.48     # mm, diameter of the head at the front end (proven: 0.4 * 1.2)
    tip_penetration: float = 0.24  # mm, how far the tip sinks into the model (proven: 0.2 * 1.2)
    head_length: float = 1.2       # mm, length of the cone from tip to full pillar width
    max_tilt: float = 25.0         # deg, how far from vertical a head may lean (it has to be printable too)
    # --- pillar
    pillar_diameter: float = 1.2   # mm (proven: 1.0 * 1.2)
    clearance: float = 0.3         # mm, pillars keep this distance from the model
    land_on_model: bool = False    # last resort: allow pillars to end on an upward facing part of the model
    land_min_normal_z: float = 0.5 # landing surface must face up at least this much (0.5 = 60 deg slope max)
    # --- foot / pad on the bed
    base_diameter: float = 4.8     # mm, foot at the bottom of a bed pillar (proven: 4 * 1.2)
    base_height: float = 1.2       # mm, height of the foot cone (proven: 1 * 1.2)
    pad_thickness: float = 0.6     # mm, flat disc under each foot, 0 disables (proven: 0.5 * 1.2)
    pad_brim: float = 3.6          # mm, pad disc extends this far beyond the foot (proven: 3 * 1.2)
    pad_merge: float = 3.6         # mm, pads closer than this are joined into one plate (proven: 3 * 1.2); 0 = off
    max_slenderness: float = 16.0  # pillar length / diameter; tall pillars get a thicker, tapered trunk
    # --- tree mode (default): branches merge into trunks that all stand on the bed
    mode: str = "tree"             # "tree", or "classic" = one straight pillar per tip (kept for tests and scripts)
    tree_spacing: float = 1.3      # mm, minimum distance between tips in tree mode (more tips, each on a thin branch)
    branch_diameter: float = 0.8   # mm, thinnest branch right behind a tip
    taper_angle: float = 4.0       # deg, cone half-angle: a branch widens by tan(angle) per mm it descends
    trunk_max_diameter: float = 10.0 # mm, thickest trunk (tall trunks grow towards this by the cone angle)
    branch_angle: float = 40.0     # deg, how far from vertical a branch may lean while it travels sideways
    link_range: float = 12.0       # mm, branches lean towards their nearest neighbour within this range to merge
    bracing: bool = True           # slanted cross-braces between neighbouring trunks
    brace_diameter: float = 0.6    # mm
    brace_spacing: float = 6.0     # mm, vertical distance between brace levels
    brace_range: float = 12.0      # mm, only trunks this close are tied together
    # --- raft (no model contact with the bed): model floats `elevation` above a plate joined from all the feet
    raft: bool = False
    raft_elevation: float = 3.0    # mm, gap between plate and the lowest point of the model
    raft_thickness: float = 0.8    # mm, plate under all the feet (replaces pad_thickness)
    raft_link: float = 12.0        # mm, feet this close (edge to edge) are joined into the plate
    # --- flat faces: a part with a big flat face on the plate stands on it (saves supports and print time),
    #     also in raft mode
    flat_on_plate: bool = True
    flat_min_area: float = 40.0    # mm^2 of flat face on the plate that counts as a base
    # --- misc
    segments: int = 12
    seed: int = 1

    def as_dict(self):
        return asdict(self)


@dataclass
class SupportResult:
    vertices: np.ndarray
    faces: np.ndarray
    points: np.ndarray            # contact points that got a support
    kinds: list                   # "bed" | "model" per supported point
    failed: np.ndarray            # contact points we could not support
    stats: dict = field(default_factory=dict)
    candidates: np.ndarray = None
    fail_info: list = field(default_factory=list)
    pillar_lengths: np.ndarray = None
    bases: np.ndarray = None          # x, y, pad radius of every pillar standing on the bed
    skipped: list = field(default_factory=list)   # (point, reason) of candidates deliberately left without a tip
