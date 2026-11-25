"""Zeitmaschine – Strategien auf den eigenen MT5-Kursen testen, bevor echtes Geld daran hängt (NUR LESEND).

Spielt die Regeln des Gold-Bots (Order Blocks) oder des Opening Range Breakout Kerze für Kerze auf den echten
Kerzen deines Brokers durch, mit Spread, Slippage, Kommission und Swap in drei Kostenstufen, und rechnet danach
mit Wochen-Blöcken aus, was mit deinem Konto auch hätte passieren können (Gewinnrate, CRV, Rückgang, Ruin).
Es wird nichts gehandelt: keine Orders, auch nicht auf dem Demokonto.

Aufruf (oder einfach ZEITMASCHINE.bat doppelklicken):
    python zeitmaschine.py goldbot                       # Gold-Bot auf XAUUSD M15, letzte 365 Tage
    python zeitmaschine.py orb --symbol XAUUSD           # Opening Range Breakout Gold (M5, New York)
    python zeitmaschine.py orb --symbol BTCUSD
    python zeitmaschine.py orb --symbol EURUSD           # London-Eröffnung; Kerzen kommen aus dem offenen MT5
    python zeitmaschine.py alle                          # alles nacheinander
    Optionen: --tage 730  --alles (ganze Historie)  --konto 357  --lot 0.01  --kosten hart  --laeufe 10000
              --wochen 52  --block 4  --mt5 (frische Kerzen aus MT5 dazuholen)  --kein-browser
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tradingbot.config import load_config  # noqa: E402
from tradingbot.zeitmaschine import bericht  # noqa: E402
from tradingbot.zeitmaschine.daten import DataSource  # noqa: E402
from tradingbot.zeitmaschine.kosten import ORDER  # noqa: E402
from tradingbot.zeitmaschine.lauf import run  # noqa: E402

ALLE = [("goldbot", "XAUUSD"), ("orb", "XAUUSD"), ("orb", "BTCUSD"), ("orb", "EURUSD")]


def log(*a, **k):
    print(*a, **k, flush=True)


def main(argv=None, client=None, now=None) -> int:
    ap = argparse.ArgumentParser(description="Zeitmaschine – Strategien auf echten MT5-Kursen testen (keine Orders)")
    ap.add_argument("strategie", choices=["goldbot", "orb", "alle"])
    ap.add_argument("--symbol", help="z. B. XAUUSD (Standard), BTCUSD, EURUSD – Name wie in der Datenbank/im MT5")
    ap.add_argument("--tage", type=int, default=365, help="so viele Tage zurück (Standard 365)")
    ap.add_argument("--alles", action="store_true", help="die ganze vorhandene Historie")
    ap.add_argument("--konto", type=float, default=357.0, help="Startkonto in USD (Standard 357)")
    ap.add_argument("--lot", type=float, help="Lot je Order/Teil (Standard: Gold-Bot aus goldbot.toml, sonst 0.01)")
    ap.add_argument("--kosten", choices=ORDER, default="konservativ", help="Kostenszenario für Monte Carlo")
    ap.add_argument("--laeufe", type=int, default=10_000, help="Monte-Carlo-Durchläufe (Standard 10 000)")
    ap.add_argument("--wochen", type=int, default=52, help="Zeitraum je Durchlauf in Wochen (Standard 52)")
    ap.add_argument("--block", type=float, default=4.0, help="mittlere Blocklänge in Wochen (Standard 4)")
    ap.add_argument("--ruin", type=float, help="Ruin = Konto unter diesem Betrag (Standard 10 %% vom Startkonto)")
    ap.add_argument("--max-risiko", type=float, default=-1,
                    help="Trades mit mehr Risiko (%% vom Konto) auslassen; Standard wie der Bot, 0 = keine Grenze")
    ap.add_argument("--sitzung", choices=["london", "new_york", "utc"], help="ORB: Eröffnung")
    ap.add_argument("--range", type=int, dest="range_min", help="ORB: Range in Minuten (Standard 15)")
    ap.add_argument("--crv", type=float, help="ORB: Ziel als Vielfaches des Risikos (Standard 1.5)")
    ap.add_argument("--zeitrahmen", help="ORB: Zeitrahmen (Standard M5)")
    ap.add_argument("--mt5", action="store_true", help="frische Kerzen aus dem offenen MT5 dazuholen (nur lesen)")
    ap.add_argument("--kein-browser", action="store_true", help="Bericht nicht im Browser öffnen")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--goldbot-config", default=str(ROOT / "goldbot.toml"))
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    print("ZEITMASCHINE – nur Rechnung auf vergangenen Kursen, es wird NICHTS gehandelt.\n")
    if client is None:
        try:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
        except Exception:                                           # noqa: BLE001 – ohne MT5 nur Datenbank
            client = None
    src = DataSource(cfg.data_root, client=client, use_mt5=a.mt5, log=log)
    from tradingbot.goldbot import config as gconf
    gcfg = gconf.load(a.goldbot_config, str(cfg.data_root))
    jobs = ALLE if a.strategie == "alle" else [(a.strategie, a.symbol or "XAUUSD")]
    orb = {k: v for k, v in (("session", a.sitzung), ("range_min", a.range_min), ("rr", a.crv),
                             ("tf", a.zeitrahmen)) if v is not None}
    out_dir = Path(cfg.report_dir) / "zeitmaschine"
    pages, rc = [], 0
    for strat, sym in jobs:
        if a.strategie == "alle" and a.symbol and strat == "goldbot":
            sym = a.symbol
        print(f"=== {bericht.NAMES[strat]} – {sym} ===")
        lot = a.lot if a.lot else (gcfg.lot if strat == "goldbot" else 0.01)
        try:
            o = run(src, strat, sym, tage=None if a.alles else a.tage, konto=a.konto, lot=lot, laeufe=a.laeufe,
                    wochen=a.wochen, block_wochen=a.block, szenario=a.kosten, gcfg=gcfg, orb=orb,
                    ruin_konto=a.ruin, calendars=getattr(cfg, "calendars", None),
                    max_risiko_pct=(None if a.max_risiko == 0 else a.max_risiko), log=log, now=now)
        except Exception as e:                                      # noqa: BLE001 – nächstes Symbol trotzdem
            print(f"  übersprungen: {e}\n")
            rc = 1
            continue
        print()
        print(bericht.text(o))
        p_html, _ = bericht.save(o, out_dir)
        print(f"\nBericht: {p_html}\n")
        pages.append(p_html)
    if client is not None:
        try:
            client.shutdown()
        except Exception:                                           # noqa: BLE001
            pass
    if pages and not a.kein_browser:
        for p in pages:
            webbrowser.open(p.resolve().as_uri())
    return rc if pages else 1


if __name__ == "__main__":
    sys.exit(main())
