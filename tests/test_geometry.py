"""Mesh primitives and the downward ray caster."""

import numpy as np

from miniprint.geometry import box, frustum, merge, signed_volume, sphere
from miniprint.raycast import DownRaycaster


def test_primitives_are_outward_and_closed():
    for V, F in [frustum([0, 0, 0], 1, [0, 0, 2], 0.5), frustum([0, 0, 0], 0.3, [1, 1, 1], 0.6), sphere([0, 0, 0], 1), box([0, 0, 0], [1, 2, 3])]:
        assert signed_volume(V, F) > 0
        E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
        _, c = np.unique(E, axis=0, return_counts=True)
        assert (c == 2).all(), "mesh not closed"
    V, F = frustum([0, 0, 0], 1, [0, 0, 2], 1, 400)
    assert abs(signed_volume(V, F) - np.pi * 2) < 0.01
    V, F = box([0, 0, 0], [1, 2, 3])
    assert abs(signed_volume(V, F) - 6) < 1e-9


def test_raycast():
    V, F = box([0, 0, 0], [4, 4, 2])
    rc = DownRaycaster(V, F)
    z, _ = rc.first_hit_below(np.array([[2, 2, 5], [9, 9, 5], [2, 2, 1.0]]))
    assert abs(z[0] - 2) < 1e-9 and z[1] == -np.inf and abs(z[2] - 0) < 1e-9


def mushroom():
    """floating plate (flat overhang) with a low block under one side (landing spot) and a
    separate downward pointing spike (island). No overlapping shells."""
    plate = box([-8, -8, 5], [8, 8, 7])
    block = box([-8, -2, 0], [-4, 2, 2])
    spike = frustum([12, 0, 3], 0.05, [12, 0, 8], 1.5, 24)   # narrow tip at the bottom
    return merge([plate, block, spike])
