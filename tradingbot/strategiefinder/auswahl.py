"""Die beste Regel je Setup finden – ohne sich selbst zu belügen (Walk-Forward + Deflated Sharpe Ratio).

Warum nicht einfach die Regel mit dem höchsten Gewinn nehmen?
  Bei tausenden Varianten ist immer eine dabei, die auf den vergangenen Kursen glänzt – rein durch Zufall. Ihr
  Rückblick-Ergebnis sagt fast nichts über die Zukunft (Kurvenanpassung / Data Snooping).

Was der Finder stattdessen macht (je Setup = Zeitrahmen + Handelszeit):
  1. Walk-Forward: Die Historie wird in Abschnitte von oos_monate Monaten geteilt. Für jeden Abschnitt wählt der
     Finder die Regel NUR anhand der Trades davor (Lernfenster, höchstens lern_monate Monate, nur Trades, die vor
     dem Abschnitt schon geschlossen waren) und handelt sie dann im Abschnitt. Die so gesammelten Trades sind
     "Out-of-Sample": So wäre es dem Finder wirklich ergangen, wenn er jedes Quartal neu gewählt hätte.
  2. Gewählt wird nach dem t-Wert der Netto-R je Trade (Durchschnitt / Streuung × √Anzahl) – stabil statt
     maximaler Gewinn. Mindestens min_trades Trades im Lernfenster, und t mindestens min_t; sonst setzt der Finder
     den Abschnitt aus (kein Handel ist besser als Raten).
  3. Deflated Sharpe Ratio (Bailey/López de Prado): Wie wahrscheinlich ist das Ergebnis echt, wenn man bedenkt,
     wie viele Varianten probiert wurden? Gerechnet für die aktuelle Wahl (über alle Varianten des Setups) und für
     das beste Setup (über alle Setups, weil auch das Setup nachträglich ausgesucht wird).
  4. Ampel je Setup:
       grün  = Out-of-Sample mind. 30 Trades, im Plus mit konservativen UND harten Kosten, Profit-Faktor ≥ 1,15,
               mind. 60 % der Abschnitte mit Handel im Plus, Deflated Sharpe ≥ 0,90 -> Kandidat für das Demokonto
       gelb  = Out-of-Sample im Plus (konservativ, in $ und in R je Trade), aber nicht alles davon -> beobachten
       rot   = Out-of-Sample im Minus oder zu wenige Trades -> kein Vorteil gefunden
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

from .kandidaten import Kandidat
from .rechnen import Trades, tstat

EULER = 0.5772156649
ND = NormalDist()


@dataclass
class Regeln:
    oos_monate: int = 3
    lern_monate: int = 36
    min_lern_monate: int = 6
    min_trades: int = 30
    min_t: float = 1.0
    # Ampel grün
    gruen_trades: int = 30
    gruen_pf: float = 1.15
    gruen_fenster: float = 0.6
    gruen_dsr: float = 0.90


# ============================================================================ Zeitfenster
def fenster(start: pd.Timestamp, ende: pd.Timestamp, r: Regeln) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Out-of-Sample-Abschnitte [a, b). Der erste beginnt nach max(min_lern_monate, 40 % der Historie)."""
    start, ende = pd.Timestamp(start), pd.Timestamp(ende)
    span_m = (ende - start).days / 30.44
    lern = max(r.min_lern_monate, int(math.ceil(0.4 * span_m)))
    a = (start + pd.DateOffset(months=lern)).normalize().replace(day=1)
    out = []
    while a < ende - pd.Timedelta(days=20):
        b = min(a + pd.DateOffset(months=r.oos_monate), ende)
        out.append((a, b))
        a = b
    return out


def _ns(ts) -> int:
    return int(pd.Timestamp(ts).as_unit("ns").value)


# ============================================================================ Kennzahlen
def kennzahlen(tr: Trades) -> dict:
    n = len(tr)
    if n == 0:
        return {"trades": 0, "summe_usd": 0.0, "erwartung_r": None, "t": 0.0, "pf": None, "gewinnrate": None,
                "max_dd_usd": 0.0}
    pnl = tr.pnl
    gp, gl = float(pnl[pnl > 0].sum()), float(-pnl[pnl < 0].sum())
    eq = np.cumsum(pnl)
    dd = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq
    return {"trades": n, "summe_usd": round(float(pnl.sum()), 2), "summe_r": round(float(tr.r.sum()), 1),
            "erwartung_r": round(float(tr.r.mean()), 3), "t": round(tstat(tr.r), 2),
            "pf": round(gp / gl, 2) if gl > 0 else None, "gewinnrate": round(float((pnl > 0).mean()), 3),
            "max_dd_usd": round(float(dd.max()), 2)}


