"""The calibration pieces."""

import numpy as np

from miniprint.geometry import signed_volume
from miniprint.supports import SupportParams, build_supports


def test_calibration_pieces():
    from miniprint import calibration as cal
    from miniprint.decimate import edge_report
    V, F = cal.specimen(0.4, 0.2)
    assert signed_volume(V, F) > 300
    assert abs(V[:, 2].min()) < 1e-9 and V[:, 2].max() > cal.HEIGHT
    assert not any(edge_report(V, F).values()), edge_report(V, F)
    # the flat 90 deg underside and the 45 deg slope are there
    N = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    A = np.linalg.norm(N, axis=1)
    n = N / A[:, None]
    assert abs(A[(n[:, 2] < -0.99) & (V[F[:, 0], 2] < 0.01)].sum() / 2 - cal.FLAT * cal.DEPTH) < 1e-6  # underside
    assert A[np.abs(n[:, 2] + np.sqrt(0.5)) < 0.01].sum() / 2 > 40            # slope
    assert cal.DEFAULT_TIPS[0] == 0.36 and len(cal.DEFAULT_TIPS) == 7
    assert cal.parse_list("0.5; 0.3 0.3, x, 9", 0.1, 2, (1,)) == [0.3, 0.5]
    assert cal.parse_list("", 0.1, 2, (0.4,)) == [0.4]
    assert cal.name(0.3, 0.1) == "Calibration tip 0.30 pen 0.10"
    sp = SupportParams(tip_diameter=0.4, tip_penetration=0.2, overhang_angle=44, flat_on_plate=False)
    res = build_supports(V + [0, 0, cal.LIFT], F, sp, log=lambda *a: None)     # floating, as in the app
    st = res.stats
    assert st["supported"] > 20 and not st.get("failed"), st
