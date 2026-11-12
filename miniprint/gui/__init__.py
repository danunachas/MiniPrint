"""The browser GUI: a small local HTTP server (standard library only) plus the page in ``miniprint/web``.

    session.py   what is loaded: the parts, their poses, supports and places on the plates (no HTTP in here)
    jobs.py      the long-running work (prepare, orientation search, supports, the batch, the calibration set)
    layout.py    build plates: arranging, moving parts by hand, the meshes of a plate
    export.py    3MF / STL downloads
    presets.py   settings profiles (presets.json)
    http.py      the routes: one small function per ``/api/...`` address
    server.py    start-up (``python -m miniprint.gui``)
"""
