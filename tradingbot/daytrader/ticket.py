"""Positionsgröße, Risikoprüfung, Freigabe und Orderversand der Day-Trader-Skills.

Eine Order erreicht MT5 nur über drei getrennte Hürden, jedes Mal, für jede Order:
  1. ticket:     die Risikoprüfung ergibt PASS für genau dieses Ticket (Fingerabdruck der Orderdaten)
  2. freigeben:  der Nutzer schreibt ein ausdrückliches "ja" für genau dieses Ticket (wörtlich protokolliert)
  3. senden:     Claude Code fragt vor dem Befehl "senden" noch einmal nach (.claude/settings.json, "ask")
Dazu prüft tradingbot/execution.py vor dem Senden selbst, dass ein DEMOKONTO eingeloggt ist.
Ein Ja gilt für eine Order. Kein "ja zu allem", keine Orders aus geplanten Hintergrundaufgaben.
"""

from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd

from ..backtest.costs import scenarios_for
from ..execution import DEMO, MAGIC, MAGIC_DAYTRADER, close_manual_position, send_manual_order
from ..instruments import CurrencyConverter, InstrumentSpec, conversion_sources, round_volume_down
from ..paper import PaperStore
from .data import Context, round_price, session_day

YES = {"ja", "yes", "jawohl", "jep", "jo"}
NO_WORDS = {"nein", "no", "nicht", "kein", "keine", "alle", "allem", "alles", "all", "immer", "vielleicht",
            "später", "spaeter", "warte", "stopp", "stop", "abbrechen", "jede", "jeden"}


def fingerprint(order: dict) -> str:
    keys = ("symbol", "richtung", "volumen", "stop", "ziel", "tag")
    return hashlib.sha256(json.dumps({k: order[k] for k in keys}, sort_keys=True).encode()).hexdigest()[:16]


def _today_trades(ctx: Context, day: str) -> list[dict]:
    return [e for e in ctx.events() if e.get("art") == "order_gesendet" and e.get("tag") == day]


def _rate(ctx: Context, spec: dict, account_ccy: str) -> float:
    if spec["currency_profit"] == account_ccy:
        return 1.0
    conv = CurrencyConverter()
    if ctx.live:
        for c in conversion_sources(ctx.doc):
            t = ctx.client.tick(c["symbol"])
            if t and t.get("bid") and t.get("ask"):
                conv.add_live(c["symbol"], c["base"], c["quote"], (t["bid"] + t["ask"]) / 2)
    if not conv.rates:
        conv = CurrencyConverter.from_store(ctx.market, conversion_sources(ctx.doc), start=ctx.now() - pd.Timedelta(days=5))
    try:
        r = float(conv.rate(spec["currency_profit"], account_ccy, [ctx.now()])[0])
    except KeyError:
        r = float("nan")
    return r


