"""Run MiniPrint over every STL in a folder and write a summary table.

    python tools/run_corpus.py path/to/your/stl/folder  [--out output/corpus]

For each part: load + file check, reduction, orientation search, supports with the self-fixing slice check,
export, mesh check.  The summary (corpus_summary.md) lists time, pose height, tips, unsupported points,
islands, long spans and whether the export is watertight - the regression check after any change.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def grab(pat, text, default="-"):
    m = re.search(pat, text)
    return m.group(1) if m else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=os.path.join(HERE, "output", "corpus"))
    ap.add_argument("extra", nargs="*", help="extra miniprint options, e.g. --raft")
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.folder, "*.stl")) + glob.glob(os.path.join(a.folder, "*.STL")),
                   key=os.path.getsize)
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "miniprint", f, "-o", os.path.join(a.out, name)] + a.extra,
                           cwd=HERE, capture_output=True, text=True)
        log = r.stdout + r.stderr
        with open(os.path.join(a.out, name + ".log"), "w") as fh:
            fh.write(log)
        rows.append({
            "part": name, "ok": "yes" if r.returncode == 0 else "CRASH", "time s": f"{time.time() - t0:.0f}",
            "input tris": grab(r"\s([\d,]+) triangles, ", log),
            "file check": grab(r"file check: ([^\n]+)", log),
            "height mm": grab(r"#1 \*.*?height\s+([\d.]+) mm", log),
            "tips": grab(r"supported: (\d+)", log), "unsupported": grab(r"failed: (\d+)", log),
            "islands": grab(r"islands \(material printing in mid-air\): (\d+)", log),
            "long spans": grab(r"overhangs that must bridge further than 2.2 mm: (\d+)", log),
            "export": ("clean" if "no repair needed" in log
                       else "input defects" if "input file already" in log else "CHECK"),
        })
        print(" | ".join(str(v) for v in rows[-1].values()), flush=True)
    keys = list(rows[0].keys()) if rows else []
    md = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    md += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    with open(os.path.join(a.out, "corpus_summary.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"summary: {os.path.join(a.out, 'corpus_summary.md')}")


if __name__ == "__main__":
    main()
