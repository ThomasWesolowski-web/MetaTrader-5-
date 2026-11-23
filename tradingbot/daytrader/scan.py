"""Scanner: sucht in den eingestellten Symbolen nach drei Day-Trading-Setups (nur abgeschlossene Kerzen).

    orb     Opening-Range-Breakout: erste Kerze, die nach der Range über dem Hoch (Long) bzw. unter dem Tief
            (Short) schließt, bis einstieg_bis. Zustand: wartet / ausgelöst / abgelaufen.
    vortag  Schlusskurs über dem Hoch bzw. unter dem Tief des Vortags (Sitzungstag in Ortszeit).
    rsi     RSI(14) kreuzt von unter 30 zurück über 30 (Long) bzw. von über 70 zurück unter 70 (Short)
            auf der letzten abgeschlossenen Kerze.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..timeframes import seconds
from .data import Context, local_frame, opening_range_of, session_day


def orb_state(day_bars: pd.DataFrame, sess, tf_min: int, now_min: int, digits: int = 5) -> dict:
    """Zustand des Opening-Range-Breakouts an einem Sitzungstag (day_bars: Kerzen dieses Tages, Ortszeit)."""
    if now_min < sess.range_end:
        return {"zustand": "wartet", "grund": f"Range läuft bis {sess.hhmm(sess.range_end)}"}
    rng = opening_range_of(day_bars, sess, tf_min)
    if rng is None:
        return {"zustand": "keine_daten", "grund": "Kerzen der Opening Range fehlen (Markt geschlossen oder Lücke)"}
    hi, lo = round(rng[0], digits), round(rng[1], digits)
    after = day_bars[(day_bars["mins"] >= sess.range_end) & (day_bars["mins"] + tf_min <= sess.entry_until)]
    base = {"range_hoch": hi, "range_tief": lo, "range_mitte": round((hi + lo) / 2, digits)}
    for _, b in after.iterrows():
        d = 1 if b["close"] > hi else (-1 if b["close"] < lo else 0)
        if d:
            lvl = hi if d == 1 else lo
            return {**base, "zustand": "ausgelöst", "richtung": d, "kurs": round(float(b["close"]), digits),
                    "zeit_utc": pd.Timestamp(b["time"]).isoformat(),
                    "abstand_pct": round(100 * d * (b["close"] - lvl) / lvl, 3)}
    if now_min >= sess.entry_until:
        return {**base, "zustand": "abgelaufen", "grund": f"kein Ausbruch bis {sess.hhmm(sess.entry_until)}"}
    return {**base, "zustand": "wartet", "grund": "noch kein Schlusskurs außerhalb der Range"}


def scan_symbol(ctx: Context, symbol: str) -> dict:
    sess = ctx.dt.session(symbol)
    tf_min = seconds(ctx.dt.timeframe) // 60
    bars = ctx.bars(symbol, days=10)
    out = {"symbol": symbol, "sitzung": sess.name, "zeitzone": sess.tz}
    try:
        dg = int(ctx.spec(symbol)["digits"])
    except RuntimeError:
        dg = 5
    rd = lambda x: round(float(x), dg)
    if bars.empty:
        return {**out, "fehler": "keine Kerzen (MT5 offen? daten_aktualisieren.py ausgeführt?)"}
    lf = local_frame(bars, sess)
    today, now_min = session_day(ctx.now(), sess)
    tb = lf[lf["day"] == today]
    out["letzte_kerze_utc"] = pd.Timestamp(bars["time"].iloc[-1]).isoformat()
    out["letzter_kurs"] = rd(bars["close"].iloc[-1])
    out["orb"] = orb_state(tb, sess, tf_min, now_min, dg) if len(tb) else {"zustand": "keine_daten",
                                                                       "grund": "heute noch keine Kerzen"}
    prev_days = sorted(d for d in lf["day"].unique() if d < today)
    if prev_days and len(tb):
        pb = lf[lf["day"] == prev_days[-1]]
        ph, pl, c = rd(pb["high"].max()), rd(pb["low"].min()), rd(tb["close"].iloc[-1])
        d = 1 if c > ph else (-1 if c < pl else 0)
        out["vortag"] = {"hoch": ph, "tief": pl, "kurs": c, "richtung": d,
                         "zustand": "ausgelöst" if d else "innerhalb"}
    r = ind.rsi(bars["close"].to_numpy(dtype=float), 14)
    if len(r) >= 2 and np.isfinite(r[-2:]).all():
        d = 1 if (r[-2] < 30 <= r[-1]) else (-1 if (r[-2] > 70 >= r[-1]) else 0)
        out["rsi"] = {"wert": round(float(r[-1]), 1), "richtung": d, "zustand": "ausgelöst" if d else "neutral"}
    a = ind.atr(bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float), 14)
    out["atr14"] = rd(a[-1]) if len(a) and np.isfinite(a[-1]) else None
    return out


def scan(ctx: Context, symbols: list[str] | None = None) -> dict:
    res = {"erstellt_utc": ctx.now().isoformat(), "zeitrahmen": ctx.dt.timeframe,
           "quelle": "MT5 live + Datenbank" if ctx.live else "nur Datenbank (MT5 nicht verbunden)",
           "symbole": [scan_symbol(ctx, s) for s in (symbols or ctx.dt.symbols)]}
    ctx.write("scan.json", res)
    return res


def _dir(d) -> str:
    return {1: "Long", -1: "Short"}.get(d, "–")


def format_scan(res: dict) -> str:
    lines = [f"Scan {res['erstellt_utc'][:16]} UTC, {res['zeitrahmen']}, {res['quelle']}"]
    for s in res["symbole"]:
        if "fehler" in s:
            lines.append(f"  {s['symbol']}: {s['fehler']}")
            continue
        o = s["orb"]
        if o["zustand"] == "ausgelöst":
            side = "über dem Hoch" if o["richtung"] == 1 else "unter dem Tief"
            orb = (f"AUSGELÖST {_dir(o['richtung'])} @ {o['kurs']} ({o['abstand_pct']:.2f} % {side} der Range "
                   f"{o['range_tief']}–{o['range_hoch']}, {o['zeit_utc'][11:16]} UTC)")
        else:
            orb = f"{o['zustand']}" + (f" – {o['grund']}" if o.get("grund") else "")
        lines.append(f"  {s['symbol']} ({s['sitzung']}), Kurs {s['letzter_kurs']}:")
        lines.append(f"    Opening Range: {orb}")
        if "vortag" in s:
            v = s["vortag"]
            lines.append(f"    Vortag {v['tief']}–{v['hoch']}: " + (f"Ausbruch {_dir(v['richtung'])}"
                                                                    if v["richtung"] else "innerhalb"))
        if "rsi" in s:
            lines.append(f"    RSI(14) {s['rsi']['wert']}: " + (f"Umkehr {_dir(s['rsi']['richtung'])}"
                                                               if s["rsi"]["richtung"] else "neutral"))
    return "\n".join(lines)
