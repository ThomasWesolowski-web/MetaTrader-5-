"""Einstieg und Ausstieg planen: Einstieg, Stop, Ziel und Glattstellzeit festlegen, bevor Geld riskiert wird.

Stop-Regeln:  or-mitte (Mitte der Opening Range), or-gegenseite (anderes Ende der Range), atr (x ATR(14)).
Ziel:         Vielfaches des Risikos (R), z. B. 1,5 = 1,5 x Abstand Einstieg–Stop.
Einstieg:     Long zum Ask, Short zum Bid (live aus MT5); ohne MT5 der letzte Schlusskurs (+ Spread bei Long).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..timeframes import seconds
from .data import Context, local_frame, round_price, session_day
from .scan import orb_state

STOP_RULES = ("or-mitte", "or-gegenseite", "atr")


def make_plan(ctx: Context, symbol: str, setup: str = "orb", direction: int | None = None, stop_rule: str | None = None,
              atr_mult: float = 1.5, target_r: float = 1.5, entry: float | None = None) -> dict:
    sess = ctx.dt.session(symbol)
    spec = ctx.spec(symbol)
    digits, point = int(spec["digits"]), float(spec["point"])
    tf_min = seconds(ctx.dt.timeframe) // 60
    bars = ctx.bars(symbol, days=10)
    if bars.empty:
        raise RuntimeError(f"Keine Kerzen für {symbol}")
    lf = local_frame(bars, sess)
    today, now_min = session_day(ctx.now(), sess)
    orb = orb_state(lf[lf["day"] == today], sess, tf_min, now_min, digits) if (lf["day"] == today).any() else {}
    a = ind.atr(bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float), 14)
    atr = float(a[-1]) if len(a) and np.isfinite(a[-1]) else None

    if direction is None:
        if setup == "orb" and orb.get("zustand") == "ausgelöst":
            direction = int(orb["richtung"])
        else:
            raise ValueError("Richtung angeben (--richtung long|short): kein ausgelöstes Opening-Range-Signal")
    stop_rule = stop_rule or ("or-mitte" if setup == "orb" else "atr")
    if stop_rule not in STOP_RULES:
        raise ValueError(f"Stop-Regel '{stop_rule}' unbekannt (erlaubt: {', '.join(STOP_RULES)})")

    tick = ctx.tick(symbol)
    spread = float(np.median(bars["spread"].tail(50))) * point
    if entry is not None:
        price, source = float(entry), "vorgegeben"
    elif tick:
        price, source = float(tick["ask"] if direction == 1 else tick["bid"]), "MT5 live (Ask/Bid)"
    else:
        last = float(bars["close"].iloc[-1])
        price, source = (last + spread if direction == 1 else last), "letzter Schlusskurs (+ Spread bei Long)"

    if stop_rule.startswith("or-"):
        if "range_hoch" not in orb:
            raise ValueError("Für eine Stop-Regel an der Opening Range muss die Range heute vollständig sein – "
                             "sonst --stop atr verwenden")
        stop = orb["range_mitte"] if stop_rule == "or-mitte" else (orb["range_tief"] if direction == 1
                                                                    else orb["range_hoch"])
    else:
        if not atr:
            raise ValueError("ATR nicht verfügbar")
        stop = price - direction * atr_mult * atr
    risk = direction * (price - stop)
    if risk <= 0:
        raise ValueError(f"Stop {round_price(stop, digits)} liegt nicht auf der richtigen Seite des Einstiegs "
                         f"{round_price(price, digits)} – Kurs ist schon zurückgelaufen, Plan verwerfen")
    target = price + direction * target_r * risk
    plan = {"symbol": symbol, "setup": setup, "richtung": direction, "einstieg": round_price(price, digits),
            "einstieg_quelle": source, "stop": round_price(stop, digits), "stop_regel": stop_rule,
            "atr_mult": atr_mult if stop_rule == "atr" else None, "ziel": round_price(target, digits),
            "ziel_r": target_r, "risiko_preis": round_price(risk, digits), "atr14": atr,
            "spread_preis": round_price(spread, digits), "erstellt_utc": ctx.now().isoformat(),
            "sitzung": {"name": sess.name, "zeitzone": sess.tz, "start": sess.hhmm(sess.start),
                        "range_min": sess.range_min, "einstieg_bis": sess.hhmm(sess.entry_until),
                        "glattstellen": sess.hhmm(sess.flat_by)},
            "tag": str(today.date()), "einstieg_min": now_min}
    for k in ("range_hoch", "range_tief", "range_mitte"):
        if k in orb:
            plan[k] = round_price(orb[k], digits)
    if setup == "orb" and orb.get("zustand") == "ausgelöst" and orb["richtung"] != direction:
        plan["warnung"] = "Richtung entgegen dem Opening-Range-Signal"
    if now_min >= sess.flat_by:
        plan["warnung"] = f"Sitzung vorbei (glattstellen {sess.hhmm(sess.flat_by)} {sess.tz})"
    elif now_min > sess.entry_until:
        plan["warnung"] = f"nach einstieg_bis ({sess.hhmm(sess.entry_until)} {sess.tz}) – die Risikoprüfung blockiert"
    ctx.write("plan.json", plan)
    ctx.log("plan", **{k: plan[k] for k in ("symbol", "setup", "richtung", "einstieg", "stop", "ziel")})
    return plan


def format_plan(p: dict) -> str:
    d = "KAUF (Long)" if p["richtung"] == 1 else "VERKAUF (Short)"
    s = p["sitzung"]
    lines = [f"Plan {p['symbol']} – {d}, Setup {p['setup']}",
             f"  Einstieg {p['einstieg']} ({p['einstieg_quelle']})",
             f"  Stop     {p['stop']} ({p['stop_regel']}) – Risiko {p['risiko_preis']} je Einheit",
             f"  Ziel     {p['ziel']} ({p['ziel_r']:g} R)",
             f"  Glattstellen spätestens {s['glattstellen']} ({s['zeitzone']})"]
    if "range_hoch" in p:
        lines.insert(1, f"  Opening Range {p['range_tief']}–{p['range_hoch']} (Mitte {p['range_mitte']})")
    if p.get("warnung"):
        lines.append(f"  ACHTUNG: {p['warnung']}")
    return "\n".join(lines)
