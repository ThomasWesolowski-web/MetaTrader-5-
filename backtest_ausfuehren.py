"""Phase 3 – Backtests, Walk-Forward und Strategieauswahl (NUR SIMULATION, keine Orders).

Voraussetzung: lokale Daten aus Phase 2 (python daten_aktualisieren.py).
Ausgelegt auf 8 GB RAM: 1 Prozess, Märkte nacheinander, Signale in Blöcken von 100 000 Kerzen.

Aufruf:
    python backtest_ausfuehren.py --testlauf               # nur den kleinen Testlauf (ca. 5–10 Minuten)
    python backtest_ausfuehren.py                          # vollständiger Lauf (Testlauf vorher Pflicht;
                                                           #   fehlt er, wird er automatisch zuerst ausgeführt)
    python backtest_ausfuehren.py --mt5-abgleich           # zusätzlich Gewinn/Margin gegen MT5 prüfen
    python backtest_ausfuehren.py --zeitrahmen M5 M15      # nur bestimmte Zeitrahmen
    python backtest_ausfuehren.py --strategien ema_trend mss
    python backtest_ausfuehren.py --blockgroesse 50000     # kleinere Blöcke (weniger RAM)
    python backtest_ausfuehren.py --neu                    # gespeicherte Teilergebnisse verwerfen

Ein abgebrochener Lauf wird beim nächsten Start automatisch fortgesetzt: fertige Testeinheiten liegen in
reports/phase3_pruefpunkte/ und werden übernommen, solange Code, Daten und Einstellungen unverändert sind.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tradingbot.backtest import report
from tradingbot.backtest.runner import (DEFAULT_BLOCK, MIN_BLOCK, compare_reference, run_all, run_testlauf,
                                       testlauf_ok, trade_strategies)
from tradingbot.config import load_config
from tradingbot.storage import MarketStore


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 3 – Backtests (nur Simulation)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.toml"))
    ap.add_argument("--symbole", nargs="+", default=None)
    ap.add_argument("--zeitrahmen", nargs="+", default=["M1", "M5", "M15", "M30", "H1", "H4"],
                    help="Jede Strategie läuft nur auf ihren eigenen Zeitrahmen (siehe Bericht)")
    ap.add_argument("--strategien", nargs="+", default=None)
    ap.add_argument("--prozesse", type=int, default=1,
                    help="Anzahl Prozesse (Standard 1; mehr nur bei ausreichend freiem RAM)")
    ap.add_argument("--mt5-abgleich", action="store_true", help="Gewinn/Margin gegen MT5-Rechenfunktionen prüfen")
    ap.add_argument("--blockgroesse", type=int, default=DEFAULT_BLOCK,
                    help=f"Kerzen je Block für die Signalberechnung (Standard {DEFAULT_BLOCK}; wird bei "
                         f"Speichermangel automatisch verkleinert, Minimum {MIN_BLOCK})")
    ap.add_argument("--testlauf", action="store_true", help="nur den kleinen Testlauf ausführen")
    ap.add_argument("--neu", action="store_true",
                    help="gespeicherte Teilergebnisse (Prüfpunkte) verwerfen und alles neu berechnen")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    symbols = args.symbole or cfg.symbols
    names = args.strategien or trade_strategies()
    unknown = set(names) - set(trade_strategies())
    if unknown:
        print(f"Unbekannte Strategien: {sorted(unknown)}. Verfügbar: {trade_strategies()}")
        return 2
    lines: list[str] = []

    def log(msg=""):
        print(msg, flush=True)
        lines.append(str(msg))

    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    log_file = cfg.report_dir / f"phase3_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        return _run(args, cfg, symbols, names, client, log, log_file)
    except BaseException:
        import traceback
        log(traceback.format_exc())
        raise
    finally:
        log_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"Protokoll: {log_file}")


def _run(args, cfg, symbols, names, client, log, log_file) -> int:
    if args.blockgroesse < MIN_BLOCK:
        log(f"--blockgroesse muss mindestens {MIN_BLOCK} sein")
        return 2
    ckpt_dir = cfg.report_dir / "phase3_pruefpunkte"
    if args.neu and ckpt_dir.exists():
        removed = 0
        for f in ckpt_dir.glob("*.json"):
            if f.name != "testlauf.json":       # Testlauf-Bestätigung hängt am Code, nicht an Teilergebnissen
                f.unlink()
                removed += 1
        log(f"--neu: {removed} gespeicherte Teilergebnisse verworfen")
    log("Phase 3 – Backtests (nur Simulation, keine Orders)")
    log(f"Märkte {symbols}, Zeitrahmen {args.zeitrahmen}, {len(names)} Strategien, {args.prozesse} Prozess(e), "
        f"Blockgröße {args.blockgroesse:,}")
    t0 = time.time()

    if args.testlauf or not testlauf_ok(ckpt_dir):
        if not args.testlauf:
            log("Kein bestandener Testlauf für den aktuellen Programmstand – der Testlauf wird zuerst ausgeführt.")
        t = run_testlauf(cfg, symbols, names, log=log, checkpoint_dir=ckpt_dir)
        if not t["ok"]:
            log("Abbruch: Testlauf nicht bestanden – der vollständige Backtest wird NICHT gestartet.")
            return 2
        if args.testlauf:
            log("Testlauf bestanden. Vollständiger Lauf: python backtest_ausfuehren.py")
            return 0

    data = run_all(cfg, symbols, args.zeitrahmen, names, workers=args.prozesse, log=log,
                   block_bars=args.blockgroesse, checkpoint_dir=ckpt_dir)
    ref_file = Path(__file__).resolve().parent / "docs" / "phase3_zusammenfassung.json"
    if ref_file.exists():
        data["reference_comparison"] = compare_reference(data, json.loads(ref_file.read_text(encoding="utf-8")))
        rc = data["reference_comparison"]
        log(f"Abgleich mit Lauf 2: {rc['identical']} von {rc['compared']} Einheiten identisch"
            + (f", abweichend: {rc['different'][:5]}" if rc["different"] else "")
            + (f"; Zeiträume geändert (neue Daten): {rc['skipped_changed_periods']}" if rc["skipped_changed_periods"] else ""))

    cross = None
    if args.mt5_abgleich:
        from tradingbot.backtest import crosscheck
        if client is None:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
        log("Abgleich mit MT5-Rechenfunktionen ...")
        cross = crosscheck.run(client, MarketStore(cfg.data_root))

    md, js, latest = report.write(cfg.report_dir, data, cross)
    log("\nErgebnis je Markt:")
    for sym, rk in data["rankings"].items():
        log(f"  {sym}: {rk['verdict']}")
        for r in rk["ranking"][:5]:
            t = r["test"]
            log(f"    {r['rank']}. {r['strategy']} {r['tf']} (Score {r['score']}, "
                f"{'qualifiziert' if r['qualified'] else 'nicht qualifiziert'}) – Test: {t['trades']} Trades, "
                f"{t['net_profit_eur']} €, PF {t['profit_factor']}, DD {t['max_drawdown_pct']} %")
    log(f"\nTesteinheiten: {data['units_completed']} von {data['units_expected']} abgeschlossen")
    if data["units_missing"]:
        log(f"FEHLEND ({len(data['units_missing'])}): {data['units_missing']}")
        log("Erneut starten setzt fort und rechnet nur die fehlenden Einheiten (ggf. mit --blockgroesse 50000).")
    if data["errors"]:
        log(f"\nACHTUNG: {len(data['errors'])} Fehler während des Laufs (siehe Bericht):")
        for e in data["errors"][:10]:
            log(f"  {e['symbol']} {e['tf']} {e.get('strategy') or ''}: {e['error']}")
    log(f"\nFertig in {(time.time() - t0) / 60:.1f} min.\nBericht: {md}\nErgebnisdatei: {latest}")
    return 1 if data["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