def build_ticket(ctx: Context, balance: float | None = None, currency: str | None = None) -> dict:
    """Größe aus dem Risiko berechnen und alle Grenzen prüfen. Schreibt ticket.json (PASS oder BLOCK)."""
    plan = ctx.read("plan.json")
    if not plan:
        raise RuntimeError("Kein Plan – zuerst planen")
    lim = ctx.dt.risk
    sym, d = plan["symbol"], int(plan["richtung"])
    sess = ctx.dt.session(sym)
    spec_d = ctx.spec(sym)
    spec = InstrumentSpec.from_dict(spec_d)
    now = ctx.now()
    blocks, warns = [], []

    acc = {}
    if ctx.live:
        acc = ctx.client.account_info()
        if acc.get("trade_mode") != DEMO:
            blocks.append("kein Demokonto eingeloggt – Orders nur auf dem Demokonto")
        mode = {0: "DEMO", 1: "WETTBEWERB", 2: "ECHTGELD"}.get(acc.get("trade_mode"), str(acc.get("trade_mode")))
    else:
        mode = "VORSCHAU (MT5 nicht verbunden)"
        blocks.append("MT5 nicht verbunden – nur Vorschau, senden nicht möglich")
        acc = {"balance": balance or 1000.0, "currency": currency or (ctx.doc.get("account") or {}).get("currency", "EUR"),
               "margin_free": None}
    bal, ccy = float(acc["balance"]), acc["currency"]

    entry, stop, target = float(plan["einstieg"]), float(plan["stop"]), float(plan["ziel"])
    tick = ctx.tick(sym)
    price = float(tick["ask"] if d == 1 else tick["bid"]) if tick else entry
    risk_px = d * (price - stop)
    if not stop or risk_px <= 0:
        blocks.append(f"Kurs {price} liegt schon jenseits des Stops {stop}")
    plan_risk = d * (entry - stop)
    if tick and plan_risk > 0 and abs(price - entry) > lim.max_drift_r * plan_risk:
        blocks.append(f"Kurs seit dem Plan um {abs(price - entry):.{spec.digits}f} gelaufen "
                      f"(> {lim.max_drift_r:g} × Risiko) – neu planen")
    age = (now - pd.Timestamp(plan["erstellt_utc"])).total_seconds() / 60
    if age > lim.max_plan_age_min:
        blocks.append(f"Plan ist {age:.0f} Minuten alt (> {lim.max_plan_age_min}) – neu planen")
    rr = d * (target - price) / risk_px if risk_px > 0 else 0.0
    if rr < lim.min_rr:
        blocks.append(f"Chance/Risiko {rr:.2f} unter dem Minimum {lim.min_rr:g}")
    day, now_min = session_day(now, sess)
    if str(day.date()) != plan["tag"]:
        blocks.append(f"Plan ist vom {plan['tag']} – neu planen")
    if not (sess.start <= now_min <= sess.entry_until):
        blocks.append(f"außerhalb des Einstiegsfensters {sess.hhmm(sess.start)}–{sess.hhmm(sess.entry_until)} "
                      f"({sess.tz}), jetzt {sess.hhmm(now_min)}")

    rate = _rate(ctx, spec_d, ccy)
    costs = scenarios_for(sym)[ctx.dt.cost_scenario]
    risk_money = bal * lim.risk_pct / 100
    vol, loss_per_lot = 0.0, float("nan")
    if not np.isfinite(rate) or rate <= 0:
        blocks.append(f"kein Umrechnungskurs {spec.currency_profit}->{ccy}")
    elif risk_px > 0:
        comm_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * price * spec.contract_size) * rate
        loss_per_lot = risk_px * spec.contract_size * rate + comm_lot
        vol = round_volume_down(spec, risk_money / loss_per_lot)
        if vol <= 0:
            blocks.append(f"Mindestlot {spec.volume_min:g} würde {loss_per_lot * spec.volume_min:.2f} {ccy} riskieren "
                          f"(erlaubt {risk_money:.2f} {ccy}) – Stop enger oder Risiko höher einstellen")
    risk_trade = vol * loss_per_lot if vol > 0 else 0.0

    trades = _today_trades(ctx, plan["tag"])
    if len(trades) >= lim.max_trades_per_day:
        blocks.append(f"heute schon {len(trades)} Day-Trades (Grenze {lim.max_trades_per_day})")
    used = sum(float(t.get("risiko", 0)) for t in trades)
    if used + risk_trade > bal * lim.max_daily_loss_pct / 100 + 1e-9:
        blocks.append(f"Tagesrisiko {used + risk_trade:.2f} {ccy} über der Grenze "
                      f"{bal * lim.max_daily_loss_pct / 100:.2f} {ccy} ({lim.max_daily_loss_pct:g} %)")
    if ctx.live:
        mine = [p for p in (ctx.client.mt5.positions_get() or ()) if getattr(p, "magic", None) == MAGIC_DAYTRADER]
        if len(mine) >= lim.max_positions:
            blocks.append(f"bereits {len(mine)} Day-Trader-Position(en) offen (Grenze {lim.max_positions})")
        if vol > 0:
            otype = ctx.client.mt5.ORDER_TYPE_BUY if d == 1 else ctx.client.mt5.ORDER_TYPE_SELL
            margin = ctx.client.mt5.order_calc_margin(otype, sym, vol, price)
            if margin is None or margin > float(acc.get("margin_free") or 0):
                blocks.append(f"nicht genug freie Margin ({margin} benötigt, {acc.get('margin_free')} frei)")
    bot = (PaperStore(ctx.cfg.data_root).state().get("execution") or {})
    if bot.get("halted"):
        blocks.append(f"Handelsstopp des Bots aktiv: {bot.get('halt_reason')}")
    if spec.stops_level and risk_px <= spec.stops_level * spec.point:
        blocks.append("Stop näher als der Mindestabstand des Brokers")
    if now_min > sess.entry_until - 30 and not blocks:
        warns.append(f"wenig Zeit bis zur Glattstellzeit {sess.hhmm(sess.flat_by)}")

    order = {"symbol": sym, "richtung": d, "volumen": vol, "stop": round_price(stop, spec.digits),
             "ziel": round_price(target, spec.digits), "tag": plan["tag"]}
    t = {"order": order, "fingerabdruck": fingerprint(order), "urteil": "BLOCK" if blocks else "PASS",
         "gruende": blocks, "hinweise": warns, "konto": {"modus": mode, "kontostand": bal, "waehrung": ccy},
         "kurs_jetzt": round_price(price, spec.digits), "chance_risiko": round(rr, 2), "risiko": round(risk_trade, 2),
         "risiko_erlaubt": round(risk_money, 2), "verlust_je_lot": round(loss_per_lot, 2) if vol else None,
         "umrechnung": rate, "tagesrisiko_bisher": round(used, 2), "trades_heute": len(trades),
         "glattstellen": f"{sess.hhmm(sess.flat_by)} {sess.tz}", "erstellt_utc": now.isoformat(), "freigabe": None,
         "gesendet": None, "digits": spec.digits}
    ctx.write("ticket.json", t)
    ctx.log("ticket", fingerabdruck=t["fingerabdruck"], urteil=t["urteil"], gruende=blocks, **order)
    return t


