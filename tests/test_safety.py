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


def test_windows_start_files_are_crlf_ascii_and_read_only():
    bats = sorted(ROOT.glob("*.bat"))
    assert {b.name for b in bats} >= {"START.bat", "STOP.bat", "INSTALLIEREN.bat", "DEINSTALLIEREN.bat"}
    for b in bats:
        data = b.read_bytes()
        assert data.decode("ascii")                         # cmd.exe zeigt Umlaute sonst falsch an
        assert b"\n" not in data.replace(b"\r\n", b"")      # nur Windows-Zeilenenden (sonst springt goto falsch)
        assert b"order_send" not in data.lower() and b"order_check" not in data.lower()


def test_installer_scripts_have_bom_and_no_order_calls():
    for f in (ROOT / "installer").glob("*.ps1"):
        data = f.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf")             # Windows PowerShell 5.1 liest Umlaute nur mit BOM richtig
        assert b"order_send" not in data and b"order_check" not in data
