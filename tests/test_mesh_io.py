"""Reading, writing and reducing meshes."""
import os
import tempfile

import numpy as np

from miniprint.geometry import box, signed_volume
from miniprint.stlio import read_stl, write_stl
from miniprint.supports import SupportParams, build_supports
from miniprint.threemf import read_3mf, write_3mf


def test_reduction_keeps_the_mesh_watertight():
    from miniprint.decimate import decimate, edge_report
    from miniprint.geometry import merge as gmerge
    from miniprint.geometry import signed_volume, sphere
    from miniprint.stlio import weld
    # two overlapping dense spheres (like a sculpt with overlapping shells), 2 x 9,800 triangles
    V, F = gmerge([sphere([0, 0, 0], 5.0, 50, 100), sphere([4, 0, 1], 3.0, 50, 100)])
    v0 = signed_volume(V, F)
    nV, nF, _ = decimate(V, F, 4000, log=lambda *a: None)
    assert len(nF) <= 4400, len(nF)
    rep = edge_report(nV, nF)
    assert rep == {"open": 0, "nonmanifold": 0, "flipped": 0}, rep
    assert abs(signed_volume(nV, nF) - v0) < 0.02 * v0
    # supports: every shell stays separate even when the file is welded (as slicers do with STL)
    res = build_supports(*box([-6, -6, 3], [6, 6, 4.5]), SupportParams(), log=lambda *a: None)
    assert edge_report(*weld(res.vertices[res.faces], 5)) == {"open": 0, "nonmanifold": 0, "flipped": 0}


def test_3mf_roundtrip_and_stl():
    V, F = box([0, 0, 0], [1, 2, 3])
    with tempfile.TemporaryDirectory() as d:
        write_3mf(os.path.join(d, "a.3mf"), [("m", V, F), ("s", V + 5, F)])
        objs = read_3mf(os.path.join(d, "a.3mf"))
        assert [o[0] for o in objs] == ["m", "s"]
        assert np.allclose(objs[1][1], V + 5, atol=1e-4) and (objs[0][2] == F).all()
        write_stl(os.path.join(d, "a.stl"), V, F)
        V2, F2 = read_stl(os.path.join(d, "a.stl"))
        assert len(F2) == 12 and abs(signed_volume(V2, F2) - 6) < 1e-6


def test_3mf_groups_one_object_per_part():
    import zipfile

    from miniprint.threemf import write_3mf_groups
    V, F = box([0, 0, 0], [1, 2, 3])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.3mf")
        write_3mf_groups(path, [("A", [("A model", V, F), ("A supports", V + 5, F)]), ("B", [("B model", V + 20, F)])])
        objs = read_3mf(path)
        assert [o[0] for o in objs] == ["A model", "A supports", "B model"]
        assert np.allclose(objs[2][1], V + 20, atol=1e-4)
        xml = zipfile.ZipFile(path).read("3D/3dmodel.model").decode()
        assert xml.count("<item ") == 2 and xml.count("<component ") == 2