def format_ticket(t: dict) -> str:
    o = t["order"]
    side = "KAUF" if o["richtung"] == 1 else "VERKAUF"
    lines = [f"TICKET {t['fingerabdruck']} – {t['konto']['modus']}, Konto {t['konto']['kontostand']:.2f} {t['konto']['waehrung']}",
             f"  {side} {o['volumen']:g} Lot {o['symbol']} zum Marktpreis (jetzt {t['kurs_jetzt']})",
             f"  Stop {o['stop']} · Ziel {o['ziel']} · Chance/Risiko {t['chance_risiko']:.2f}",
             f"  Verlust bis zum Stop ca. {t['risiko']:.2f} {t['konto']['waehrung']} inkl. Kommission "
             f"(erlaubt {t['risiko_erlaubt']:.2f}); kann bei schnellem Markt größer sein",
             f"  glattstellen bis {t['glattstellen']}; heute {t['trades_heute']} Day-Trade(s), Tagesrisiko bisher "
             f"{t['tagesrisiko_bisher']:.2f}"]
    if t["urteil"] == "PASS":
        lines.append("  PASS – wartet auf ein ausdrückliches \"ja\" für genau dieses Ticket")
    else:
        lines.append("  BLOCK:")
        lines += [f"    - {g}" for g in t["gruende"]]
    lines += [f"  Hinweis: {h}" for h in t.get("hinweise", [])]
    return "\n".join(lines)


def check_reply(reply: str, t: dict) -> tuple[bool, str]:
    """Ist die Antwort ein ausdrückliches Ja für genau dieses Ticket? Nie großzügig auslegen."""
    words = re.findall(r"[a-zäöüß]+|\d+(?:[.,]\d+)?", reply.lower())
    if not words or not (set(words) & YES):
        return False, "kein ausdrückliches \"ja\""
    if set(words) & NO_WORDS or any(w.startswith(("all", "jed", "beide")) for w in words):
        return False, "die Antwort enthält eine Einschränkung oder ein \"alle/nein\""
    if "?" in reply:
        return False, "die Antwort ist eine Frage"
    o = t["order"]
    syms = {w for w in re.findall(r"\b[A-Z][A-Z0-9]{4,}\b", reply) if sum(ch.isalpha() for ch in w) >= 3}
    if syms - {o["symbol"]}:
        return False, f"die Antwort nennt ein anderes Symbol ({', '.join(sorted(syms - {o['symbol']}))})"
    allowed = {o["volumen"], o["stop"], o["ziel"], float(t["kurs_jetzt"])}
    for w in words:
        if w[0].isdigit():
            v = float(w.replace(",", "."))
            if not any(abs(v - a) < 1e-9 for a in allowed):
                return False, f"die Zahl {w} passt nicht zum Ticket"
    return True, "ok"


def approve(ctx: Context, reply: str) -> dict:
    t = ctx.read("ticket.json")
    if not t:
        raise RuntimeError("Kein Ticket – zuerst ticket")
    if t["urteil"] != "PASS":
        return {"ok": False, "meldung": "Ticket ist BLOCK – nicht freigebbar"}
    if t.get("gesendet"):
        return {"ok": False, "meldung": "Ticket wurde schon gesendet – ein Ja gilt für eine Order"}
    ok, why = check_reply(reply, t)
    ctx.log("freigabe" if ok else "freigabe_abgelehnt", fingerabdruck=t["fingerabdruck"], antwort=reply, grund=why)
    if not ok:
        return {"ok": False, "meldung": f"Nicht erfasst: {why}. Bitte erneut fragen."}
    t["freigabe"] = {"antwort": reply, "utc": ctx.now().isoformat(), "fingerabdruck": t["fingerabdruck"]}
    ctx.write("ticket.json", t)
    return {"ok": True, "meldung": f"Ja erfasst für Ticket {t['fingerabdruck']}"}


