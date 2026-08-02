# ---------------------------------------------------------
# Copyright (C) 2026 softpsycho
#
# Licensed under the GNU GPLv3.
# ---------------------------------------------------------

"""Pytest configuration: make the project root importable so tests can
``from src.core...`` without requiring an editable install."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
