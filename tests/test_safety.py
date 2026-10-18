"""Sicherheitsnetz: In Phase 1 und 2 darf kein Code Orders senden oder prüfen."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"\.\s*order_(send|check)\s*\(")


def test_no_order_functions_in_production_code():
    files = list((ROOT / "tradingbot").rglob("*.py")) + list(ROOT.glob("*.py"))
    assert files
    offenders = [str(f) for f in files if PATTERN.search(f.read_text(encoding="utf-8"))]
    assert offenders == []
