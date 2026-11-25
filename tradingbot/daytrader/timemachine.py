"""Zeitmaschine: spielt den Rest des Handelstages eines Plans 10 000-mal durch, bevor Geld riskiert wird.

1. Vorrat: nur VOLLSTÄNDIGE Sitzungen vor dem Plan-Tag (alle Kerzen von start bis glattstellen vorhanden, Ortszeit
   der Sitzung). Jede Kerze wird zu vier Verhältnissen zum vorherigen Schlusskurs (Eröffnung, Hoch, Tief, Schluss).
2. Ein Durchlauf: ab dem Einstieg werden BLÖCKE von `block` aufeinanderfolgenden Kerzen eines zufälligen vergangenen
   Tages (gleiche Tageszeit, Block um ± fenster Kerzen verschoben) aneinandergehängt und auf den aktuellen Kurs
   skaliert. So bleiben ruhige und hektische Phasen zusammen (Volatilität kommt in Schüben); block = 1 wäre das
   alte Ziehen einzelner Kerzen. Je Kerze zuerst Stop, dann Ziel prüfen; berührt eine Kerze beides, zählt der Stop.
   Kurse sind Bid; Short-Ausstiege zum Ask (+ Spread).
3. Ergebnis in R: Ziel = +ziel_r, Stop = −1; eröffnet eine Kerze schon jenseits des Stops (Lücke), wird zum
   Eröffnungskurs ausgestiegen (mehr als −1 R). Offen zur Glattstellzeit = Abstand / Risiko. Kosten (Kommission,
   Slippage aus dem Kostenszenario) werden als R abgezogen ("netto").
4. Geschichte: dieselbe Geometrie auf jedem vergangenen Tag zur selben Uhrzeit, und beim Setup orb dieselbe Regel
   (Opening Range, Stop-Regel, Ziel in R) – das ist, was wirklich passiert ist, keine Simulation.

Eine Simulation aus den eigenen letzten Wochen, keine Vorhersage: Nachrichten, Zinsentscheide oder Wochenenden, die in
diesen Wochen nicht vorkamen, kennt sie nicht. Ganze Strategien über Monate (Gold-Bot, ORB) testet zeitmaschine.py.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..backtest.costs import scenarios_for
from ..timeframes import seconds
from .data import Context, local_frame


def _sessions(lf: pd.DataFrame, sess, tf_min: int, before_day) -> tuple[list, np.ndarray]:
    """Vollständige Sitzungen vor before_day: (Tage, Array Tage x Kerzen x [open, high, low, close])."""
    n = (sess.flat_by - sess.start) // tf_min
    slots = sess.start + tf_min * np.arange(n)
    w = lf[(lf["mins"] >= sess.start) & (lf["mins"] < sess.flat_by) & (lf["day"] < before_day)]
    days, arrs = [], []
    for day, g in w.groupby("day"):
        if len(g) != n or not np.array_equal(g["mins"].to_numpy(), slots):
            continue
        days.append(day)
        arrs.append(g[["open", "high", "low", "close"]].to_numpy(dtype=float))
    return days, (np.stack(arrs) if arrs else np.empty((0, n, 4)))


def _ratios(ohlc: np.ndarray) -> np.ndarray:
    prev = np.concatenate([ohlc[:, :1, 0:1], ohlc[:, :-1, 3:4]], axis=1)    # erste Kerze: eigene Eröffnung
    return ohlc / prev


def _walk(o, h, l, c, d, entry, stop, target, spread):
    """Erstes Ereignis je Zeile: 1 Ziel, −1 Stop, 0 offen; dazu Schlusskurs der letzten Kerze (Bid) und der
    Ausstiegskurs beim Stop (Stop-Kurs, bei einer Lücke der Eröffnungskurs der Kerze)."""
    if d == 1:
        hit_s, hit_t = l <= stop, h >= target
    else:
        hit_s, hit_t = h + spread >= stop, l + spread <= target
    big = hit_s.shape[1] + 1
    fs = np.where(hit_s.any(1), hit_s.argmax(1), big)
    ft = np.where(hit_t.any(1), hit_t.argmax(1), big)
    res = np.where((fs <= ft) & (fs < big), -1, np.where(ft < big, 1, 0))
    o_at = o[np.arange(o.shape[0]), np.minimum(fs, o.shape[1] - 1)]
    stop_px = np.minimum(o_at, stop) if d == 1 else np.maximum(o_at + spread, stop)
    stop_px = np.where(fs < big, stop_px, stop)
    return res, c[:, -1], stop_px


def _r(res, last_bid, d, entry, risk, target_r, spread, stop_px=None):
    exit_px = last_bid + (spread if d == -1 else 0.0)
    open_r = d * (exit_px - entry) / risk
    stop_r = -1.0 if stop_px is None else d * (stop_px - entry) / risk
    return np.where(res == 1, target_r, np.where(res == -1, stop_r, open_r))


def verdict(p_t: float, p_s: float, target_r: float) -> str:
    decided = p_t + p_s
    if decided < 0.05:
        return "Meist weder Ziel noch Stop bis zur Glattstellzeit – das Ergebnis hängt am Schlusskurs"
    share = p_t / decided
    be = 1 / (1 + target_r)
    diff = 100 * (share - be)
    near = "Nahe am Münzwurf" if math.isclose(target_r, 1.0) else "Nahe an der Gewinnschwelle"
    if abs(diff) < 5:
        return near
    side = "Ziel" if diff > 0 else "Stop"
    return f"{'Neigt' if abs(diff) < 15 else 'Deutlich'} zum {side}"


def run(ctx: Context, plan: dict | None = None, replays: int = 10_000, seed: int = 19, window: int = 2,
        days: int = 60, block: int = 6) -> dict:
    plan = plan or ctx.read("plan.json")
    if not plan:
        raise RuntimeError("Kein Plan – zuerst planen (daytrader.py planen)")
    sym, d = plan["symbol"], int(plan["richtung"])
    entry, stop, target, target_r = (float(plan[k]) for k in ("einstieg", "stop", "ziel", "ziel_r"))
    risk = d * (entry - stop)
    sess = ctx.dt.session(sym)
    tf_min = seconds(ctx.dt.timeframe) // 60
    bars = ctx.bars(sym, days=days + 10)
    lf = local_frame(bars, sess)
    plan_day = pd.Timestamp(plan["tag"])
    pool_days, ohlc = _sessions(lf, sess, tf_min, plan_day)
    pool_days, ohlc = pool_days[-days:], ohlc[-days:]
    if len(pool_days) < 10:
        raise RuntimeError(f"Nur {len(pool_days)} vollständige Sitzungen in den Daten – mindestens 10 nötig "
                           f"(daten_aktualisieren.py ausführen)")
    n = ohlc.shape[1]
    j0 = max(0, -(-(int(plan["einstieg_min"]) - sess.start) // tf_min))
    if j0 >= n:
        raise RuntimeError("Keine Kerze mehr bis zur Glattstellzeit")
    spread = float(plan.get("spread_preis") or 0.0)
    spec = ctx.spec(sym)
    cs = float(spec.get("contract_size", 1.0) or 1.0)
    costs = scenarios_for(sym)[ctx.dt.cost_scenario]
    cost_px = costs.commission_per_lot_rt / cs + costs.commission_frac_rt * entry + 2 * costs.slippage_frac * entry
    cost_r = cost_px / risk

    # ------------------------------------------------------------ Monte Carlo
    rat = _ratios(ohlc)
    rng = np.random.default_rng(seed)
    K = n - j0
    slot = j0 + np.arange(K)
    block = max(1, int(block))
    nb = -(-K // block)
    blk = np.arange(K) // block                              # Block-Nummer je Kerze
    off = rng.integers(-window, window + 1, size=(replays, nb))[:, blk]
    s_idx = np.clip(slot[None, :] + off, 0, n - 1)
    d_idx = rng.integers(0, len(pool_days), size=(replays, nb))[:, blk]
    r = rat[d_idx, s_idx]                                    # replays x K x 4
    bid0 = entry - spread if d == 1 else entry
    closes = bid0 * np.cumprod(r[:, :, 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), bid0), closes[:, :-1]], axis=1)
    o, h, l, c = prev * r[:, :, 0], prev * r[:, :, 1], prev * r[:, :, 2], closes
    res, last, spx = _walk(o, h, l, c, d, entry, stop, target, spread)
    R = _r(res, last, d, entry, risk, target_r, spread, spx)
    p_t, p_s, p_o = (float((res == k).mean()) for k in (1, -1, 0))
    mc = {"laeufe": replays, "seed": seed, "fenster": window, "block_kerzen": block, "kerzen_bis_glattstellen": K,
          "ziel_zuerst": round(p_t, 4), "stop_zuerst": round(p_s, 4), "offen_bei_glattstellen": round(p_o, 4),
          "im_plus": round(float((R - cost_r > 0).mean()), 4),
          "mittel_r_brutto": round(float(R.mean()), 3), "mittel_r_netto": round(float(R.mean() - cost_r), 3),
          "kosten_r": round(cost_r, 3),
          "schlusskurs_perzentile": {str(q): round(float(np.percentile(last, q)), 2) for q in (5, 25, 50, 75, 95)}}

    # ------------------------------------------------------------ Geschichte
    hist = {"vollstaendige_tage": len(pool_days)}
    rp = risk / entry
    eo = ohlc[:, j0, 0] + (spread if d == 1 else 0.0)
    st, tg = eo * (1 - d * rp), eo * (1 + d * rp * target_r)
    rr = []
    for i in range(len(pool_days)):
        x = ohlc[i:i + 1, j0:]
        res_i, last_i, sp_i = _walk(x[..., 0], x[..., 1], x[..., 2], x[..., 3], d, eo[i], st[i], tg[i], spread)
        rr.append((int(res_i[0]), float(_r(res_i, last_i, d, eo[i], eo[i] * rp, target_r, spread, sp_i)[0])))
    hist["gleiche_uhrzeit"] = _summary(rr, cost_r)
    if plan.get("setup") == "orb":
        hist["gleiche_regel"] = _orb_history(ohlc, sess, tf_min, d, plan, spread, target_r, bars, lf, pool_days,
                                             cost_r)
    out = {"plan": plan, "zeitmaschine": mc, "geschichte": hist, "urteil": verdict(p_t, p_s, target_r),
           "gewinnschwelle_trefferquote": round(1 / (1 + target_r), 3),
           "erstellt_utc": ctx.now().isoformat()}
    ctx.write("zeitmaschine.json", out)
    return out


def _summary(rr: list[tuple[int, float]], cost_r: float) -> dict:
    if not rr:
        return {"ausgeloest": 0}
    res = np.array([x[0] for x in rr])
    R = np.array([x[1] for x in rr]) - cost_r
    return {"ausgeloest": len(rr), "ziel": int((res == 1).sum()), "stop": int((res == -1).sum()),
            "offen": int((res == 0).sum()), "mittel_r_netto": round(float(R.mean()), 3),
            "summe_r_netto": round(float(R.sum()), 2)}


def _orb_history(ohlc, sess, tf_min, d, plan, spread, target_r, bars, lf, pool_days, cost_r) -> dict:
    nr = sess.range_min // tf_min
    last_entry = (sess.entry_until - sess.start) // tf_min
    atr_s = pd.Series(ind.atr(bars["high"].to_numpy(float), bars["low"].to_numpy(float),
                              bars["close"].to_numpy(float), 14), index=pd.MultiIndex.from_arrays([lf["day"], lf["mins"]]))
    rr = []
    for i, day in enumerate(pool_days):
        x = ohlc[i]
        hi, lo = x[:nr, 1].max(), x[:nr, 2].min()
        for j in range(nr, min(last_entry, len(x))):
            if d * (x[j, 3] - (hi if d == 1 else lo)) > 0:
                e = x[j, 3] + (spread if d == 1 else 0.0)
                rule = plan.get("stop_regel")
                if rule == "or-mitte":
                    s = (hi + lo) / 2
                elif rule == "or-gegenseite":
                    s = lo if d == 1 else hi
                else:
                    a = atr_s.get((day, sess.start + j * tf_min), np.nan)
                    s = e - d * float(plan.get("atr_mult") or 1.5) * a
                rk = d * (e - s)
                if not (rk > 0) or j + 1 >= len(x):
                    break
                y = x[None, j + 1:]
                t = e + d * target_r * rk
                res, last, spx = _walk(y[..., 0], y[..., 1], y[..., 2], y[..., 3], d, e, s, t, spread)
                rr.append((int(res[0]), float(_r(res, last, d, e, rk, target_r, spread, spx)[0])))
                break
    return _summary(rr, cost_r)


def format_run(o: dict) -> str:
    mc, h, p = o["zeitmaschine"], o["geschichte"], o["plan"]
    pct = lambda x: f"{100 * x:.1f} %"
    lines = [f"Zeitmaschine {p['symbol']} {'Long' if p['richtung'] == 1 else 'Short'}: Einstieg {p['einstieg']}, "
             f"Stop {p['stop']}, Ziel {p['ziel']} ({p['ziel_r']:g} R)",
             f"  {mc['laeufe']:,} Durchläufe (Seed {mc['seed']}, Blöcke à {mc.get('block_kerzen', 1)} Kerzen) aus "
             f"{h['vollstaendige_tage']} vollständigen Sitzungen, "
             f"{mc['kerzen_bis_glattstellen']} Kerzen bis zur Glattstellzeit".replace(",", "."),
             f"  Ziel zuerst {pct(mc['ziel_zuerst'])} · Stop zuerst {pct(mc['stop_zuerst'])} · "
             f"offen bei Glattstellzeit {pct(mc['offen_bei_glattstellen'])}",
             f"  Durchschnitt {mc['mittel_r_netto']:+.2f} R netto (Kosten {mc['kosten_r']:.2f} R je Trade), "
             f"im Plus {pct(mc['im_plus'])}",
             f"  Gewinnschwelle: Ziel muss in {pct(o['gewinnschwelle_trefferquote'])} der entschiedenen Fälle zuerst kommen"]
    g = h["gleiche_uhrzeit"]
    if g.get("ausgeloest"):
        lines.append(f"  Geschichte, gleiche Uhrzeit: {g['ausgeloest']} Tage – Ziel {g['ziel']}, Stop {g['stop']}, "
                     f"offen {g['offen']}, Summe {g['summe_r_netto']:+.1f} R netto")
    g = h.get("gleiche_regel")
    if g is not None:
        lines.append(f"  Geschichte, gleiche ORB-Regel: ausgelöst {g.get('ausgeloest', 0)}-mal" +
                     (f" – Ziel {g['ziel']}, Stop {g['stop']}, offen {g['offen']}, Summe {g['summe_r_netto']:+.1f} R netto"
                      if g.get("ausgeloest") else ""))
    lines.append(f"  Urteil: {o['urteil']}. Simulation, keine Vorhersage.")
    return "\n".join(lines)
