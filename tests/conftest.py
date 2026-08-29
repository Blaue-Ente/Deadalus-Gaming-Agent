"""Product tests run with the repository root on ``sys.path``."""
from __future__ import annotations

import sys
from pathlib import Path

_PRODUCT = Path(__file__).resolve().parents[1]
if str(_PRODUCT) not in sys.path:
    sys.path.insert(0, str(_PRODUCT))
