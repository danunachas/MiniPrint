"""Orientation search."""

import numpy as np

from miniprint.geometry import box
from miniprint.supports import SupportParams


def test_orientation_helpers():
    from miniprint.orient import base_margin, fibonacci_sphere, rotation_to_up
    rng = np.random.default_rng(3)
    for _ in range(20):
        u = rng.normal(size=3)
        u /= np.linalg.norm(u)
        R = rotation_to_up(u)
        assert np.allclose(R @ u, [0, 0, 1], atol=1e-9)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
    assert np.allclose(np.linalg.norm(fibonacci_sphere(200), axis=1), 1)
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10.0]])
    assert abs(base_margin(np.array([5, 5.0]), sq) - 5) < 1e-9       # centre: 5 mm from every edge
    assert base_margin(np.array([14, 5.0]), sq) < 0                     # outside the base


def test_orientation_search_lays_a_plain_plate_flat():
    from miniprint.orient import OrientParams, choose_orientation
    V, F = box([0, 0, 0], [30, 30, 3])
    # start tilted so the search has real work to do
    th = np.radians(35)
    Rx = np.array([[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]])
    V = V @ Rx.T
    out, _ = choose_orientation(V, F, OrientParams(n_dirs=200, top_k=3, samples=5000), SupportParams(), log=lambda *a: None)
    best = out[0]
    P = best["P"]
    assert np.ptp(P[:, 2]) < 4.0, f"a 30x30x3 plate should end up lying flat, got height {np.ptp(P[:, 2]):.1f}"
