"""Tests für Phase 1 mit simuliertem MT5-Modul (laufen auch ohne Windows/Terminal).

    python -m unittest discover tests
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "phase1_bestandsaufnahme.py"


class Phase1Test(unittest.TestCase):
    def test_no_order_calls_in_code(self):
        code = SCRIPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\.\s*order_(send|check)\s*\(", code))

    def test_run_with_fake_mt5(self):
        with tempfile.TemporaryDirectory() as out:
            env = dict(os.environ, PYTHONPATH=str(ROOT / "tests" / "fake_mt5"))
            proc = subprocess.run([sys.executable, str(SCRIPT), "--sample-seconds", "2", "--out", out],
                                  env=env, capture_output=True, text=True, timeout=120)
            report = json.loads(next(Path(out).glob("*.json")).read_text(encoding="utf-8"))
            self.assertTrue(list(Path(out).glob("*.md")))

        assets = report["data"]["assets"]
        self.assertEqual(assets["gold"]["symbol"], "XAUUSD")
        self.assertEqual(assets["btc"]["symbol"], "BTCUSD")
        for a in assets.values():
            self.assertGreater(a["quotes"]["ask"], a["quotes"]["bid"])
            self.assertGreater(a["history"]["D1"]["bars"], 0)
        # Außer OS-Prüfung (Linux) und pip-Metadaten des Fake-Pakets darf nichts fehlschlagen.
        fails = [c for c in report["checks"] if c["status"] == "FEHLER" and c["name"] not in ("Betriebssystem", "Paket MetaTrader5")]
        self.assertEqual(fails, [], proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
