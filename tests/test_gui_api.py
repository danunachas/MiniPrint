"""The GUI server, driven over HTTP exactly like the page does (no browser needed)."""
import io
import json
import zipfile

import numpy as np

from miniprint.gui.session import S
from miniprint.threemf import read_3mf


def upload(client, path, scale=1.0):
    status, body = client.request(f"/api/load?name={path.name}", path.read_bytes())
    assert status == 200, body
    part = json.loads(body)
    client.json("/api/select", {"id": part["id"]})
    client.job("/api/prepare", {"target": 0, "scale": scale})
    return part["id"]


def read_mesh(blob):
    nv, nf = np.frombuffer(blob[:8], "<u4")
    V = np.frombuffer(blob[8:8 + 12 * nv], "<f4").reshape(-1, 3)
    F = np.frombuffer(blob[8 + 12 * nv:], "<u4").reshape(-1, 3)
    assert len(F) == nf
    return V, F


def test_page_and_scripts_are_served(client):
    status, body = client.request("/")
    assert status == 200 and b"MiniPrint" in body
    for name in set(__import__("re").findall(rb'(?:src|href)="/web/([^"]+)"', body)):
        assert client.request("/web/" + name.decode())[0] == 200, name
    assert client.request("/web/../gui/http.py")[0] == 404
    assert client.request("/api/nothing")[0] == 404


def test_only_this_computer_and_this_page_may_call(client):
    assert client.request("/api/info", headers={"Host": "evil.example:8765"})[0] == 403
    assert client.request("/api/touch", {}, headers={"Origin": "http://evil.example"})[0] == 403


def test_one_part_from_upload_to_export(client, table_stl):
    pid = upload(client, table_stl)
    info = client.json("/api/info")
    assert info["loaded"] and info["parts"][0]["state"] == "new"
    assert [round(x) for x in info["size"]] == [24, 18, 12]

    r = client.job("/api/supports", {"support": {"tree_spacing": 2.0}})
    assert r["stats"]["supported"] > 20 and not r["stats"]["failed"] and r["check"]["ok"]
    V, F = read_mesh(client.request("/api/supports_mesh")[1])
    assert len(F) == r["stats"]["support_triangles"] and V[:, 2].min() >= 0

    # a turn about the vertical axis keeps the supports, any other turn removes them
    assert client.json("/api/rotate", {"axis": "z", "deg": 30})["has_supports"]
    assert not client.json("/api/rotate", {"axis": "x", "deg": 90})["has_supports"]
    assert client.json("/api/rotate", {"reset": True})["parts"][0]["state"] == "oriented"

    client.job("/api/supports", {"support": {"tree_spacing": 2.0}})
    info = client.json("/api/arrange", {"w": 120, "d": 120, "gap": 4, "margin": 5, "turn": True})
    assert info["nplates"] == 1 and info["parts"][0]["plate"] == 1
    index = client.json("/api/plate_parts?plate=0")
    assert index["parts"][0]["id"] == pid and not index["overlaps"] and not index["outside"]

    status, data = client.request("/api/export_plates?name=test&anchor=1")
    assert status == 200
    path = table_stl.parent / "plate.3mf"
    path.write_bytes(data)
    names = [name for name, _, _ in read_3mf(str(path))]
    assert names == ["table model", "table supports"]

    assert not client.json("/api/clear_supports", {})["has_supports"]
    assert client.request("/api/export?fmt=supports")[0] == 400
    assert client.json("/api/remove", {"id": pid})["parts"] == []


def test_batch_orients_supports_and_places_every_part(client, table_stl):
    for _ in range(2):
        status, _ = client.request(f"/api/load?name={table_stl.name}", table_stl.read_bytes())
        assert status == 200
    plate = {"w": 42, "d": 42, "gap": 4, "margin": 5, "turn": True}
    info = client.job("/api/process_all", {"target": 0, "scale": 1.0, "support": {"tree_spacing": 2.0}, "plate": plate})
    assert [p["state"] for p in info["parts"]] == ["supported", "supported"]
    assert [p["name"] for p in info["parts"]] == ["table", "table (2)"]
    assert info["batch"] is None and info["nplates"] == 2          # only one table fits a 42 mm plate
    status, data = client.request("/api/export_plates?name=two")
    assert status == 200 and sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["two_plate1.3mf", "two_plate2.3mf"]

    # moving a part off its plate puts it back into the waiting row; it is not exported any more
    client.json("/api/place", {"id": info["parts"][1]["id"], "dx": 500})
    assert client.json("/api/info")["parts"][1]["plate"] is None


def test_calibration_set_is_laid_out_as_a_grid(client):
    info = client.job("/api/calibration", {"tips": "0.4, 0.5", "pens": "0.2", "height": 4,
                                           "plate": {"w": 256, "d": 256, "gap": 4, "margin": 5, "turn": True}})
    assert [p["name"] for p in info["parts"]] == ["Calibration tip 0.40 pen 0.20", "Calibration tip 0.50 pen 0.20"]
    assert all(p["state"] == "supported" and p["plate"] == 1 and p["lift"] == 4 for p in info["parts"])
    back, front = (S.parts[p["id"]].placed_outline()[:, 1].mean() for p in info["parts"])
    assert back > front                                             # rows: first tip diameter at the back


def test_profiles_are_saved_and_listed(client):
    doc = client.json("/api/presets", {"action": "save", "name": "My printer", "values": {"p_tip_diameter": "0.52"}})
    assert doc["last"] == "My printer"
    assert client.json("/api/presets")["presets"]["My printer"] == {"p_tip_diameter": "0.52"}
    assert "My printer" not in client.json("/api/presets", {"action": "delete", "name": "My printer"})["presets"]
    assert client.request("/api/presets", {"action": "nonsense"})[0] == 400


def test_page_and_scripts_agree(client):
    """Every element the scripts look up by id exists in the page, and every script the page needs is served."""
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "miniprint" / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    for script in sorted(web.glob("*.js")):
        source = script.read_text(encoding="utf-8")
        wanted = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', source))
        assert not wanted - ids, (script.name, sorted(wanted - ids))
        for other in re.findall(r'from "\./([a-z]+\.js)"', source):
            assert (web / other).is_file(), (script.name, other)
        status, body = client.request("/web/" + script.name)
        assert status == 200 and body == script.read_bytes()
    status, _ = client.request("/web/style.css")
    assert status == 200


def test_examples_can_be_loaded(client):
    examples = client.json("/api/examples")
    assert {e["name"] for e in examples} >= {"shield", "torus_knot"}
    loaded = client.json("/api/load_example", {"name": "shield"})
    assert loaded["orig_tris"] > 100 and client.json("/api/info")["parts"][0]["name"] == "shield"
    assert client.request("/api/load_example", {"name": "../secret"})[0] == 404


def test_which_host_names_are_answered(monkeypatch):
    from miniprint.gui.http import host_allowed
    assert host_allowed("127.0.0.1", True) and host_allowed("localhost", True)
    assert not host_allowed("192.168.1.20", True) and not host_allowed("evil.example", True)
    # started with --host (home network, Docker): addresses and home-network names, not internet domains
    assert host_allowed("192.168.1.20", False) and host_allowed("nas", False) and host_allowed("printer.local", False)
    assert not host_allowed("evil.example", False)
    monkeypatch.setenv("MINIPRINT_HOSTS", "miniprint.example.org, other.example")
    assert host_allowed("miniprint.example.org", False) and not host_allowed("evil.example", False)
