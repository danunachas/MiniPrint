"""Support generation: where tips go, trees, support rafts, no bed contact, the slice check."""

import numpy as np

from miniprint.geometry import box, frustum, merge, signed_volume
from miniprint.supports import SupportParams, build_supports


def mushroom():
    """A floating plate (flat overhang) with a low block under one side (a landing spot) and a separate
    downward pointing spike (an island).  No overlapping shells."""
    plate = box([-8, -8, 5], [8, 8, 7])
    block = box([-8, -2, 0], [-4, 2, 2])
    spike = frustum([12, 0, 3], 0.05, [12, 0, 8], 1.5, 24)   # narrow tip at the bottom
    return merge([plate, block, spike])


def test_supports_mushroom():
    V, F = mushroom()
    p = SupportParams(mode="classic", land_on_model=True)
    res = build_supports(V, F, p, log=lambda *a: None)
    st = res.stats
    assert st["failed"] == 0, st
    assert st["supported"] >= 6, st
    assert st["island_points"] >= 1, st          # the spike tip
    assert st["to_bed"] >= 4 and st["on_model"] >= 1, st
    sv = res.vertices
    # no support geometry inside the block (it may only touch the top surface by the tip penetration)
    # (the pad layer, z < pad thickness, deliberately overlaps the model's footprint to join with it)
    inside = (sv[:, 0] > -8) & (sv[:, 0] < -4) & (np.abs(sv[:, 1]) < 2) & (sv[:, 2] > p.pad_thickness + 0.05) & (sv[:, 2] < 2 - p.tip_penetration - 0.01)
    assert not inside.any()
    assert sv[:, 2].min() < 0.01                  # reaches the bed
    assert sv[:, 2].min() > -1e-6                 # never dips below the bed


def test_supports_near_bed_stay_above_bed():
    """a plate floating only 0.7-1.5 mm above the bed: short pillars must not poke through z=0"""
    for h in (0.7, 0.9, 1.2, 1.5):
        V, F = box([-6, -6, h], [6, 6, h + 1.5])
        res = build_supports(V, F, SupportParams(), log=lambda *a: None)
        if len(res.vertices):
            assert res.vertices[:, 2].min() > -1e-6, (h, res.vertices[:, 2].min())


def test_pads_merge_and_prisms_are_valid():
    from miniprint.rafts import prism
    V, F = prism(np.array([[0, 0], [2, 0], [2, 1], [0, 1.0]]), 0, 0.6)
    assert abs(signed_volume(V, F) - 1.2) < 1e-9
    V, F = prism(np.array([[0, 0], [2, 0], [1, 1.5]]), 0, 1)
    assert abs(signed_volume(V, F) - 1.5) < 1e-9
    # two floating plates 9 mm apart: with merge on, supports contain linking pads
    Va, Fa = box([-6, -6, 5], [-2, -2, 6.5])
    Vb, Fb = box([2, 2, 5], [6, 6, 6.5])
    Vm, Fm = merge([(Va, Fa), (Vb, Fb)])
    on = build_supports(Vm, Fm, SupportParams(pad_merge=3.6), log=lambda *a: None)
    off = build_supports(Vm, Fm, SupportParams(pad_merge=0), log=lambda *a: None)
    assert on.stats["pad_links"] >= 1 and off.stats["pad_links"] == 0
    assert len(on.faces) > len(off.faces)
    assert on.vertices[:, 2].min() > -1e-6


def test_tall_pillars_are_tapered():
    V, F = box([-3, -3, 40], [3, 3, 42])
    res = build_supports(V, F, SupportParams(pad_merge=0, pad_brim=0, mode="classic"), log=lambda *a: None)
    assert res.stats["failed"] == 0 and res.stats["supported"] >= 1
    sv = res.vertices
    ax = res.points[0, :2]                      # contact point; the pillar below it is vertical here
    def radius(z0, z1):
        band = sv[(sv[:, 2] > z0) & (sv[:, 2] < z1)]
        dist = np.linalg.norm(band[:, :2] - ax, axis=1)
        return dist[dist < 2.0].max()
    assert radius(1.7, 1.9) > radius(38.5, 39.0) + 0.3  # rings exist only at the ends of each cone: foot top vs head end


