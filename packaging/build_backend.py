"""Build the backend into one folder with its own Python (PyInstaller), for the desktop installer.

    uv run --group packaging python packaging/build_backend.py

Output: build/backend/aics-backend/ (aics-backend.exe on Windows). PyInstaller builds for the OS it runs on, so
the Windows installer is built on Windows (see packaging/build-windows.ps1).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
SEP = os.pathsep  # PyInstaller's --add-data uses the platform's path separator between source and target


def main() -> None:
    PyInstaller.__main__.run([
        str(ROOT / "packaging" / "aics_backend.py"),
        "--name", "aics-backend",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--console",  # stdout carries the ready line to the desktop shell; the shell hides the window
        "--distpath", str(ROOT / "build" / "backend"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build" / "pyinstaller"),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'examples'}{SEP}examples",
        "--add-data", f"{ROOT / 'ai_engine' / 'prompts'}{SEP}ai_engine/prompts",
        # Loaded by name at run time, so PyInstaller cannot see them in the imports.
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "keyring.backends",
        "--collect-submodules", "backend",
        "--collect-submodules", "ai_engine",
        "--collect-submodules", "circuit_core",
        "--collect-submodules", "kicad_adapter",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
    ])
    name = "aics-backend.exe" if sys.platform == "win32" else "aics-backend"
    exe = ROOT / "build" / "backend" / "aics-backend" / name
    print(f"built {exe}" if exe.is_file() else f"missing {exe}")


if __name__ == "__main__":
    main()