def send(ctx: Context) -> dict:
    if not ctx.live:
        return {"ok": False, "meldung": "MT5 nicht verbunden"}
    t = ctx.read("ticket.json")
    if not t:
        return {"ok": False, "meldung": "kein Ticket"}
    now = ctx.now()
    fail = None
    if t["urteil"] != "PASS":
        fail = "Ticket ist nicht PASS"
    elif t.get("gesendet"):
        fail = "Ticket wurde schon gesendet"
    elif not t.get("freigabe"):
        fail = "kein Ja des Nutzers für dieses Ticket"
    elif t["freigabe"]["fingerabdruck"] != fingerprint(t["order"]) or t["fingerabdruck"] != fingerprint(t["order"]):
        fail = "Ticket wurde nach der Freigabe verändert"
    elif (now - pd.Timestamp(t["freigabe"]["utc"])).total_seconds() > 60 * ctx.dt.risk.approval_valid_min:
        fail = f"Ja ist älter als {ctx.dt.risk.approval_valid_min} Minuten – Ticket neu prüfen und neu fragen"
    elif (now - pd.Timestamp(t["erstellt_utc"])).total_seconds() > 60 * (ctx.dt.risk.approval_valid_min + 5):
        fail = "Risikoprüfung ist zu alt – Ticket neu erstellen"
    if fail:
        ctx.log("senden_abgelehnt", fingerabdruck=t["fingerabdruck"], grund=fail)
        return {"ok": False, "meldung": fail}
    o = t["order"]
    res = send_manual_order(ctx.client.mt5, o["symbol"], o["richtung"], o["volumen"], o["stop"], o["ziel"],
                            int(t.get("digits", 2)), ctx.dt.risk.deviation_points, f"DT {o['symbol']}")
    t["gesendet"] = {"utc": now.isoformat(), **{k: res.get(k) for k in ("ok", "meldung", "ticket", "kurs")}}
    ctx.write("ticket.json", t)
    if res["ok"]:
        ctx.log("order_gesendet", fingerabdruck=t["fingerabdruck"], risiko=t["risiko"], ticket=res["ticket"],
                kurs=res["kurs"], **o)
    else:
        ctx.log("order_fehlgeschlagen", fingerabdruck=t["fingerabdruck"], meldung=res["meldung"], **o)
    return res


def close(ctx: Context, ticket: int) -> dict:
    if not ctx.live:
        return {"ok": False, "meldung": "MT5 nicht verbunden"}
    res = close_manual_position(ctx.client.mt5, ticket, ctx.dt.risk.deviation_points)
    ctx.log("position_geschlossen" if res["ok"] else "schliessen_fehlgeschlagen", ticket=ticket, meldung=res["meldung"],
            kurs=res.get("kurs"))
    return res


def status(ctx: Context) -> dict:
    out = {"utc": ctx.now().isoformat(), "verbunden": ctx.live}
    if ctx.live:
        acc = ctx.client.account_info()
        out["konto"] = {"modus": {0: "DEMO", 1: "WETTBEWERB", 2: "ECHTGELD"}.get(acc.get("trade_mode")),
                        "kontostand": acc.get("balance"), "equity": acc.get("equity"), "waehrung": acc.get("currency")}
        pos = ctx.client.mt5.positions_get() or ()
        out["day_trader_positionen"] = [{"ticket": int(p.ticket), "symbol": p.symbol,
                                         "richtung": "Long" if int(p.type) == 0 else "Short", "volumen": p.volume,
                                         "einstieg": p.price_open, "stop": p.sl, "ziel": p.tp,
                                         "ergebnis": getattr(p, "profit", None)}
                                        for p in pos if getattr(p, "magic", None) == MAGIC_DAYTRADER]
        out["bot_positionen"] = sum(1 for p in pos if getattr(p, "magic", None) == MAGIC)
        out["andere_positionen"] = sum(1 for p in pos if getattr(p, "magic", None) not in (MAGIC, MAGIC_DAYTRADER))
        for p in out["day_trader_positionen"]:
            sess = ctx.dt.sessions.get(p["symbol"])
            if sess and session_day(ctx.now(), sess)[1] >= sess.flat_by:
                p["hinweis"] = f"Glattstellzeit {sess.hhmm(sess.flat_by)} ({sess.tz}) erreicht – schließen"
    bot = PaperStore(ctx.cfg.data_root).state().get("execution") or {}
    out["bot_handelsstopp"] = bot.get("halt_reason") if bot.get("halted") else None
    t = ctx.read("ticket.json")
    if t:
        out["letztes_ticket"] = {"fingerabdruck": t["fingerabdruck"], "urteil": t["urteil"],
                                 "freigegeben": bool(t.get("freigabe")), "gesendet": t.get("gesendet")}
    day = str(pd.Timestamp(ctx.now()).date())
    out["trades_heute_utc"] = len([e for e in ctx.events() if e.get("art") == "order_gesendet"
                                   and e.get("utc", "")[:10] == day])
    return out