def sharpe(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r)) / sd if sd > 0 else 0.0


def deflated_sharpe(r: np.ndarray, sr_alle: np.ndarray) -> float | None:
    """Wahrscheinlichkeit, dass der wahre Sharpe-Wert (je Trade) über 0 liegt, nach Abzug des Glücks, das man
    beim Probieren von len(sr_alle) Varianten erwartet (Bailey & López de Prado 2014)."""
    t = len(r)
    sr_alle = np.asarray([x for x in sr_alle if np.isfinite(x)])
    if t < 10 or len(sr_alle) < 1:
        return None
    sr = sharpe(r)
    n = len(sr_alle)
    v = float(np.var(sr_alle, ddof=1)) if n > 1 else 0.0
    if n > 1 and v > 0:
        sr0 = math.sqrt(v) * ((1 - EULER) * ND.inv_cdf(1 - 1 / n) + EULER * ND.inv_cdf(1 - 1 / (n * math.e)))
    else:
        sr0 = 0.0
    x = r - r.mean()
    sd = float(np.std(r))
    skew = float((x ** 3).mean() / sd ** 3) if sd > 0 else 0.0
    kurt = float((x ** 4).mean() / sd ** 4) if sd > 0 else 3.0
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        return None
    return round(ND.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(den)), 4)


# ============================================================================ Walk-Forward je Setup
@dataclass
class Wahl:
    a: str
    b: str
    key: str | None
    lern_t: float | None
    lern_trades: int
    oos: dict = field(default_factory=dict)


@dataclass
class SetupErgebnis:
    tf: str
    sitzung: str
    varianten: int
    fenster: list[Wahl]
    oos: Trades
    oos_keys: list[str]                       # Kandidat je OOS-Trade
    kz: dict
    aktuell: str | None = None                # aktuelle Wahl (Lernfenster bis heute)
    aktuell_kz: dict = field(default_factory=dict)
    aktuell_dsr: float | None = None
    kz_hart: dict = field(default_factory=dict)
    oos_hart: Trades | None = None
    dsr_oos: float | None = None
    ampel: str = "rot"
    gruende: list[str] = field(default_factory=list)
    vergleich_halten: dict = field(default_factory=dict)


def _lern_auswahl(cands: dict[str, Trades], a_ns: int, b_ns: int, r: Regeln):
    """Beste Regel im Lernfenster [a, b): nur Trades, die darin eröffnet UND geschlossen wurden."""
    best_key, best_t, best_n = None, -math.inf, 0
    srs = []
    for key, tr in cands.items():
        i0 = np.searchsorted(tr.entry, a_ns, "left")
        i1 = np.searchsorted(tr.entry, b_ns, "left")
        if i1 - i0 < r.min_trades:
            continue
        m = tr.exit[i0:i1] < b_ns
        rr = tr.r[i0:i1][m]
        if len(rr) < r.min_trades:
            continue
        t = tstat(rr)
        srs.append(sharpe(rr))
        if t > best_t or (t == best_t and key < (best_key or "~")):
            best_key, best_t, best_n = key, t, len(rr)
    return best_key, best_t, best_n, np.array(srs)


def _oos_sammeln(picks: list[tuple[int, int, str | None]], cands: dict[str, Trades]):
    """Trades der jeweils gewählten Regel in ihrem Abschnitt; höchstens eine Position zur Zeit."""
    teile, keys = [], []
    last_exit = -1
    for a_ns, b_ns, key in picks:
        if key is None or key not in cands:
            continue
        tr = cands[key]
        i0 = np.searchsorted(tr.entry, a_ns, "left")
        i1 = np.searchsorted(tr.entry, b_ns, "left")
        sel = []
        for i in range(i0, i1):
            if tr.entry[i] >= last_exit:
                sel.append(i)
                last_exit = tr.exit[i]
        if sel:
            teile.append(tr.nimm(np.array(sel)))
            keys += [key] * len(sel)
    return Trades.verbinden(teile), keys


def walk_forward(cands: dict[str, Trades], start, ende, r: Regeln) -> tuple[list[Wahl], Trades, list[str]]:
    wins = fenster(start, ende, r)
    picks, wahl = [], []
    for a, b in wins:
        la = max(pd.Timestamp(start), a - pd.DateOffset(months=r.lern_monate))
        key, t, n, _ = _lern_auswahl(cands, _ns(la), _ns(a), r)
        if key is not None and t < r.min_t:
            key = None
        picks.append((_ns(a), _ns(b), key))
        wahl.append(Wahl(str(a.date()), str(b.date()), key, round(t, 2) if np.isfinite(t) else None, n))
    oos, keys = _oos_sammeln(picks, cands)
    # Ergebnis je Abschnitt
    for w, (a_ns, b_ns, _) in zip(wahl, picks):
        m = (oos.entry >= a_ns) & (oos.entry < b_ns)
        w.oos = kennzahlen(oos.nimm(m))
    return wahl, oos, keys


