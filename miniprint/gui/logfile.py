"""``miniprint.log`` in MiniPrint's data folder: what the console window shows, plus every job's log and any
error, so a problem can be looked into even when MiniPrint ran without a window (MiniPrint.pyw) or the window
is gone.  The file starts afresh when it passes 2 MB (the previous one is kept as ``miniprint.log.old``)."""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time

from . import presets

LIMIT = 2 * 1024 * 1024
_lock = threading.Lock()
_fh = None


def path():
    return os.path.join(presets.data_dir(), "miniprint.log")


def open_log():
    """Start logging to the file; also send Python's own crash report there (``faulthandler``), which is the
    only trace a crash inside numpy / scipy leaves."""
    global _fh
    file = path()
    try:
        if os.path.exists(file) and os.path.getsize(file) > LIMIT:
            os.replace(file, file + ".old")
        _fh = open(file, "a", encoding="utf-8", buffering=1)
    except OSError:                                    # read-only folder: no log file, everything else works
        return None
    faulthandler.enable(_fh)
    threading.excepthook = lambda a: write(f"error in a background thread: {a.exc_type.__name__}: {a.exc_value}")
    return file


def write(*parts):
    """One line, with the time, to the log file (and to the console when there is one)."""
    line = " ".join(str(x) for x in parts)
    if sys.stdout is not None:                         # pythonw.exe has no console
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass
    if _fh is None:
        return
    with _lock:
        try:
            _fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except (OSError, ValueError):
            pass