def test_manual_tips_and_keep_clear():
    V, F = box([-6, -6, 3], [6, 6, 4.5])          # floating plate: needs supports on its underside
    p = SupportParams()
    base = build_supports(V, F, p, log=lambda *a: None)
    assert base.stats["supported"] >= 2
    # keep-clear zone covering the whole underside removes every automatic tip
    none = build_supports(V, F, p, log=lambda *a: None, manual_erase=[((0, 0, 3), 20.0)])
    assert none.stats["contact_points"] == 0 and len(none.vertices) == 0
    # a hand-placed tip is always attempted and reaches the bed
    one = build_supports(V, F, p, log=lambda *a: None, manual_erase=[((0, 0, 3), 20.0)],
                         manual_add=[((1.0, 1.0, 3.0), (0, 0, -1))])
    assert one.stats["supported"] == 1 and one.stats["failed"] == 0, one.stats
    assert one.vertices[:, 2].min() > -1e-6


def test_tree_supports_merge_and_reach_the_bed():
    from miniprint.geometry import merge as gmerge
    V1, F1 = box([-3, -3, 0], [3, 3, 22])
    V2, F2 = box([-10, -10, 20], [10, 10, 22])
    V, F = gmerge([(V1, F1), (V2, F2)])
    res = build_supports(V, F, SupportParams(), log=lambda *a: None)
    st = res.stats
    assert st["failed"] == 0 and st["on_model"] == 0, st
    assert st["supported"] > 20 and 1 <= st["trunks"] < st["supported"] / 2, st    # many tips, few trunks
    sv = res.vertices
    assert sv[:, 2].min() > -1e-6
    # nothing of the supports may sit inside the post or the plate (tips may sink about 0.3 into the plate)
    in_post = (np.abs(sv[:, 0]) < 2.7) & (np.abs(sv[:, 1]) < 2.7) & (sv[:, 2] > 0.7)
    in_plate = (np.abs(sv[:, 0]) < 9.7) & (np.abs(sv[:, 1]) < 9.7) & (sv[:, 2] > 20.45) & (sv[:, 2] < 21.7)
    assert not in_post.any() and not in_plate.any()
    # classic mode still works
    old = build_supports(V, F, SupportParams(mode="classic", land_on_model=True), log=lambda *a: None)
    assert old.stats["supported"] > 10


def test_raft_mode_floats_the_model_on_one_plate():
    V, F = box([-8, -8, 3], [8, 8, 6])             # lifted 3 mm, nothing touches the bed
    res = build_supports(V, F, SupportParams(raft=True), log=lambda *a: None)
    st = res.stats
    assert st["failed"] == 0 and st["supported"] > 10, st
    sv = res.vertices
    assert sv[:, 2].min() > -1e-6 and sv[:, 2].min() < 1e-6
    from miniprint.slicecheck import slice_check
    chk = slice_check((V, F), (sv, res.faces), log=lambda *a: None)
    assert chk["ok"], chk["islands"]               # plate and every foot are connected up to the model


def test_slice_check_finds_floating_material_and_accepts_supported():
    from miniprint.geometry import merge as gmerge
    from miniprint.slicecheck import slice_check
    # volume bookkeeping of the voxeliser: post + plate (overlap counted once)
    V1, F1 = box([-3, -3, 0], [3, 3, 22])
    V2, F2 = box([-10, -10, 20], [10, 10, 22])
    V, F = gmerge([(V1, F1), (V2, F2)])
    r = slice_check((V, F), None, log=lambda *a: None)
    assert abs(r["solid_volume"] - (36 * 22 + 400 * 2 - 36 * 2)) < 40, r["solid_volume"]
    assert r["overhangs"] and r["overhangs"][0]["span"] > 5          # a 10 mm ceiling with nothing below
    # a block hovering above the bed is an island
    Vb, Fb = box([0, 0, 5], [4, 4, 6])
    rb = slice_check((Vb, Fb), None, log=lambda *a: None)
    assert rb["islands"]
    # with generated supports the ceiling only bridges between tips
    res = build_supports(V, F, SupportParams(), log=lambda *a: None)
    rs = slice_check((V, F), (res.vertices, res.faces), log=lambda *a: None)
    assert not rs["islands"] and not rs["overhangs"], (rs["islands"][:2], rs["overhangs"][:2])


