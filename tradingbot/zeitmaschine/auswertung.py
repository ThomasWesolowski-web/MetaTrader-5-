"""Auswertung: Kennzahlen aus den Trades und Monte Carlo mit Block-Bootstrap (Volatilitätsphasen bleiben erhalten).

Kennzahlen (alle nach Kosten): Gewinnrate, durchschnittlicher Gewinn/Verlust, CRV (durchschnittlicher Gewinn /
durchschnittlicher Verlust), Profit-Faktor, Erwartungswert je Trade in R und USD, größter Rückgang (Drawdown) in USD
und in % vom Startkonto, längste Verlustserie, Ergebnis je Jahr und Halbjahr.

Monte Carlo ("Was hätte auch passieren können?"):
  Die echte Trade-Geschichte wird in Kalenderwochen geschnitten. Ein Durchlauf setzt eine neue Geschichte aus
  zufälligen BLÖCKEN aufeinanderfolgender Wochen zusammen (stationärer Bootstrap nach Politis/Romano, mittlere
  Blocklänge block_wochen). Ruhige und hektische Phasen, Pechsträhnen und Wochen ohne Trade bleiben dadurch
  zusammen – anders als beim Ziehen einzelner Kerzen oder einzelner Trades, das Serien zerreißt und das Risiko
  unterschätzt. Zum Vergleich wird dasselbe auch mit einzeln gezogenen Trades gerechnet.
  Gehandelt wird mit fester Lotgröße (wie die Bots) vom Startkonto aus; fällt das Konto unter den Mindestbetrag,
  ist es "ruiniert" und handelt nicht weiter.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ============================================================================ Kennzahlen
def kennzahlen(tr: pd.DataFrame, konto: float, dd_stopp_pct: float | None = None) -> dict:
    n = len(tr)
    if n == 0:
        return {"trades": 0}
    pnl = tr["pnl"].to_numpy(float)
    r = tr["r"].to_numpy(float)
    win, loss = pnl > 0, pnl < 0
    avg_w = float(pnl[win].mean()) if win.any() else 0.0
    avg_l = float(-pnl[loss].mean()) if loss.any() else 0.0
    eq = konto + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[konto], eq]))[1:]
    dd = peak - eq
    i_dd = int(np.argmax(dd))
    streak = longest = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
        longest = max(longest, streak)
    t0, t1 = tr["entry_time"].iloc[0], tr["exit_time"].iloc[-1]
    months = max((t1 - t0).days / 30.44, 1e-9)
    gp, gl = float(pnl[win].sum()), float(-pnl[loss].sum())
    risk_usd = tr["risk_usd"].to_numpy(float)
    stopp = None
    if dd_stopp_pct:
        hit = np.nonzero(dd >= konto * dd_stopp_pct / 100)[0]
        stopp = str(tr["exit_time"].iloc[hit[0]].date()) if len(hit) else None
    blank = np.nonzero(eq <= 0)[0]
    return {
        "trades": n, "von": str(t0.date()), "bis": str(t1.date()), "trades_pro_monat": round(n / months, 1),
        "gewinnrate": round(float(win.mean()), 4),
        "gewinner": int(win.sum()), "verlierer": int(loss.sum()), "null": int((pnl == 0).sum()),
        "mittel_gewinn": round(avg_w, 2), "mittel_verlust": round(avg_l, 2),
        "crv": round(avg_w / avg_l, 2) if avg_l > 0 else None,
        "profit_faktor": round(gp / gl, 2) if gl > 0 else None,
        "erwartung_r": round(float(r.mean()), 3), "erwartung_usd": round(float(pnl.mean()), 2),
        "summe_usd": round(float(pnl.sum()), 2), "summe_r": round(float(r.sum()), 1),
        "endkonto": round(float(eq[-1]), 2), "rendite_pct": round(100 * float(pnl.sum()) / konto, 1),
        "max_drawdown_usd": round(float(dd.max()), 2),
        "max_drawdown_pct": round(100 * float(dd.max()) / konto, 1),
        "max_drawdown_bis": str(tr["exit_time"].iloc[i_dd].date()),
        "laengste_verlustserie": longest,
        "risiko_usd_median": round(float(np.median(risk_usd)), 2),
        "risiko_pct_median": round(100 * float(np.median(risk_usd)) / konto, 1),
        "dd_stopp_pct": dd_stopp_pct, "dd_stopp_am": stopp,
        "konto_leer_am": str(tr["exit_time"].iloc[blank[0]].date()) if len(blank) else None,
        "kosten_usd": round(float(-(tr["commission"] + tr["swap"].clip(upper=0)).sum()), 2),
        "luecken_ueber_stop": int(tr["exit_reason"].str.contains("luecke|gap").sum()),
        "schlechtester_trade_r": round(float(r.min()), 2),
        "kurve": [round(float(x), 2) for x in eq],
        "kurve_zeit": [str(x.date()) for x in tr["exit_time"]],
    }


def je_zeitraum(tr: pd.DataFrame, freq: str = "Y") -> list[dict]:
    if tr.empty:
        return []
    key = tr["exit_time"].dt.year.astype(str) if freq == "Y" else (
        tr["exit_time"].dt.year.astype(str) + "-H" + ((tr["exit_time"].dt.month > 6) + 1).astype(str))
    out = []
    for k, g in tr.groupby(key):
        out.append({"zeitraum": k, "trades": len(g), "gewinnrate": round(float((g["pnl"] > 0).mean()), 3),
                    "summe_usd": round(float(g["pnl"].sum()), 2), "erwartung_r": round(float(g["r"].mean()), 3)})
    return out


# ============================================================================ Monte Carlo
def wochen(tr: pd.DataFrame) -> list[np.ndarray]:
    """Trade-Ergebnisse (USD) je Kalenderwoche, lückenlos vom ersten bis zum letzten Trade (leere Wochen = [])."""
    wk = tr["exit_time"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("W-SUN")
    first, last = wk.min(), wk.max()
    allw = pd.period_range(first, last, freq="W-SUN")
    groups = {k: g.to_numpy(float) for k, g in tr["pnl"].groupby(wk)}
    return [groups.get(w, np.empty(0)) for w in allw]


def _stationary_index(rng, n_src: int, length: int, mean_block: float) -> np.ndarray:
    """Indizes einer Bootstrap-Geschichte: Blöcke aufeinanderfolgender Wochen mit geometrischer Länge."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty(length, dtype=np.int64)
    cur = rng.integers(n_src)
    for k in range(length):
        if k == 0 or rng.random() < p:
            cur = rng.integers(n_src)
        else:
            cur = (cur + 1) % n_src           # Kreis: nach der letzten Woche geht es mit der ersten weiter
        idx[k] = cur
    return idx


