"""Phase 2 – Datenbasis aufbauen und aktualisieren (NUR LESEND, keine Trades).

Beim ersten Start wird die gesamte verfügbare Historie blockweise geladen, bei jedem
weiteren Start nur noch, was fehlt. Anschließend läuft die Qualitätsprüfung und ein
Bericht wird unter reports/ abgelegt.

Aufruf (Windows, MT5-Terminal geöffnet und eingeloggt):
    python daten_aktualisieren.py
    python daten_aktualisieren.py --symbole XAUUSD --zeitrahmen H1 D1
    python daten_aktualisieren.py --nur-pruefung      # ohne MT5, nur lokale Daten prüfen
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tradingbot.config import load_config
from tradingbot.pipeline import run_quality, run_update
from tradingbot.report import write_report


def main(argv=None, client=None, now_utc_fn=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 2 – Datenbasis (nur lesend, keine Trades)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.toml"))
    ap.add_argument("--symbole", nargs="+", help="nur diese Symbole")
    ap.add_argument("--zeitrahmen", nargs="+", help="nur diese Zeitrahmen, z. B. H1 D1")
    ap.add_argument("--nur-pruefung", action="store_true", help="keine MT5-Verbindung, nur lokale Daten prüfen")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    print("Phase 2 – Datenbasis (nur lesend, keine Trades)")
    print(f"Datenordner: {cfg.data_root}")
    t0 = time.time()
    update = None
    if not args.nur_pruefung:
        if client is None:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
        update = run_update(cfg, client, now_utc_fn=now_utc_fn, symbols=args.symbole, timeframes=args.zeitrahmen)
    print("Qualitätsprüfung ...")
    quality = run_quality(cfg, symbols=args.symbole, timeframes=args.zeitrahmen)
    unexplained = 0
    for q in quality["results"]:
        s = q.summary
        n_gap = s.get("gaps", {}).get("data_gap", 0)
        unexplained += n_gap
        print(f"  {q.symbol:<8} {q.tf:<4} {s['rows']:>9} Kerzen  {s['first_utc'][:10]} – {s['last_utc'][:10]}  "
              f"Dupl. {s['duplicates']}  ungültig {s['invalid_ohlc']}  unerklärte Lücken {n_gap}  "
              f"Abdeckung {s['coverage_pct']} %  Ausreißer {sum(s['anomalies'].values())}")
    md, js = write_report(cfg.report_dir, update, quality)
    print(f"\nFertig in {time.time() - t0:.0f} s. Bericht: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
