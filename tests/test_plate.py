"""Arranging parts on build plates."""

import numpy as np


def test_plate_arrangement_packs_without_overlap():
    from miniprint.plate import apply_place, arrange
    rng = np.random.default_rng(3)
    fps = {}
    for k in range(14):
        w, d = rng.uniform(10, 70), rng.uniform(8, 40)
        a = rng.uniform(0, np.pi)
        P = rng.uniform([-w / 2, -d / 2], [w / 2, d / 2], (60, 2))
        c, s_ = np.cos(a), np.sin(a)
        fps[k] = P @ np.array([[c, -s_], [s_, c]]).T + rng.uniform(-100, 100, 2)
    fps["huge"] = np.array([[0, 0], [400, 0], [400, 300], [0, 300.0]])
    place, n, big = arrange(fps, 180, 180, gap=4, margin=5)
    assert big == ["huge"] and n >= 1 and len(place) == 14
    boxes = {}
    for k, pl in place.items():
        V = np.c_[fps[k], np.zeros(len(fps[k]))]
        Q = apply_place(V, pl)[:, :2]
        assert Q.min() >= 5 - 1e-6 and Q[:, 0].max() <= 175 + 1e-6 and Q[:, 1].max() <= 175 + 1e-6
        boxes[k] = (pl["plate"], Q.min(0), Q.max(0))
    ks = list(boxes)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a, b = boxes[ks[i]], boxes[ks[j]]
            if a[0] != b[0]:
                continue
            sep = np.maximum(b[1] - a[2], a[1] - b[2]).max()   # > 0 when the rectangles are apart
            assert sep >= 4 - 1e-2, (ks[i], ks[j], sep)


def test_plate_diagonal_fit_place_more_and_staging():
    from miniprint.plate import arrange, convex_overlap, place_more, rot2, staging
    sword = np.array([[0, 0], [111, 0], [111, 8], [0, 8.0]])
    place, n, big = arrange({"s": sword}, 100, 100, gap=4, margin=5)
    assert not big and n == 1                                  # fits diagonally
    Q = sword @ rot2(place["s"]["angle"]).T + [place["s"]["tx"], place["s"]["ty"]]
    assert Q.min() >= 5 - 1e-6 and Q.max() <= 95 + 1e-6
    huge = np.array([[0, 0], [200, 0], [200, 50], [0, 50.0]])
    assert arrange({"h": huge}, 100, 100)[2] == ["h"]
    # new parts go into the free space, existing ones stay
    sq = np.array([[0, 0], [30, 0], [30, 30], [0, 30.0]])
    occ = [(0, sq + [10, 10])]
    pl, n, big = place_more(occ, {"a": sq, "b": sq}, 100, 100, 1, gap=4, margin=5)
    assert n == 1 and not big
    outs = [sq @ rot2(p["angle"]).T + [p["tx"], p["ty"]] for p in pl.values()]
    for O in outs:
        assert not convex_overlap(O, occ[0][1]) and O.min() >= 5 - 1e-6 and O.max() <= 95 + 1e-6
    assert not convex_overlap(outs[0], outs[1])
    # parts that are not on a plate line up beside it without touching
    st = staging({"a": sq, "b": sq, "h": huge}, 100)
    O = {k: (sq if k != "h" else huge) @ rot2(p["angle"]).T + [p["tx"], p["ty"]] for k, p in st.items()}
    assert all(o[:, 0].min() > 100 for o in O.values())
    ks = list(O)
    assert not any(convex_overlap(O[a], O[b]) for i, a in enumerate(ks) for b in ks[i + 1:])
