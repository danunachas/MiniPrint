"""Kept so that ``python -m miniprint.server`` (used by MiniPrint.exe, the batch files and older guides) still
starts the GUI.  The code lives in ``miniprint/gui``."""
from .gui.server import main

if __name__ == "__main__":
    main()
