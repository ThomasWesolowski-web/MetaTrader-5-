"""Phase 3 – Backtests, Walk-Forward und Strategieauswahl (NUR SIMULATION, keine Orders).

Voraussetzung: lokale Daten aus Phase 2 (python daten_aktualisieren.py).

Aufruf:
    python backtest_ausfuehren.py                          # alles (XAUUSD, BTCUSD; M1–H1; alle Strategien)
    python backtest_ausfuehren.py --zeitrahmen M5 M15      # nur bestimmte Zeitrahmen
    python backtest_ausfuehren.py --strategien ema_trend mss
    python backtest_ausfuehren.py --prozesse 2             # parallel (mehr Arbeitsspeicher)
    python backtest_ausfuehren.py --mt5-abgleich           # zusätzlich Gewinn/Margin gegen MT5 prüfen
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tradingbot.backtest import report
from tradingbot.backtest.runner import run_all, trade_strategies
from tradingbot.config import load_config
from tradingbot.storage import MarketStore


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 3 – Backtests (nur Simulation)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.toml"))
    ap.add_argument("--symbole", nargs="+", default=None)
    ap.add_argument("--zeitrahmen", nargs="+", default=["M1", "M5", "M15", "M30", "H1"])
    ap.add_argument("--strategien", nargs="+", default=None)
    ap.add_argument("--prozesse", type=int, default=1)
    ap.add_argument("--mt5-abgleich", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    symbols = args.symbole or cfg.symbols
    names = args.strategien or trade_strategies()
    unknown = set(names) - set(trade_strategies())
    if unknown:
        print(f"Unbekannte Strategien: {sorted(unknown)}. Verfügbar: {trade_strategies()}")
        return 2
    print("Phase 3 – Backtests (nur Simulation, keine Orders)")
    print(f"Märkte {symbols}, Zeitrahmen {args.zeitrahmen}, {len(names)} Strategien, {args.prozesse} Prozess(e)")
    t0 = time.time()
    data = run_all(cfg, symbols, args.zeitrahmen, names, workers=args.prozesse)

    cross = None
    if args.mt5_abgleich:
        from tradingbot.backtest import crosscheck
        if client is None:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
        print("Abgleich mit MT5-Rechenfunktionen ...")
        cross = crosscheck.run(client, MarketStore(cfg.data_root))

    md, js, latest = report.write(cfg.report_dir, data, cross)
    print("\nErgebnis je Markt:")
    for sym, rk in data["rankings"].items():
        print(f"  {sym}: {rk['verdict']}")
        for r in rk["ranking"][:5]:
            t = r["test"]
            print(f"    {r['rank']}. {r['strategy']} {r['tf']} (Score {r['score']}, "
                  f"{'qualifiziert' if r['qualified'] else 'nicht qualifiziert'}) – Test: {t['trades']} Trades, "
                  f"{t['net_profit_eur']} €, PF {t['profit_factor']}, DD {t['max_drawdown_pct']} %")
    print(f"\nFertig in {(time.time() - t0) / 60:.1f} min.\nBericht: {md}\nErgebnisdatei: {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
