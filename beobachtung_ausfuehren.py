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
import time
from pathlib import Path

from tradingbot.config import load_config
from tradingbot.execution import RELEASE_FILE
from tradingbot.mt5_client import MT5Error
from tradingbot.paper import Observer, PaperStore
from tradingbot.paper.observer import STOP_FILE

RETRY_S = 60


def single_instance(lock_path: Path):
    """Verhindert, dass die Beobachtung zweimal gleichzeitig läuft. Gibt den offenen Sperr-Handle zurück
    (bleibt bis Programmende offen) oder None, wenn bereits eine Beobachtung läuft. Das Betriebssystem gibt
    die Sperre auch bei einem Absturz frei."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


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
    h = status.get("handel") or {}
    print(f"  Handel (Demo): {'aktiv' if h.get('aktiv') else 'aus'} – {h.get('grund')}"
          + (f" – GESTOPPT: {h.get('stopp_grund')}" if h.get("gestoppt") else "")
          + (f" – Tagesgrenze erreicht" if h.get("tageslimit_erreicht") else "")
          + (f", offene Positionen {h.get('offene_positionen')}, Drawdown {h.get('drawdown_pct')} %" if h.get("aktiv") else ""))
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
    ap.add_argument("--handel-freigeben", action="store_true",
                    help="nach einem Handelsstopp (20 %% Drawdown) den Demo-Handel wieder freigeben")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.status:
        return show_status(cfg)
    if args.handel_freigeben:
        f = Path(cfg.data_root) / "paper" / RELEASE_FILE
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("freigegeben\n", encoding="utf-8")
        print("Freigabe gespeichert: Der Demo-Handel läuft beim nächsten Durchlauf der Beobachtung wieder "
              "(Drawdown wird ab dem aktuellen Kontostand neu gemessen).")
        return 0
    if args.intervall is not None and args.intervall < 10:
        ap.error("--intervall muss mindestens 10 Sekunden betragen")

    lock = single_instance(Path(cfg.data_root) / "paper" / "beobachtung.lock")
    if lock is None:
        print("Die Beobachtung läuft bereits in einem anderen Fenster – kein zweiter Start.")
        return 3
    (Path(cfg.data_root) / "paper" / STOP_FILE).unlink(missing_ok=True)   # alte Stopp-Anforderung verwerfen
    print("Beobachtung und Signale" + (" – Orders NUR auf dem Demokonto ([execution] orders_enabled = true)"
                                       if cfg.execution.orders_enabled else " – keine Orders"))
    print(f"Kandidaten: {', '.join(c.key for c in cfg.paper.candidates)}")
    print(f"Ablage: {Path(cfg.data_root) / 'paper'}")
    if client is None:
        from tradingbot.mt5_client import MT5Client
        client = MT5Client(terminal_path=cfg.terminal_path)
    obs = Observer(cfg, client, now_utc_fn=now_utc_fn)
    wait = sleep or time.sleep
    try:
        while True:
            try:
                obs.start()
                break
            except MT5Error as e:   # Terminal noch nicht bereit (z. B. direkt nach dem Windows-Start)
                if args.einmal:
                    raise
                print(f"MT5 noch nicht bereit: {e}\nNeuer Versuch in {RETRY_S} s (Strg+C beendet) ...")
                wait(RETRY_S)
                if obs.stop_requested():
                    return 0
        kw = {"sleep": sleep} if sleep else {}
        obs.run(args.intervall, max_cycles=1 if args.einmal else None, **kw)
    except KeyboardInterrupt:
        print("\nBeendet (Strg+C). Der Stand ist gespeichert; ein Neustart setzt die Beobachtung fort.")
    finally:
        obs.close()
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