def bewerte_setup(tf: str, sitzung: str, cands: dict[str, Trades], start, ende, r: Regeln,
                  hart: dict[str, Trades] | None = None) -> SetupErgebnis:
    wahl, oos, keys = walk_forward(cands, start, ende, r)
    res = SetupErgebnis(tf, sitzung, len(cands), wahl, oos, keys, kennzahlen(oos))
    # aktuelle Wahl: Lernfenster bis zum Datenende
    la = max(pd.Timestamp(start), pd.Timestamp(ende) - pd.DateOffset(months=r.lern_monate))
    key, t, n, srs = _lern_auswahl(cands, _ns(la), _ns(ende) + 1, r)
    if key is not None:
        tr = cands[key]
        m = tr.entry >= _ns(la)
        sub = tr.nimm(m)
        res.aktuell = key if t >= r.min_t else None
        res.aktuell_kz = kennzahlen(sub) | {"lern_von": str(la.date())}
        res.aktuell_dsr = deflated_sharpe(sub.r, srs)
    if hart is not None:
        picks = [(_ns(w.a), _ns(w.b), w.key) for w in wahl]
        res.oos_hart, _ = _oos_sammeln(picks, hart)
        res.kz_hart = kennzahlen(res.oos_hart)
    return res


def ampel(res: SetupErgebnis, r: Regeln) -> None:
    k, kh = res.kz, res.kz_hart
    gr = []
    aktiv = [w for w in res.fenster if w.oos.get("trades")]
    pos = sum(1 for w in aktiv if w.oos["summe_usd"] > 0)
    anteil = pos / len(aktiv) if aktiv else 0.0
    if k["trades"] < 10 or k["summe_usd"] <= 0 or (k.get("erwartung_r") or 0) <= 0:
        res.ampel = "rot"
        if k["trades"] < 10:
            gr.append(f"nur {k['trades']} Out-of-Sample-Trades")
        elif k["summe_usd"] <= 0:
            gr.append("Out-of-Sample im Minus")
        else:
            gr.append("Out-of-Sample im Durchschnitt unter 0 R je Trade (Plus nur durch wenige große Trades)")
    else:
        ok = True
        if k["trades"] < r.gruen_trades:
            ok = False
            gr.append(f"nur {k['trades']} Out-of-Sample-Trades (mind. {r.gruen_trades})")
        if not kh or kh.get("summe_usd", 0) <= 0:
            ok = False
            gr.append("mit harten Kosten im Minus")
        if (k.get("pf") or 0) < r.gruen_pf:
            ok = False
            gr.append(f"Profit-Faktor {_de(k.get('pf'))} (mind. {_de(r.gruen_pf)})")
        if anteil < r.gruen_fenster:
            ok = False
            gr.append(f"nur {pos} von {len(aktiv)} Abschnitten im Plus")
        if res.dsr_oos is None or res.dsr_oos < r.gruen_dsr:
            ok = False
            gr.append(f"Deflated Sharpe {_de(res.dsr_oos)} (mind. {_de(r.gruen_dsr)}) – das Ergebnis könnte Glück sein")
        res.ampel = "gruen" if ok else "gelb"
    res.gruende = gr


def _de(x) -> str:
    return "–" if x is None else f"{x:.2f}".replace(".", ",")


def setup_dsr(alle: list[SetupErgebnis]) -> None:
    """Deflated Sharpe der Out-of-Sample-Trades je Setup, bezogen auf die Zahl der verglichenen Setups."""
    srs = np.array([sharpe(s.oos.r) for s in alle if len(s.oos) >= 10])
    for s in alle:
        s.dsr_oos = deflated_sharpe(s.oos.r, srs) if len(s.oos) >= 10 else None


def rangliste(alle: list[SetupErgebnis]) -> list[SetupErgebnis]:
    rang = {"gruen": 0, "gelb": 1, "rot": 2}
    return sorted(alle, key=lambda s: (rang[s.ampel], -(s.kz.get("t") or 0), -s.kz.get("summe_usd", 0)))


def keys_zu_kandidat(keys, by_key: dict[str, Kandidat]) -> list[Kandidat]:
    return [by_key[k] for k in keys if k in by_key]