def monte_carlo(tr: pd.DataFrame, konto: float, laeufe: int = 5000, wochen_horizont: int = 52,
                block_wochen: float = 4.0, ruin_konto: float | None = None, seed: int = 7,
                einzeln: bool = False) -> dict:
    """Verteilung von Endkonto, größtem Rückgang und Ruin über wochen_horizont Wochen.

    einzeln=True: Vergleichsrechnung, Trades unabhängig voneinander gezogen (gleiche Anzahl Trades je Durchlauf
    wie im Schnitt der echten Geschichte) – zerreißt Serien und unterschätzt typischerweise das Risiko."""
    if tr.empty:
        return {"laeufe": 0}
    rng = np.random.default_rng(seed)
    ruin_konto = konto * 0.1 if ruin_konto is None else ruin_konto
    wk = wochen(tr)
    n_src = len(wk)
    if einzeln:
        pnl_all = tr["pnl"].to_numpy(float)
        per_week = len(pnl_all) / n_src
        n_tr = max(1, int(round(per_week * wochen_horizont)))
        paths = [pnl_all[rng.integers(len(pnl_all), size=n_tr)] for _ in range(laeufe)]
    else:
        paths = []
        for _ in range(laeufe):
            idx = _stationary_index(rng, n_src, wochen_horizont, block_wochen)
            seq = [wk[j] for j in idx]
            paths.append(np.concatenate(seq) if seq else np.empty(0))
    end = np.empty(laeufe)
    mdd = np.empty(laeufe)
    mdd_pct = np.empty(laeufe)
    ruined = np.zeros(laeufe, bool)
    lo_curve = []
    for k, p in enumerate(paths):
        eq = konto + np.cumsum(p) if len(p) else np.array([konto])
        below = np.nonzero(eq <= ruin_konto)[0]
        if len(below):
            ruined[k] = True
            eq = eq[: below[0] + 1]
        peak = np.maximum.accumulate(np.concatenate([[konto], eq]))
        dd = peak[1:] - eq
        end[k] = eq[-1]
        mdd[k] = dd.max() if len(dd) else 0.0
        j = int(np.argmax(dd)) if len(dd) else 0
        mdd_pct[k] = 100 * dd[j] / peak[j + 1] if len(dd) else 0.0
        lo_curve.append(eq)
    # Fächer: Kontostand nach jeder Woche wäre teuer – hier je Trade-Nummer normiert auf 40 Stützstellen
    fan = _fan(lo_curve, konto)
    q = lambda a, x: round(float(np.percentile(a, x)), 2)
    return {
        "laeufe": laeufe, "wochen": wochen_horizont, "block_wochen": block_wochen if not einzeln else 0,
        "methode": "einzelne Trades" if einzeln else "Wochen-Blöcke", "seed": seed,
        "quell_wochen": n_src, "ruin_konto": round(ruin_konto, 2),
        "endkonto": {str(x): q(end, x) for x in (5, 25, 50, 75, 95)},
        "im_plus": round(float((end > konto).mean()), 4),
        "verdoppelt": round(float((end >= 2 * konto).mean()), 4),
        "drawdown_usd": {str(x): q(mdd, x) for x in (50, 75, 95)},
        "drawdown_pct": {str(x): q(mdd_pct, x) for x in (50, 75, 95)},
        "p_rueckgang": {str(x): round(float((mdd >= konto * x / 100).mean()), 4) for x in (15, 25, 50)},
        "p_ruin": round(float(ruined.mean()), 4),
        "faecher": fan,
        "endkonten": [round(float(x), 2) for x in np.sort(end)[:: max(1, laeufe // 400)]],
    }


def _fan(curves: list[np.ndarray], konto: float, points: int = 40) -> dict:
    m = max((len(c) for c in curves), default=1)
    grid = np.linspace(0, m, points + 1)[1:]
    mat = np.empty((len(curves), points))
    for k, cv in enumerate(curves):
        full = np.concatenate([[konto], cv])
        pos = np.minimum(grid.astype(int), len(full) - 1)
        mat[k] = full[pos]
    return {"trade_nr": [int(x) for x in grid],
            **{f"p{x}": [round(float(v), 2) for v in np.percentile(mat, x, axis=0)] for x in (5, 25, 50, 75, 95)}}


# ============================================================================ Urteil
def urteil(k_by_cost: dict, halves: list[dict], mc: dict, konto: float) -> list[str]:
    """Ehrliche Einschätzung in Worten aus allen Kostenszenarien, der zeitlichen Stabilität und dem Ruin-Risiko."""
    out = []
    kk = k_by_cost.get("konservativ") or next(iter(k_by_cost.values()))
    n = kk.get("trades", 0)
    if n < 30:
        out.append(f"Nur {n} Trades – zu wenige für ein belastbares Urteil (Faustregel: mindestens 30, besser 100).")
    if n == 0:
        return out
    signs = {name: (k.get("summe_usd") or 0) > 0 for name, k in k_by_cost.items() if k.get("trades")}
    if all(signs.values()):
        out.append("Bleibt in allen drei Kostenszenarien im Plus – auch mit hohen Kosten.")
    elif signs.get("basis") and not signs.get("konservativ"):
        out.append("Verdient nur bei sehr niedrigen Kosten. Mit realistischen Kosten (konservativ) im Minus – "
                   "so nicht handeln.")
    elif not signs.get("basis", True):
        out.append("Verliert schon bei den niedrigsten Kosten – die Regeln haben auf diesen Daten keinen Vorteil.")
    elif signs.get("konservativ") and not signs.get("hart"):
        out.append("Im Plus mit realistischen Kosten, im Stresstest (hart) aber im Minus – Vorteil ist dünn.")
    pos_h = [h for h in halves if h["trades"] >= 5]
    if len(pos_h) >= 2:
        good = sum(h["summe_usd"] > 0 for h in pos_h)
        if good == len(pos_h):
            out.append(f"Alle {len(pos_h)} Halbjahre im Plus – stabil über die Zeit.")
        elif good == 0:
            out.append(f"Kein einziges Halbjahr im Plus.")
        else:
            out.append(f"{good} von {len(pos_h)} Halbjahren im Plus – das Ergebnis schwankt stark mit der Marktphase.")
    if mc.get("laeufe"):
        pr = mc["p_ruin"]
        p25 = mc["p_rueckgang"]["25"]
        if pr >= 0.05:
            out.append(f"Ruin-Risiko {100 * pr:.0f} % in {mc['wochen']} Wochen – für {konto:.0f} $ mit dieser "
                       f"Lotgröße zu hoch.")
        elif p25 >= 0.25:
            out.append(f"In {100 * p25:.0f} % der Durchläufe ein Rückgang von mindestens 25 % – nervlich hart, "
                       f"Lotgröße nicht erhöhen.")
        else:
            out.append(f"Ruin-Risiko gering ({100 * pr:.1f} %), ein Rückgang von 25 % kam in "
                       f"{100 * p25:.0f} % der Durchläufe vor.")
    out.append("Vergangenheit, keine Garantie: Nachrichten, neue Marktphasen und Broker-Ausfälle können anders sein.")
    return out


def fmt_pct(x) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.1f} %"
