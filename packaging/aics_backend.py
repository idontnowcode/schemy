"""Entry point of the bundled backend (PyInstaller): the same as ``python -m backend``."""

import sys

from backend.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
