"""Start the GUI:  ``python -m miniprint.gui``  (or ``python -m miniprint.server``, or MiniPrint.exe).

Everything stays on your own computer.  The page talks to this small server with plain HTTP; heavy work runs
as background jobs whose log the page shows.
"""
from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from .. import __version__
from . import logfile
from .http import Handler

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m miniprint.gui", description="MiniPrint in your browser.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MINIPRINT_PORT", 8765)),
                    help="port to listen on (default %(default)s)")
    ap.add_argument("--host", default=os.environ.get("MINIPRINT_HOST", "127.0.0.1"),
                    help="address to listen on (default %(default)s = this computer only; 0.0.0.0 lets other "
                         "devices on your network use it - MiniPrint has no password, only do that on a network "
                         "you trust)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    args = ap.parse_args(argv)

    log = logfile.open_log()
    local = args.host in LOOPBACK
    Handler.local_only = local
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{'127.0.0.1' if local or args.host == '0.0.0.0' else args.host}:{args.port}/"
    logfile.write(f"MiniPrint {__version__} is running at {url}   (close this window or press Ctrl+C to stop)")
    if log:
        logfile.write(f"Log file: {log}")
    if not local:
        logfile.write(f"Listening on {args.host}: other devices on the network can reach MiniPrint. "
                      "It has no password.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except BaseException as ex:
        logfile.write(f"MiniPrint stopped: {type(ex).__name__}: {ex}")
        raise
    finally:
        server.server_close()
        logfile.write("MiniPrint stopped")


if __name__ == "__main__":
    main()
