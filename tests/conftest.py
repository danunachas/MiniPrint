"""Shared fixtures: a running GUI server with an empty project and a private settings folder."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from miniprint.geometry import box, merge
from miniprint.stlio import write_stl


class Client:
    """Talks to the GUI server the way the page does."""

    def __init__(self, base):
        self.base = base

    def request(self, path, data=None, headers=None):
        """(status, body bytes).  data: None = GET, bytes = POST as they are, anything else = POST as JSON."""
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def json(self, path, data=None):
        status, body = self.request(path, data)
        assert status == 200, (path, status, body[:300])
        return json.loads(body)

    def job(self, path, data):
        """Start a background job and wait for it; returns its result."""
        jid = self.json(path, data)["job"]
        for _ in range(3000):
            state = self.json(f"/api/job?id={jid}")
            if state["done"]:
                assert not state["error"], "\n".join(state["log"])
                return state["result"]
            time.sleep(0.1)
        raise AssertionError("job did not finish")


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    import os
    os.environ["MINIPRINT_DATA"] = str(tmp_path_factory.mktemp("settings"))
    from miniprint.gui.http import Handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def client(server):
    from miniprint.gui.session import S
    with S.lock:
        S.clear()
        S.set_plate({"w": 256.0, "d": 256.0, "gap": 4.0, "margin": 5.0, "turn": True})
    return Client(server)


@pytest.fixture(scope="session")
def table_stl(tmp_path_factory):
    """A small table: a top on one thick leg (flat base on the plate, overhang all around)."""
    path = tmp_path_factory.mktemp("models") / "table.stl"
    write_stl(str(path), *merge([box([-4, -4, 0], [4, 4, 9.99]), box([-12, -9, 10], [12, 9, 12])]))
    return path
