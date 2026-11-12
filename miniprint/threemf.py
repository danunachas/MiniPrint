"""Minimal 3MF writer (core specification) and reader used for self-checks."""
from __future__ import annotations

import re
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np

_CT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
)


def write_3mf(path: str, objects, group: str | None = None):
    """objects: list of (name, V, F), same coordinate system.
    group=None: each is its own build item. group="name": all are parts of ONE object (so a slicer does not
    run its object-to-object path conflict check between the model and its own supports)."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        "<resources>",
    ]
    for oid, (name, V, F) in enumerate(objects, start=1):
        out.append(f'<object id="{oid}" name={quoteattr(name)} type="model"><mesh><vertices>')
        out.append("".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in V))
        out.append("</vertices><triangles>")
        out.append("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F))
        out.append("</triangles></mesh></object>")
    if group:
        gid = len(objects) + 1
        out.append(f'<object id="{gid}" name={quoteattr(group)} type="model"><components>')
        out.append("".join(f'<component objectid="{i}"/>' for i in range(1, len(objects) + 1)))
        out.append("</components></object>")
        out.append(f'</resources><build><item objectid="{gid}"/>')
    else:
        out.append("</resources><build>")
        for oid in range(1, len(objects) + 1):
            out.append(f'<item objectid="{oid}"/>')
    out.append("</build></model>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "".join(out))


def write_3mf_groups(path: str, groups):
    """Several objects in one build: groups = [(object name, [(part name, V, F), ...]), ...].
    Each object becomes one 3MF object made of its parts (model, supports, ...), all in plate coordinates."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        "<resources>",
    ]
    oid = 0
    items = []
    for gname, parts in groups:
        ids = []
        for name, V, F in parts:
            if not len(F):
                continue
            oid += 1
            ids.append(oid)
            out.append(f'<object id="{oid}" name={quoteattr(name)} type="model"><mesh><vertices>')
            out.append("".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in V))
            out.append("</vertices><triangles>")
            out.append("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F))
            out.append("</triangles></mesh></object>")
        if not ids:
            continue
        if len(ids) == 1:
            items.append(ids[0])
            continue
        oid += 1
        out.append(f'<object id="{oid}" name={quoteattr(gname)} type="model"><components>')
        out.append("".join(f'<component objectid="{i}"/>' for i in ids))
        out.append("</components></object>")
        items.append(oid)
    out.append("</resources><build>")
    out.append("".join(f'<item objectid="{i}"/>' for i in items))
    out.append("</build></model>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "".join(out))


def read_3mf(path: str):
    """Read back (name, V, F) objects - used by the self-check."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("3D/3dmodel.model").decode("utf-8")
    res = []
    for m in re.finditer(r'<object id="(\d+)" name="([^"]*)"[^>]*>(.*?)</object>', xml, re.S):
        body = m.group(3)
        V = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"/>', body), dtype=float)
        F = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"/>', body), dtype=np.int64)
        if "<mesh>" not in body:
            continue
        res.append((m.group(2), V.reshape(-1, 3), F.reshape(-1, 3)))
    return res
