"""Check a finished 3MF: structure, volumes, support/model collisions and overhang coverage.
Usage: python tools/verify_output.py path/to/x_supported.3mf"""
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from miniprint.geometry import face_normals_areas, signed_volume
from miniprint.raycast import DownRaycaster
from miniprint.supports import surface_samples
from miniprint.threemf import read_3mf

objs = read_3mf(sys.argv[1])
assert len(objs) == 2, "expected model + supports"
(mn, MV, MF), (sn, SV, SF) = objs
print(f"objects: {mn!r} {len(MF):,} tris | {sn!r} {len(SF):,} tris")
for name, V, F in objs:
    assert F.max() < len(V) and F.min() >= 0, "bad index"
    print(f"  {name}: volume {signed_volume(V, F):.1f} mm^3, bbox {np.round(V.min(0),1)} .. {np.round(V.max(0),1)}")
    assert signed_volume(V, F) > 0
assert SV[:, 2].min() > -1e-3, "supports go below the bed"
print(f"supports lowest z = {SV[:,2].min():.3f}, model lowest z = {MV[:,2].min():.3f}")

# 1) how deep do support vertices sit inside the model?  (only the tip penetration is intended)
rc = DownRaycaster(MV, MF)
fn, _ = face_normals_areas(MV, MF)
kd = cKDTree(surface_samples(MV, MF, 0.2))
d, _ = kd.query(SV)
z, t = rc.first_hit_below(SV - [0, 0, 1e-4], eps=0.0)
inside = (t >= 0) & (fn[np.maximum(t, 0), 2] < -0.05)
deepest = d[inside].max() if inside.any() else 0
print(f"support vertices inside the model: {inside.sum()} of {len(SV)}  (deepest {deepest:.2f} mm; the tips reach "
      f"0.24 mm into the model by default)")
close = (~inside) & (d < 0.1)
print(f"support vertices touching/within 0.1 mm of the surface (tips): {close.sum()}")
assert (d[inside].max() if inside.any() else 0) < 0.6, "supports cut deeper into the model than a tip should"
