"""Telegram-Signale automatisch in MetaTrader 5 handeln (Beschreibung: docs/telegram_signale.md).

Liest den Kanal aus telegram.toml mit deinem eigenen Telegram-Account mit (Telethon) und handelt erkannte Signale
über tradingbot/execution.py – auf dem Demokonto oder, wenn in telegram.toml freigegeben, auf genau einem Live-Konto.

Aufruf (Windows, MT5 geöffnet und eingeloggt; am einfachsten TELEGRAM_START.bat):
    python telegram_signale.py                      # läuft dauerhaft (beim ersten Mal: Telefonnummer + Code)
    python telegram_signale.py --kanaele            # Kanäle deines Accounts anzeigen (Name/Nummer für telegram.toml)
    python telegram_signale.py --verlauf 50         # letzte 50 Nachrichten des Kanals prüfen, OHNE zu handeln
    python telegram_signale.py --pruefen "TEXT"     # einen Text prüfen (ohne Telegram und MT5)
    python telegram_signale.py --status             # gespeicherten Stand anzeigen
    python telegram_signale.py --handel-freigeben   # nach einem Drawdown-Stopp wieder handeln
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import pandas as pd

from tradingbot.config import load_config
from tradingbot.telegram import SignalStore, SignalTrader, load, parse_followup, parse_signal
from tradingbot.telegram.trader import RELEASE_FILE, STOP_FILE

ROOT = Path(__file__).resolve().parent
TICK_S = 5
RETRY_S = 60


def describe(text: str) -> str:
    sig = parse_signal(text)
    if sig:
        side = "KAUF" if sig.direction == 1 else "VERKAUF"
        zone = f"{sig.entry_lo}-{sig.entry_hi}" if sig.entry_lo is not None else "Marktpreis"
        return f"SIGNAL: {side} {sig.symbol}, Einstieg {zone}, Stop {sig.stop}, Ziel(e) {', '.join(map(str, sig.targets)) or 'offen'}"
    acts = parse_followup(text)
    if acts:
        return "FOLGENACHRICHT: " + ", ".join(a if v is None else f"{a} {v}" for a, v in acts)
    return "keine Handelsnachricht"


def _client(tcfg):
    try:
        from telethon import TelegramClient
    except ImportError:
        sys.exit("Das Paket telethon fehlt: .venv\\Scripts\\python.exe -m pip install telethon")
    api_id, api_hash = tcfg.credentials()
    sess = Path(tcfg.data_root) / "telegram" / "konto"
    sess.parent.mkdir(parents=True, exist_ok=True)
    # bei Netzwerk-Aussetzern unbegrenzt neu verbinden (alle 10 s), statt nach 5 Versuchen aufzugeben
    return TelegramClient(str(sess), api_id, api_hash, connection_retries=None, retry_delay=10, auto_reconnect=True)


async def find_channel(client, name: str):
    name = name.strip()
    if name.lstrip("-").isdigit():
        return await client.get_entity(int(name))
    if name.startswith("@"):
        return await client.get_entity(name)
    async for d in client.iter_dialogs():
        if (d.name or "").strip().lower() == name.lower():
            return d.entity
    async for d in client.iter_dialogs():
        if name.lower() in (d.name or "").lower():
            return d.entity
    raise SystemExit(f"Kanal '{name}' nicht gefunden. Mit --kanaele die Namen anzeigen und in telegram.toml eintragen.")


async def list_channels(tcfg) -> int:
    async with _client(tcfg) as client:
        async for d in client.iter_dialogs():
            if d.is_channel or d.is_group:
                print(f"{d.id:>16}  {d.name}")
    return 0


async def history(tcfg, n: int, out_path: str | None = None, full: bool = False) -> int:
    lines = []
    async with _client(tcfg) as client:
        ch = await find_channel(client, tcfg.channel)
        msgs = [m async for m in client.iter_messages(ch, limit=n)]
        for m in reversed(msgs):
            text = (m.message or "").strip()
            if not text:
                continue
            res = describe(text)
            lines.append(f"{m.date:%d.%m. %H:%M}  #{m.id}  {text.splitlines()[0][:70]}")
            if full or (res == "keine Handelsnachricht" and re.search(r"\b(?:BUY|SELL|TRADE|SIGNAL|LONG|SHORT)\b",
                                                                      text.upper())):
                lines += ["      | " + ln for ln in text.splitlines() if ln.strip()]
            lines.append(f"                → {res}")
    print("\n".join(lines))
    if out_path:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nGespeichert in {out_path}")
    return 0


async def run(tcfg, mt5_module) -> int:
    from telethon import events
    store = SignalStore(tcfg.data_root)
    trader = SignalTrader(mt5_module, tcfg, store)
    stop_file = store.dir / STOP_FILE
    stop_file.unlink(missing_ok=True)
    outbox: list[str] = []

    def log(text: str) -> None:
        print(f"{pd.Timestamp.now():%H:%M:%S}  {text}")
        if tcfg.notify:
            outbox.append(text.strip())

    async def flush(client) -> None:
        if not outbox:
            return
        text = "🤖 Trading-Bot\n" + "\n".join(outbox)
        outbox.clear()
        try:
            await client.send_message("me", text[:4000])      # "Gespeicherte Nachrichten" in deinem Telegram
        except Exception as e:                                 # noqa: BLE001 – Handel läuft weiter
            print(f"Benachrichtigung fehlgeschlagen: {e!r}")

    trader.log = log
    async with _client(tcfg) as client:
        ch = await find_channel(client, tcfg.channel)
        print(f"Kanal: {getattr(ch, 'title', ch)} – warte auf Signale (Strg+C beendet)")
        if tcfg.notify:
            acc = mt5_module.account_info()
            outbox.append(f"gestartet – Kanal {getattr(ch, 'title', ch)}, Konto {getattr(acc, 'login', '?')}, "
                          f"Kontostand {getattr(acc, 'balance', '?')} {getattr(acc, 'currency', '')}")
            k = trader.st["konto"]
            if k.get("gestoppt"):
                outbox.append(f"⚠️ HANDEL GESTOPPT ({k.get('grund')}) – es werden KEINE Signale gehandelt. "
                              f"Freigeben: .venv\\Scripts\\python.exe telegram_signale.py --handel-freigeben")
            await flush(client)

        @client.on(events.NewMessage(chats=ch))
        async def on_new(ev):
            m = ev.message
            reply_to = getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None)
            try:
                res = trader.on_message(m.id, m.message or "", m.date, reply_to)
                if res not in ("keine Handelsnachricht", "schon verarbeitet"):
                    print(f"{pd.Timestamp.now():%H:%M:%S}  #{m.id}: {res}")
            except Exception as e:                                  # noqa: BLE001 – weiterlaufen, protokollieren
                store.event("fehler", pd.Timestamp.now(tz="UTC"), nachricht=m.id, fehler=repr(e))
                log(f"FEHLER bei Nachricht #{m.id}: {e!r}")
            await flush(client)

        last_conn_check = 0.0
        while not stop_file.exists():
            if asyncio.get_running_loop().time() - last_conn_check > 60:      # Telegram-Verbindung prüfen
                last_conn_check = asyncio.get_running_loop().time()
                if not client.is_connected():
                    log("Telegram-Verbindung weg – verbinde neu ...")
                    try:
                        await client.connect()
                        await client.catch_up()
                        log("Telegram wieder verbunden")
                    except Exception as e:                      # noqa: BLE001 – nächster Versuch in 60 s
                        print(f"Neu verbinden fehlgeschlagen: {e!r}")
            try:
                trader.tick()
            except Exception as e:                                  # noqa: BLE001
                store.event("fehler", pd.Timestamp.now(tz="UTC"), fehler=repr(e))
                log(f"FEHLER im Durchlauf: {e!r}")
            await flush(client)
            await asyncio.sleep(TICK_S)
        stop_file.unlink(missing_ok=True)
        log("beendet (Stopp-Anforderung)")
        await flush(client)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Telegram-Signale automatisch in MT5 handeln")
    ap.add_argument("--config", default=str(ROOT / "telegram.toml"))
    ap.add_argument("--kanaele", action="store_true", help="Kanäle und Gruppen deines Telegram-Accounts anzeigen")
    ap.add_argument("--verlauf", type=int, metavar="N", help="letzte N Nachrichten des Kanals prüfen, ohne zu handeln")
    ap.add_argument("--ausgabe", metavar="DATEI", help="mit --verlauf: Ergebnis zusätzlich in diese Datei schreiben")
    ap.add_argument("--voll", action="store_true", help="mit --verlauf: alle Nachrichten mit vollem Text zeigen")
    ap.add_argument("--pruefen", metavar="TEXT", help="einen Nachrichtentext prüfen (ohne Telegram und MT5)")
    ap.add_argument("--status", action="store_true", help="gespeicherten Stand anzeigen")
    ap.add_argument("--handel-freigeben", action="store_true", help="nach einem Drawdown-Stopp wieder handeln")
    args = ap.parse_args(argv)
    if args.pruefen is not None:
        print(describe(args.pruefen))
        return 0
    cfg = load_config(str(ROOT / "config.toml"))
    tcfg = load(args.config, data_root=str(cfg.data_root))
    store = SignalStore(tcfg.data_root)
    if args.status:
        print("\n".join(SignalTrader(None, tcfg, store).status_lines()))
        return 0
    if args.handel_freigeben:
        store.dir.mkdir(parents=True, exist_ok=True)
        (store.dir / RELEASE_FILE).write_text("freigegeben\n", encoding="utf-8")
        print("Freigabe gespeichert – gilt beim nächsten Durchlauf.")
        return 0
    if args.kanaele:
        return asyncio.run(list_channels(tcfg))
    if args.verlauf:
        return asyncio.run(history(tcfg, args.verlauf, args.ausgabe, args.voll))

    from beobachtung_ausfuehren import single_instance
    lock = single_instance(store.dir / "telegram.lock")
    if lock is None:
        print("Die Telegram-Signale laufen bereits in einem anderen Fenster – kein zweiter Start.")
        return 3
    konto = (f"LIVE-Konto {tcfg.live_login} (feste {tcfg.lot:g} Lot)" if tcfg.live_login
             else "nur Demokonto")
    print(f"Telegram-Signale – Orders {'AN' if tcfg.orders_enabled else 'AUS (nur Protokoll)'}, {konto}")
    print(f"Grenzen: max. {tcfg.risk_pct:g} % je Trade, Tagesverlust {tcfg.max_daily_loss_pct:g} %, "
          f"Drawdown-Stopp {tcfg.max_drawdown_pct:g} %, max. {tcfg.max_positions} Positionen; "
          f"Folgenachrichten: {tcfg.followups}")
    from tradingbot.mt5_client import MT5Client, MT5Error
    client = MT5Client(terminal_path=cfg.terminal_path)
    import time
    while True:
        try:
            client.connect()
            acc = client.account_info()
            print(f"MT5: Konto {acc.get('login')} ({ {0: 'Demo', 1: 'Wettbewerb', 2: 'Echtgeld'}.get(acc.get('trade_mode'))}), "
                  f"Kontostand {acc.get('balance')} {acc.get('currency')}")
            break
        except MT5Error as e:
            print(f"MT5 noch nicht bereit: {e}\nNeuer Versuch in {RETRY_S} s (Strg+C beendet) ...")
            time.sleep(RETRY_S)
    try:
        return asyncio.run(run(tcfg, client.mt5))
    except KeyboardInterrupt:
        print("\nBeendet (Strg+C). Der Stand ist gespeichert.")
        return 0
    finally:
        client.shutdown()
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
