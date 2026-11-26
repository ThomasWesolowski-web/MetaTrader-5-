"""Gold-Bot: Order-Block-Strategie für XAUUSD (M15) automatisch auf dem DEMOKONTO (Beschreibung: docs/goldbot.md).

Aufruf (Windows, MT5 geöffnet und mit dem Demokonto eingeloggt; am einfachsten GOLDBOT_START.bat):
    python goldbot.py                      # läuft dauerhaft, Strg+C beendet
    python goldbot.py --analyse            # aktuelle Analyse zeigen (Trend, Order Blocks, Liquidität, Setup), ohne Orders
    python goldbot.py --rueckblick 60      # Regeln auf den letzten 60 Tagen M15 durchspielen, ohne Orders
    python goldbot.py --ki-test            # KI-Prüfung einmal mit dem aktuellen Chart ausprobieren
    python goldbot.py --status             # gespeicherten Stand anzeigen
    python goldbot.py --handel-freigeben   # nach einem Drawdown-Stopp wieder handeln
    python goldbot.py --alles-schliessen   # alle Gold-Bot-Orders und -Positionen sofort schließen
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from tradingbot.config import load_config
from tradingbot.goldbot import GoldBot, GoldStore, analyse, load
from tradingbot.goldbot.ki import make_checker
from tradingbot.goldbot.rueckblick import simulate, summary
from tradingbot.goldbot.strategie import Setup, to_frame
from tradingbot.goldbot.trader import RELEASE_FILE, STOP_FILE

ROOT = Path(__file__).resolve().parent
TICK_S = 10
RETRY_S = 60


def connect(gcfg, cfg):
    from tradingbot.mt5_client import MT5Client, MT5Error
    client = MT5Client(terminal_path=gcfg.terminal_path or cfg.terminal_path)
    while True:
        try:
            client.connect()
            acc = client.account_info()
            mode = {0: "Demo", 1: "Wettbewerb", 2: "ECHTGELD"}.get(acc.get("trade_mode"))
            print(f"MT5: Konto {acc.get('login')} ({mode}), {acc.get('server')}, Kontostand {acc.get('balance')} "
                  f"{acc.get('currency')}")
            if acc.get("trade_mode") != 0:
                print("  ACHTUNG: kein Demokonto – der Gold-Bot analysiert nur und sendet KEINE Orders.\n"
                      "  Für ein zweites MT5 mit dem Demokonto in goldbot.toml terminal_pfad eintragen.")
            return client
        except MT5Error as e:
            print(f"MT5 noch nicht bereit: {e}\nNeuer Versuch in {RETRY_S} s (Strg+C beendet) ...")
            time.sleep(RETRY_S)


def history(mt5, symbol: str, days: int) -> pd.DataFrame:
    bars = int(days * 24 * 4) + 700
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 1, bars)
    if rates is None or len(rates) == 0:
        raise SystemExit(f"Keine M15-Kerzen für {symbol}: {mt5.last_error()}")
    return to_frame(rates)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gold-Bot (Order-Block-Strategie, nur Demokonto)")
    ap.add_argument("--config", default=str(ROOT / "goldbot.toml"))
    ap.add_argument("--analyse", action="store_true", help="aktuelle Analyse zeigen, ohne Orders")
    ap.add_argument("--rueckblick", type=int, metavar="TAGE", help="Regeln auf vergangenen Tagen durchspielen")
    ap.add_argument("--ki-test", action="store_true", help="KI-Prüfung einmal ausprobieren")
    ap.add_argument("--status", action="store_true", help="gespeicherten Stand anzeigen")
    ap.add_argument("--handel-freigeben", action="store_true", help="nach einem Drawdown-Stopp wieder handeln")
    ap.add_argument("--alles-schliessen", action="store_true", help="alle Gold-Bot-Orders/-Positionen schließen")
    ap.add_argument("--einmal", action="store_true", help="nur einen Durchlauf")
    args = ap.parse_args(argv)

    cfg = load_config(str(ROOT / "config.toml"))
    gcfg = load(args.config, data_root=str(cfg.data_root))
    store = GoldStore(gcfg.data_root)
    if args.status:
        print("\n".join(GoldBot(None, gcfg, store).status_lines()))
        return 0
    if args.handel_freigeben:
        store.dir.mkdir(parents=True, exist_ok=True)
        (store.dir / RELEASE_FILE).write_text("freigegeben\n", encoding="utf-8")
        print("Freigabe gespeichert – gilt beim nächsten Durchlauf.")
        return 0

    if not (args.analyse or args.rueckblick or args.ki_test or args.alles_schliessen):
        if not gcfg.active:
            print("Gold-Bot ist ausgeschaltet (goldbot.toml: aktiv = false).")
            return 0
        from beobachtung_ausfuehren import single_instance
        lock = single_instance(store.dir / "goldbot.lock")
        if lock is None:
            print("Der Gold-Bot läuft bereits in einem anderen Fenster – kein zweiter Start.")
            return 3

    client = connect(gcfg, cfg)
    mt5 = client.mt5
    try:
        if args.analyse:
            an = analyse(history(mt5, gcfg.symbol, 3).tail(gcfg.bars), gcfg)
            print("\n".join(an.lines()))
            return 0
        if args.rueckblick:
            df = history(mt5, gcfg.symbol, args.rueckblick)
            print(f"Rückblick über {len(df)} M15-Kerzen ({pd.Timestamp(int(df.time.iloc[0]), unit='s'):%d.%m.%Y} bis "
                  f"{pd.Timestamp(int(df.time.iloc[-1]), unit='s'):%d.%m.%Y}), {gcfg.parts} x {gcfg.lot:g} Lot ...")
            trades = simulate(df, gcfg, progress=lambda i, n: print(f"  {i}/{n} Kerzen", end="\r"))
            print("\n".join(summary(trades, mt5.account_info().currency)))
            return 0
        if args.ki_test:
            gcfg.ai_enabled = True
            check = make_checker(gcfg)
            df = history(mt5, gcfg.symbol, 3).tail(gcfg.bars).reset_index(drop=True)
            an = analyse(df, gcfg)
            print("\n".join(an.lines()))
            s = an.setups[0] if an.setups else Setup(
                "A", -1 if an.trend <= 0 else 1, round(an.close + an.atr, 2), round(an.close + an.atr, 2),
                round(an.close + 2.5 * an.atr, 2), round(an.close - 1.5 * an.atr, 2), round(an.close - 3 * an.atr, 2),
                an.atr, "test", an.time, reason="TEST – kein echtes Setup, nur zum Ausprobieren der Verbindung")
            ok, text = check(s, df, an)
            print(f"\nKI: {'JA' if ok else 'NEIN'} – {text}")
            return 0
        bot = GoldBot(mt5, gcfg, store)
        if args.alles_schliessen:
            bot.close_all(pd.Timestamp.now(tz="UTC"))
            bot.save()
            return 0
        bot.ai = make_checker(gcfg)
        stop_file = store.dir / STOP_FILE
        stop_file.unlink(missing_ok=True)
        print(f"Gold-Bot {gcfg.symbol} M15 – Orders {'AN (nur Demo)' if gcfg.orders_enabled else 'AUS'}, "
              f"{gcfg.parts} x {gcfg.lot:g} Lot, Risiko max. {gcfg.max_risk_pct:g} % je Trade, Plan A "
              f"{'an' if gcfg.plan_a else 'aus'}, Plan B {'an' if gcfg.plan_b else 'aus'}, KI-Prüfung "
              f"{'an' if bot.ai else 'aus'}")
        bot.say(f"gestartet – Konto {mt5.account_info().login}, {gcfg.parts} x {gcfg.lot:g} Lot, KI-Prüfung "
                f"{'an' if bot.ai else 'aus'}")
        while not stop_file.exists():
            try:
                an = bot.tick()
                if an is not None:
                    print(f"{pd.Timestamp.now():%H:%M:%S}  " + an.lines()[0])
            except Exception as e:                                  # noqa: BLE001 – weiterlaufen, protokollieren
                store.event("fehler", pd.Timestamp.now(tz="UTC"), fehler=repr(e))
                print(f"{pd.Timestamp.now():%H:%M:%S}  FEHLER: {e!r}")
            if args.einmal:
                break
            time.sleep(TICK_S)
        stop_file.unlink(missing_ok=True)
        print("Beendet. Offene Orders/Positionen behalten Stop und Ziel beim Broker.")
        return 0
    except KeyboardInterrupt:
        print("\nBeendet (Strg+C). Offene Orders/Positionen behalten Stop und Ziel beim Broker.")
        return 0
    finally:
        client.shutdown()


if __name__ == "__main__":
    sys.exit(main())