def test_anchor_cube_for_floating_parts():
    from miniprint.anchor import CUBE_HEIGHT, anchor_cube
    # a floating plate on two pads: the cube goes under it, inside one of the pads (merges with it when sliced)
    V, F = box([0, 0, 3], [30, 12, 5])
    pads = merge([frustum([4, 6, 0], 3, [4, 6, 0.6], 3, 24), frustum([26, 6, 0], 3, [26, 6, 0.6], 3, 24)])
    cube, where = anchor_cube((V, F), pads)
    assert cube is not None and where.startswith("under the model, inside"), where
    C = cube[0]
    assert abs(C[:, 2].min()) < 1e-9 and abs(C[:, 2].max() - CUBE_HEIGHT) < 1e-9
    assert abs(signed_volume(*cube) - 2 * 2 * CUBE_HEIGHT) < 1e-9
    corners = C[:, :2]
    assert all(min(np.hypot(x - 4, y - 6), np.hypot(x - 26, y - 6)) < 3 for x, y in corners)   # all on a pad
    # a raft under everything: under the middle of the model, inside the raft
    raft = box([-2, -2, 0], [32, 14, 0.8])
    cube, where = anchor_cube((V, F), raft)
    c = cube[0].mean(0)
    assert where.startswith("under the model, inside") and abs(c[0] - 15) < 0.5 and abs(c[1] - 6) < 0.5, (where, c)
    # no support material under the model: still directly under it
    cube, where = anchor_cube((V, F), (None, []))
    c = cube[0].mean(0)
    assert where == "under the model" and 1 < c[0] < 29 and 1 < c[1] < 11
    # a model standing on the plate needs no anchor
    assert anchor_cube(box([0, 0, 0], [5, 5, 5]), pads)[0] is None


def test_flat_face_goes_on_the_plate_even_with_raft():
    from miniprint.orient import OrientParams, choose_orientation
    from miniprint.supports import flat_base_area, plate_params
    # a "shield": a flat disc (back) with a boss on its front, given standing on its edge
    disc = frustum([0, 0, 0], 20, [0, 0, 3], 20, 48)
    dome = frustum([0, 0, 2.9], 12, [0, 0, 10], 4, 32)
    V, F = merge([disc, dome])
    V = V @ np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.0]]).T      # standing up (flat back facing -y)
    V = V - [0, 0, V[:, 2].min()]
    sp = SupportParams(raft=True)
    fin, _ = choose_orientation(V, F, OrientParams(top_k=4, n_dirs=200, eval_tris=20000), sp, log=lambda *a: None)
    R = fin[0]["R"]
    up = R.T @ np.array([0, 0, 1.0])
    assert up[1] < -0.95, up                                         # flat back (normal +y) down
    P = V @ R.T
    P = P - [0, 0, P[:, 2].min()]
    assert flat_base_area(P, F) > 1000
    p2, a = plate_params(P, F, sp)
    assert not p2.raft and a > 1000                                  # stands on the plate instead of floating
    p3, _ = plate_params(P, F, SupportParams(raft=True, flat_on_plate=False))
    assert p3.raft


def test_supports_flow_without_flat_shoulders():
    # branches widen into the trunks they join and trunks into their feet: no flat ledges inside the supports
    from miniprint.slicecheck import flat_shoulder_area
    V, F = box([0, 0, 14], [26, 18, 16])                     # a plate floating 14 mm above the bed
    res = build_supports(V, F, SupportParams(), log=lambda *a: None)
    assert res.stats["supported"] > 100 and not res.stats["failed"]
    assert res.stats["trunks"] < res.stats["supported"] / 4   # the tips really merge into trunks
    assert flat_shoulder_area((V, F), (res.vertices, res.faces)) < 0.3
