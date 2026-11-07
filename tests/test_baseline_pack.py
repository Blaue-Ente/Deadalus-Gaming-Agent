"""Validate the committed P0.00 golden baseline pack.

Does not re-run the product suite or the honesty bench. The pack itself is
the recorded snapshot; this module only checks that the six artifacts exist
and satisfy the contracts in ``scripts/record_baseline.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import record_baseline as baseline  # noqa: E402


def test_six_baseline_artifacts_exist():
    missing = [
        name
        for name in baseline.ARTIFACTS
        if not (ROOT / "artifacts" / "baseline" / name).is_file()
    ]
    assert missing == [], f"missing baseline artifacts: {missing}"


def test_baseline_pack_check_passes():
    errors = baseline.check_pack(ROOT)
    assert errors == [], "\n".join(errors)
