#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


# This wrapper is itself launched from the immutable export.  Disable bytecode
# before importing the packaged runner so pre-verification cannot create
# __pycache__ inside the boundary it is about to verify.
sys.dont_write_bytecode = True
PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from flowness_oss_harness.cleanroom_acceptance import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
