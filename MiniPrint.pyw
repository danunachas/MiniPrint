"""Double-click launcher for the MiniPrint GUI (no command prompt needed).

Starts the local server, opens the browser, and shows a tiny window. Closing that window stops MiniPrint.
"""
import os
import socket
import subprocess
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


def _message(title, text):
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk()
        r.withdraw()
        messagebox.showerror(title, text)
        r.destroy()
    except Exception:
        pass


def _ensure_packages():
    try:
        import numpy, scipy, PIL  # noqa: F401
        return True
    except ImportError:
        pass
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[:-11] + "python.exe"
    flags = 0x08000000 if os.name == "nt" else 0          # no console flash
    r = subprocess.run([py, "-m", "pip", "install", "-r", os.path.join(HERE, "requirements.txt")],
                       capture_output=True, text=True, creationflags=flags)
    if r.returncode != 0:
        _message("MiniPrint", "Could not install numpy/scipy/Pillow automatically:\n\n" + r.stderr[-800:])
        return False
    return True


def _port_busy():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    if _port_busy():                      # already running: just open the page
        webbrowser.open(URL)
        return
    if not _ensure_packages():
        return
    from http.server import ThreadingHTTPServer

    from miniprint.gui import logfile
    from miniprint.gui.http import Handler
    logfile.open_log()                    # no console here: miniprint.log is the only record of problems
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except Exception as e:
        _message("MiniPrint", f"Could not start the server:\n{e}")
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Timer(0.8, lambda: webbrowser.open(URL)).start()
    try:
        import tkinter as tk
    except ImportError:                   # no window available: keep serving
        threading.Event().wait()
        return
    root = tk.Tk()
    root.title("MiniPrint")
    root.geometry("300x120")
    root.resizable(False, False)
    tk.Label(root, text="MiniPrint is running", font=("Segoe UI", 12, "bold")).pack(pady=(14, 2))
    tk.Label(root, text="Close this window to stop it.").pack()
    tk.Button(root, text="Open in browser", command=lambda: webbrowser.open(URL)).pack(pady=10)
    root.mainloop()
    srv.shutdown()


try:
    main()
except Exception as e:                    # pythonw has no console, so show errors in a box
    _message("MiniPrint", f"{type(e).__name__}: {e}")
