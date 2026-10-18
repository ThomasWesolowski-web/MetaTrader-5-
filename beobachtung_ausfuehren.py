"""Phase 4 – Beobachtung (Paper-Trading) der Bitcoin-Trendfolge-Kandidaten mit neuen Live-Kursen.

NUR LESEND: Es werden keine Orders an MetaTrader 5 gesendet, kein Demo- und kein Echtgeldhandel.
Signale, hypothetische Ein- und Ausstiege, Kosten und Ergebnisse werden unter data/paper/ protokolliert.

Aufruf (Windows, MT5-Terminal geöffnet und eingeloggt):
    python beobachtung_ausfuehren.py                  # läuft dauerhaft, Durchlauf alle 60 s (Strg+C beendet)
    python beobachtung_ausfuehren.py --einmal         # ein Durchlauf, z. B. für die Windows-Aufgabenplanung
    python beobachtung_ausfuehren.py --intervall 120  # anderer Abstand zwischen den Durchläufen
    python beobachtung_ausfuehren.py --status         # gespeicherten Stand anzeigen (ohne MT5)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tradingbot.config import load_config
from tradingbot.paper import Observer, PaperStore


def show_status(cfg) -> int:
    store = PaperStore(cfg.data_root)
    status = store.read("status.json")
    trades = store.read("trades.json") or {}
    if not status:
        print("Noch keine Beobachtungsdaten vorhanden (data/paper/status.json fehlt).")
        return 0
    print(f"Letzter Durchlauf: {status['updated_utc'][:19]} UTC (Nr. {status['durchlauf']})")
    for sym, m in status["maerkte"].items():
        print(f"  {sym}: {m['zustand']} (Tick {m['tick_utc']}, Alter {m['tick_alter_s']} s)")
    for key, c in (trades.get("kandidaten") or {}).items():
        ml = c.get("mindestlot") or {}
        print(f"  {key}: seit {c['beobachtung_seit_utc'][:16]}, letzte Kerze {str(c['letzte_kerze_utc'])[:16]}, "
              f"{len(c['signale'])} Signale, {sum(t['status'] == 'offen' for t in c['trades'])} offen, "
              f"{ml.get('trades', 0)} abgeschlossen, Netto je 0,01 Lot {ml.get('netto_eur', '–')} €")
    for ev in store.events(limit=10):
        print(f"    {ev.get('utc', '')[:19]}  {ev.get('art')}  {ev.get('kandidat') or ev.get('symbol') or ''}")
    return 0


def main(argv=None, client=None, now_utc_fn=None, sleep=None) -> int:
    ap = argparse.ArgumentParser(description="Phase 4 – Beobachtung (Paper-Trading, keine Orders)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.toml"))
    ap.add_argument("--einmal", action="store_true", help="nur einen Durchlauf ausführen und beenden")
    ap.add_argument("--intervall", type=int, help="Sekunden zwischen zwei Durchläufen (Standard aus config.toml: 60)")
    ap.add_argument("--status", action="store_true", help="gespeicherten Stand anzeigen, ohne MT5-Verbindung")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.status:
        return show_status(cfg)
    if args.intervall is not None and args.intervall < 10:
        ap.error("--intervall muss mindestens 10 Sekunden betragen")

    print("Phase 4 – Beobachtung (Paper-Trading): keine Orders, kein Demo- oder Echtgeldhandel")
    print(f"Kandidaten: {', '.join(c.key for c in cfg.paper.candidates)}")
    print(f"Ablage: {Path(cfg.data_root) / 'paper'}")
    if client is None:
        from tradingbot.mt5_client import MT5Client
        client = MT5Client(terminal_path=cfg.terminal_path)
    obs = Observer(cfg, client, now_utc_fn=now_utc_fn)
    try:
        obs.start()
        kw = {"sleep": sleep} if sleep else {}
        obs.run(args.intervall, max_cycles=1 if args.einmal else None, **kw)
    except KeyboardInterrupt:
        print("\nBeendet (Strg+C). Der Stand ist gespeichert; ein Neustart setzt die Beobachtung fort.")
    finally:
        obs.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
