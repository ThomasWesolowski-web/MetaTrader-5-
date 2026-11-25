"""Day-Trader-Skills für MetaTrader 5 – Befehle für Claude (.claude/skills/mt5-*) und zum Aufruf von Hand.

Ablauf für einen Trade:
    python daytrader.py scannen                           # Setups in den Symbolen aus daytrader.toml
    python daytrader.py planen XAUUSD                     # Einstieg, Stop, Ziel, Glattstellzeit -> plan.json
    python daytrader.py zeitmaschine                      # 10 000 Durchläufe + Geschichte des Plans
    python daytrader.py ticket                            # Größe in Lot + Risikoprüfung: PASS oder BLOCK
    python daytrader.py freigeben --antwort "ja"          # das Ja des Nutzers, wörtlich
    python daytrader.py senden                            # Order an MT5 – NUR Demokonto
Solange offen:
    python daytrader.py status                            # Konto, Positionen, Hinweise zur Glattstellzeit
    python daytrader.py schliessen --ticket 12345         # Day-Trader-Position zum Marktpreis schließen

Optionen für alle Befehle: --ohne-mt5 (nur Datenbank, keine Verbindung), --jetzt "2026-11-23 15:45" (deutsche Zeit,
Uhr festlegen), --json (Ausgabe als JSON), --config / --daytrader-config.
Kein Finanzrat. Orders ausschließlich über tradingbot/execution.py und nur auf einem Demokonto.
"""

from __future__ import annotations

import argparse
import json
import sys

from tradingbot.config import load_config
from tradingbot.daytrader import plan as planmod
from tradingbot.daytrader import scan as scanmod
from tradingbot.daytrader import ticket as ticketmod
from tradingbot.daytrader import timemachine as tm
from tradingbot.daytrader.config import load_daytrader_config
from tradingbot.daytrader.data import Context, parse_now

NEEDS_MT5 = {"senden", "schliessen"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Day-Trader-Skills für MetaTrader 5 (nur Demokonto)")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--daytrader-config", default="daytrader.toml")
    ap.add_argument("--ohne-mt5", action="store_true", help="nur lokale Datenbank, keine Verbindung zu MT5")
    ap.add_argument("--jetzt", help='Uhrzeit festlegen, deutsche Zeit, z. B. "2026-11-23 15:45"')
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scannen")
    s.add_argument("symbole", nargs="*")
    p = sub.add_parser("planen")
    p.add_argument("symbol")
    p.add_argument("--setup", default="orb", choices=["orb", "vortag", "rsi", "frei"])
    p.add_argument("--richtung", choices=["long", "short"])
    p.add_argument("--stop", choices=list(planmod.STOP_RULES))
    p.add_argument("--atr-mult", type=float, default=1.5)
    p.add_argument("--ziel", type=float, default=1.5, help="Ziel in R (Vielfaches des Risikos)")
    p.add_argument("--einstieg", type=float)
    z = sub.add_parser("zeitmaschine")
    z.add_argument("--laeufe", type=int, default=10_000)
    z.add_argument("--seed", type=int, default=19)
    z.add_argument("--fenster", type=int, default=2)
    z.add_argument("--tage", type=int, default=60)
    z.add_argument("--block", type=int, default=6, help="Kerzen je Block (1 = einzeln gezogen wie früher)")
    t = sub.add_parser("ticket")
    t.add_argument("--kontostand", type=float, help="nur Vorschau ohne MT5")
    f = sub.add_parser("freigeben")
    f.add_argument("--antwort", required=True, help="die Antwort des Nutzers, wörtlich")
    sub.add_parser("senden")
    c = sub.add_parser("schliessen")
    c.add_argument("--ticket", type=int, required=True)
    sub.add_parser("status")
    return ap


def run(argv=None, client=None) -> int:
    a = build_parser().parse_args(argv)
    cfg = load_config(a.config)
    dt = load_daytrader_config(a.daytrader_config)
    if client is None and not a.ohne_mt5:
        try:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
            client.connect()
        except Exception as e:  # kein Windows, kein Terminal, nicht eingeloggt
            if a.cmd in NEEDS_MT5:
                print(f"MT5 nicht erreichbar: {e}", file=sys.stderr)
                return 2
            print(f"Hinweis: MT5 nicht verbunden ({type(e).__name__}) – nur lokale Datenbank", file=sys.stderr)
            client = None
    ctx = Context(cfg, dt, client, parse_now(a.jetzt))
    try:
        if a.cmd == "scannen":
            res, text = scanmod.scan(ctx, a.symbole or None), None
            text = scanmod.format_scan(res)
        elif a.cmd == "planen":
            d = {"long": 1, "short": -1}.get(a.richtung)
            res = planmod.make_plan(ctx, a.symbol, a.setup, d, a.stop, a.atr_mult, a.ziel, a.einstieg)
            text = planmod.format_plan(res)
        elif a.cmd == "zeitmaschine":
            res = tm.run(ctx, None, a.laeufe, a.seed, a.fenster, a.tage, a.block)
            text = tm.format_run(res)
        elif a.cmd == "ticket":
            res = ticketmod.build_ticket(ctx, a.kontostand)
            text = ticketmod.format_ticket(res)
        elif a.cmd == "freigeben":
            res = ticketmod.approve(ctx, a.antwort)
            text = res["meldung"]
        elif a.cmd == "senden":
            res = ticketmod.send(ctx)
            text = (f"Order gesendet (Demo): Ticket {res['ticket']} @ {res['kurs']}" if res["ok"]
                    else f"NICHT gesendet: {res['meldung']}")
        elif a.cmd == "schliessen":
            res = ticketmod.close(ctx, a.ticket)
            text = f"Geschlossen @ {res['kurs']}" if res["ok"] else f"NICHT geschlossen: {res['meldung']}"
        else:
            res = ticketmod.status(ctx)
            text = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    except (RuntimeError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            try:
                client.shutdown()
            except Exception:
                pass
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str) if a.json else text)
    return 0 if (not isinstance(res, dict) or res.get("ok", True)) else 1


if __name__ == "__main__":
    sys.exit(run())
