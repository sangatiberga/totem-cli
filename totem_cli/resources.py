"""Runtime resource paths that work both from source and PyInstaller onefile."""
from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """Directory holding templates/ and assets/ at runtime.

    Inside a PyInstaller ``--onefile`` bundle, data files land under
    ``sys._MEIPASS`` preserving the ``totem_cli/...`` layout used below.
    """
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "totem_cli"  # type: ignore[arg-type]
    return Path(__file__).resolve().parent
