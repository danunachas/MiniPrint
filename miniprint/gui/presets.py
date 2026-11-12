"""Settings profiles.  They live in ``presets.json`` in MiniPrint's data folder: the program folder by
default, or the folder named by the environment variable ``MINIPRINT_DATA`` (used by the Docker image).

    {"presets": {name: values}, "last": name or None, "current": values}

``values`` is what the page tracks (element id -> value); the server never interprets it.
"""
from __future__ import annotations

import json
import os
import threading

_lock = threading.Lock()


def data_dir():
    custom = os.environ.get("MINIPRINT_DATA")
    if custom:
        os.makedirs(custom, exist_ok=True)
        return custom
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def path():
    return os.path.join(data_dir(), "presets.json")


def load():
    file = path()
    try:
        with open(file, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            d.setdefault("presets", {})
            return d
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as ex:           # damaged file: keep a copy, start fresh
        try:
            os.replace(file, file + ".broken")
        except OSError:
            pass
        print(f"presets.json could not be read ({ex}); saved it as presets.json.broken")
    return {"presets": {}, "last": None, "current": None}


def _save(d):
    file = path()
    tmp = file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1, sort_keys=True)
    os.replace(tmp, file)                         # atomic: a crash never leaves half a file


def update(action, name=None, values=None):
    """action: "save" (store values under name), "delete", or "state" (remember the current settings and the
    selected profile for the next start).  Returns the whole presets document."""
    name = str(name or "").strip()[:60]
    with _lock:
        d = load()
        if action == "save":
            if not name or not isinstance(values, dict):
                raise ValueError("a profile needs a name and values")
            d["presets"][name] = values
            d["last"], d["current"] = name, values
        elif action == "delete":
            d["presets"].pop(name, None)
            if d.get("last") == name:
                d["last"] = None
        elif action == "state":
            if isinstance(values, dict):
                d["current"] = values
            d["last"] = name or None
        else:
            raise ValueError("unknown profile action")
        _save(d)
        return d
