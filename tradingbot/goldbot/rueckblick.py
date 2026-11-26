"""Rückblick: die Gold-Bot-Regeln Kerze für Kerze über vergangene M15-Kerzen laufen lassen (ohne Orders, ohne KI).

Je Kerze wird nur mit den bis dahin abgeschlossenen Kerzen analysiert (wie im Live-Betrieb). Ausführung vereinfacht und
eher vorsichtig: Limit gilt als ausgeführt, wenn eine spätere Kerze den Einstieg berührt; Plan B zum Schlusskurs der
Retest-Kerze plus Spread; liegen Stop und Ziel in derselben Kerze, zählt der Stop. Ergebnis in R (Vielfache des
Risikos) und in Kontowährung für die eingestellte Lotgröße (XAUUSD: 1 Lot = 100 Unzen).
Handelszeiten, KI und Tagesgrenzen werden NICHT nachgebildet – das ist eine Plausibilitätsprüfung, kein Beweis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import GoldConfig
from .strategie import analyse


@dataclass
class SimTrade:
    plan: str
    direction: int
    entry: float
    stop: float
    ziel1: float
    ziel2: float
    placed: int
    filled: int | None = None
    closed: int | None = None
    r: float = 0.0
    money: float = 0.0
    status: str = "wartend"
    legs: list = field(default_factory=list)       # [(ziel, offen?)]
    be: bool = False


def simulate(df: pd.DataFrame, cfg: GoldConfig, start: int | None = None, spread: float = 0.3,
             contract: float = 100.0, progress=None) -> list[SimTrade]:
    df = df.reset_index(drop=True)
    h, l_, c, t = (df[x].to_numpy(float) for x in ("high", "low", "close", "time"))
    n = len(df)
    start = start if start is not None else min(cfg.bars, n - 1)
    seen: set[str] = set()
    trades: list[SimTrade] = []
    cur: SimTrade | None = None
    vol = cfg.lot
    parts = cfg.parts

    def close_leg(tr: SimTrade, k: int, price: float, i: int) -> None:
        risk = abs(tr.entry - tr.stop)
        tr.r += tr.direction * (price - tr.entry) / risk / parts
        tr.money += tr.direction * (price - tr.entry) * contract * vol
        tr.legs[k] = (tr.legs[k][0], False)
        if not any(o for _, o in tr.legs):
            tr.status, tr.closed = "beendet", i

    for i in range(start, n):
        # 1) laufenden Trade mit Kerze i fortschreiben
        if cur is not None:
            d = cur.direction
            if cur.status == "wartend":
                touched = (h[i] >= cur.entry) if d == -1 else (l_[i] <= cur.entry)
                reached = (l_[i] <= cur.ziel1) if d == -1 else (h[i] >= cur.ziel1)
                if reached and not touched:
                    cur.status = "verpasst"
                elif touched:
                    cur.status, cur.filled = "offen", i
                elif (t[i] - t[cur.placed]) // 900 >= cfg.pending_valid_bars:
                    cur.status = "verfallen"
            if cur.status == "offen":
                stop_hit = (h[i] >= cur.stop) if d == -1 else (l_[i] <= cur.stop)
                if stop_hit:
                    for k, (_, open_) in enumerate(cur.legs):
                        if open_:
                            close_leg(cur, k, cur.stop, i)
                else:
                    for k, (z, open_) in enumerate(cur.legs):
                        if open_ and ((l_[i] <= z) if d == -1 else (h[i] >= z)):
                            close_leg(cur, k, z, i)
                    if not cur.be and ((l_[i] <= cur.ziel1) if d == -1 else (h[i] >= cur.ziel1)):
                        cur.be, cur.stop = True, cur.entry
            if cur.status != "offen" and cur.status != "wartend":
                if cur.status == "beendet":
                    trades.append(cur)
                cur = None
        # 2) Analyse mit den bis i abgeschlossenen Kerzen
        win = df.iloc[max(0, i + 1 - cfg.bars):i + 1]
        an = analyse(win, cfg)
        if cur is not None and cur.status == "wartend" and an.trend != cur.direction:
            cur = None
        if cur is None:
            for s in sorted(an.setups, key=lambda s: s.plan != "B"):
                if s.key in seen:
                    continue
                seen.add(s.key)
                if s.plan == "A":
                    entry = s.entry
                else:
                    entry = s.ref_price + s.direction * spread
                    if s.direction * (s.ziel1 - entry) <= 0:
                        continue
                legs = [(s.ziel1, True), (s.ziel2, True)] if parts == 2 else [(s.ziel2, True)]
                cur = SimTrade(s.plan, s.direction, entry, s.stop, s.ziel1, s.ziel2, i, legs=legs)
                if s.plan == "B":
                    cur.status, cur.filled = "offen", i
                break
        if progress and i % 500 == 0:
            progress(i, n)
    return trades


def summary(trades: list[SimTrade], currency: str = "USD") -> list[str]:
    if not trades:
        return ["Keine abgeschlossenen Trades im Zeitraum."]
    r = np.array([x.r for x in trades])
    m = np.array([x.money for x in trades])
    eq = np.cumsum(m)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0
    lines = [f"Trades: {len(trades)}  (Plan A {sum(x.plan == 'A' for x in trades)}, "
             f"Plan B {sum(x.plan == 'B' for x in trades)})",
             f"Gewinner: {int((r > 0.01).sum())}  |  Verlierer: {int((r < -0.01).sum())}  |  "
             f"ca. null (Stop auf Einstieg): {int((abs(r) <= 0.01).sum())}",
             f"Summe: {r.sum():+.1f} R  |  {m.sum():+.2f} {currency}  |  Durchschnitt {r.mean():+.2f} R je Trade",
             f"größter Rückgang: {dd:.2f} {currency}",
             "Hinweis: vereinfachte Simulation ohne Kommission und Handelszeiten, keine Garantie für die Zukunft."]
    return lines
