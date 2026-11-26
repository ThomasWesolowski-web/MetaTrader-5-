"""Zusätzliche Strategien für den Strategie-Finder (26.11.2026).

Gleiche Regeln wie die Bibliothek: Signal am Schlusskurs der Kerze i, nur Daten bis i, Ausführung frühestens zur
Eröffnung von i+1 (Backtester). Jede Strategie hat eindeutige Regeln, damit sie im Demo-Bot genauso läuft wie im
Finder.

Dazu der Filter-Baustein `finder_gefiltert`: eine beliebige Strategie, nur zu bestimmten Uhrzeiten (UTC) und/oder nur
in eine Richtung. So kann ein Ergebnis des Strategie-Finders unverändert als Demo-Kandidat in config.toml stehen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import features
from .base import Signals, TradeStrategy, create, register
from .library import ATR_N, _arrays, _rr_target


def _prev(x: np.ndarray, fill=np.nan) -> np.ndarray:
    return np.concatenate([[fill], x[:-1]])


def _rolling_std(c: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(c).rolling(n, min_periods=n).std(ddof=0).to_numpy()


def _pack(up: np.ndarray, dn: np.ndarray):
    iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
    idx = np.concatenate([iu, idn])
    d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
    return idx, d


# =============================================================================== Bollinger
@register
class BollingerReversion(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "bollinger_reversion"
    description = "Mean Reversion: Schluss zurück in die Bollinger-Bänder nach einem Ausreißer"
    rules = ("Bänder = SMA(n) ± k·Standardabweichung(n). Long: vorige Kerze schloss unter dem unteren Band, diese "
             "schließt wieder darüber. Stop: Schluss − atr_mult·ATR. Ausstieg: Schluss über der Mittellinie (SMA) "
             "oder nach max_bars Kerzen. Short spiegelbildlich am oberen Band.")
    default_params = {"n": 20, "k": 2.0, "atr_mult": 1.5, "max_bars": 30}
    param_grid = [{"n": n, "k": k} for n in (20, 50) for k in (2.0, 2.5)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        mid = features.get(bars).sma(p["n"])
        sd = _rolling_std(c, p["n"])
        lo, hi = mid - p["k"] * sd, mid + p["k"] * sd
        with np.errstate(invalid="ignore"):
            up = (_prev(c) < _prev(lo)) & (c > lo)
            dn = (_prev(c) > _prev(hi)) & (c < hi)
            ex_l, ex_s = c > mid, c < mid
        idx, d = _pack(up, dn)
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, max_bars=p["max_bars"],
                             exit_long=ex_l, exit_short=ex_s)


@register
class BollingerSqueeze(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "bollinger_squeeze"
    description = "Breakout: Ausbruch aus engen Bollinger-Bändern (Squeeze)"
    rules = ("Squeeze = Bandbreite (4·Std/SMA) war in den letzten 5 Kerzen die kleinste der letzten lookback Kerzen. "
             "Long: Schluss über dem oberen Band (n, 2 Std) während oder direkt nach einem Squeeze. Stop: Mittellinie "
             "(SMA n), höchstens atr_mult·ATR entfernt. Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"n": 20, "lookback": 120, "rr": 2.0, "atr_mult": 2.0}
    param_grid = [{"n": 20, "lookback": lb, "rr": rr} for lb in (60, 120) for rr in (1.5, 2.5)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        mid = features.get(bars).sma(p["n"])
        sd = _rolling_std(c, p["n"])
        with np.errstate(invalid="ignore", divide="ignore"):
            bw = 4 * sd / mid
        lowest = pd.Series(bw).rolling(p["lookback"], min_periods=p["lookback"]).min().to_numpy()
        sq = pd.Series(bw <= lowest * 1.0001).rolling(5, min_periods=1).max().to_numpy().astype(bool)
        hi, lo = mid + 2 * sd, mid - 2 * sd
        with np.errstate(invalid="ignore"):
            up = sq & (c > hi) & ~(_prev(c) > _prev(hi))
            dn = sq & (c < lo) & ~(_prev(c) < _prev(lo))
        idx, d = _pack(up, dn)
        dist = np.minimum(np.abs(c[idx] - mid[idx]), p["atr_mult"] * a[idx])
        st = c[idx] - d * dist
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]), c)


# =============================================================================== MACD
@register
class MacdTrend(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "macd_trend"
    description = "Trendfolge: MACD kreuzt die Signallinie, nur in Richtung der EMA 200"
    rules = ("MACD = EMA(fast) − EMA(slow), Signallinie = EMA(9) des MACD. Long: MACD kreuzt über die Signallinie "
             "und Schluss > EMA(trend). Stop: Schluss − atr_mult·ATR. Ausstieg: Gegenkreuzung. Short spiegelbildlich.")
    default_params = {"fast": 12, "slow": 26, "signal": 9, "trend": 200, "atr_mult": 2.0}
    param_grid = [{"fast": f, "slow": s, "atr_mult": m} for f, s in ((12, 26), (24, 52)) for m in (1.5, 2.5)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        fe = features.get(bars)
        macd = fe.ema(p["fast"]) - fe.ema(p["slow"])
        sig = pd.Series(macd).ewm(span=p["signal"], adjust=False).mean().to_numpy()
        tr = fe.ema(p["trend"]) if p["trend"] else np.full(len(c), np.nan)
        with np.errstate(invalid="ignore"):
            above = macd > sig
            cross_up = above & ~_prev(above, False)
            cross_dn = ~above & _prev(above, False)
            trend_ok_l = (c > tr) if p["trend"] else np.ones(len(c), bool)
            trend_ok_s = (c < tr) if p["trend"] else np.ones(len(c), bool)
        valid = np.isfinite(macd) & np.isfinite(sig)
        if p["trend"]:
            valid &= np.isfinite(tr)
        idx, d = _pack(cross_up & trend_ok_l & valid, cross_dn & trend_ok_s & valid)
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, exit_long=cross_dn, exit_short=cross_up)


# =============================================================================== Supertrend
def supertrend(h, l, c, a, mult):
    """Klassischer Supertrend (Bänder aus HL2 ± mult·ATR). Rückgabe: Linie und Richtung (+1/−1) je Kerze."""
    n = len(c)
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    line = np.full(n, np.nan)
    dirn = np.zeros(n, dtype=int)
    start = int(np.argmax(np.isfinite(a))) if np.isfinite(a).any() else n
    for i in range(start, n):
        if i == start:
            dirn[i] = 1
            line[i] = flb[i]
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if c[i] < flb[i] else 1
        else:
            dirn[i] = 1 if c[i] > fub[i] else -1
        line[i] = flb[i] if dirn[i] == 1 else fub[i]
    return line, dirn


@register
class SupertrendFlip(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "supertrend"
    description = "Trendfolge: Supertrend wechselt die Richtung"
    rules = ("Supertrend aus HL2 ± mult·ATR(atr_n). Long: Richtung wechselt auf aufwärts. Stop: Supertrend-Linie "
             "(höchstens 3·ATR entfernt). Ausstieg: Richtungswechsel abwärts. Short spiegelbildlich.")
    default_params = {"atr_n": 10, "mult": 3.0}
    param_grid = [{"atr_n": n, "mult": m} for n in (10, 20) for m in (2.0, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        fe = features.get(bars)
        a_st = fe.atr(p["atr_n"])
        line, dirn = fe._get(("supertrend", p["atr_n"], p["mult"]),
                             lambda: supertrend(h, l, c, a_st, p["mult"]))
        pd_ = _prev(dirn, 0)
        flip_up = (dirn == 1) & (pd_ == -1)
        flip_dn = (dirn == -1) & (pd_ == 1)
        idx, d = _pack(flip_up, flip_dn)
        dist = np.minimum(np.abs(c[idx] - line[idx]), 3 * a[idx])
        st = c[idx] - d * dist
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, exit_long=flip_dn, exit_short=flip_up)


# =============================================================================== Vortag
@register
class VortagBreakout(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "vortag_breakout"
    description = "Breakout: Schluss über dem Vortageshoch (unter dem Vortagestief), glatt zum Tagesende"
    rules = ("Tage nach UTC. Long: erster Schluss des Tages über dem Hoch des Vortags. Stop: Schluss − atr_mult·ATR. "
             "Ziel: rr × Risiko. Spätestens mit der letzten Kerze des UTC-Tages raus (Ausführung zur nächsten "
             "Eröffnung). Short spiegelbildlich am Vortagestief. Höchstens ein Trade je Tag und Richtung.")
    default_params = {"rr": 2.0, "atr_mult": 1.5}
    param_grid = [{"rr": rr, "atr_mult": m} for rr in (1.5, 3.0) for m in (1.0, 2.0)]
    timeframes = ("M5", "M15", "M30", "H1")

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        t = pd.DatetimeIndex(bars["time"])
        day = t.normalize().as_unit("ns").asi8
        g = pd.DataFrame({"d": day, "h": h, "l": l}).groupby("d").agg(h=("h", "max"), l=("l", "min"))
        prev_h = pd.Series(day).map(g["h"].shift(1)).to_numpy(float)
        prev_l = pd.Series(day).map(g["l"].shift(1)).to_numpy(float)
        with np.errstate(invalid="ignore"):
            up0, dn0 = c > prev_h, c < prev_l
        first = lambda m: m & (pd.Series(m).groupby(day).cumsum().to_numpy() == 1)
        up, dn = first(up0), first(dn0)
        tf_s = _tf_seconds(t)
        # letzte Kerze des Tages = sie endet um 24:00 UTC (aus der eigenen Uhrzeit, ohne Blick auf die nächste Kerze)
        last_of_day = ((t + pd.Timedelta(seconds=tf_s)).normalize() != t.normalize())
        last_of_day = np.asarray(last_of_day, dtype=bool)
        idx, d = _pack(up & ~last_of_day, dn & ~last_of_day)
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]), c,
                             exit_long=last_of_day, exit_short=last_of_day)


# =============================================================================== Inside Bar
@register
class InsideBarBreakout(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "inside_bar"
    description = "Breakout: Ausbruch aus einer Inside Bar (Kerze innerhalb der Mutterkerze)"
    rules = ("Inside Bar = Hoch und Tief innerhalb der vorigen Kerze (Mutterkerze). Long: innerhalb von 3 Kerzen "
             "danach erster Schluss über dem Hoch der Mutterkerze, mit Trendfilter Schluss > EMA(trend). Stop: Tief "
             "der Mutterkerze. Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"rr": 2.0, "trend": 50, "wait": 3}
    param_grid = [{"rr": rr, "trend": tr} for rr in (1.5, 2.5) for tr in (50, 200)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        e = features.get(bars).ema(p["trend"])
        n = len(c)
        inside = np.zeros(n, bool)
        inside[1:] = (h[1:] <= h[:-1]) & (l[1:] >= l[:-1])
        out_i, out_d, out_s = [], [], []
        for k in np.nonzero(inside)[0]:
            mh, ml = h[k - 1], l[k - 1]
            for j in range(k + 1, min(n, k + 1 + p["wait"])):
                if c[j] > mh:
                    if c[j] > e[j]:
                        out_i.append(j)
                        out_d.append(1)
                        out_s.append(ml)
                    break
                if c[j] < ml:
                    if c[j] < e[j]:
                        out_i.append(j)
                        out_d.append(-1)
                        out_s.append(mh)
                    break
        idx, d, st = np.array(out_i, int), np.array(out_d, int), np.array(out_s, float)
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]) if len(idx) else st, c)


# =============================================================================== Pullback
@register
class EmaPullback(TradeStrategy):
    nur_finder = True          # nur im Strategie-Finder; der allgemeine Backtest (Phase 3) bleibt unverändert
    name = "ema_pullback"
    description = "Trend-Pullback: im Aufwärtstrend (EMA 50 > EMA 200) Rücksetzer an die EMA(fast), dann Schluss darüber"
    rules = ("Long: EMA 50 > EMA 200, das Tief der vorigen Kerze berührte die EMA(fast) oder lag darunter, diese "
             "Kerze schließt über der EMA(fast) und über ihrer Eröffnung. Stop: tiefstes Tief der letzten 5 Kerzen "
             "− 0,1·ATR (mindestens 0,5·ATR, höchstens 3·ATR). Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"fast": 20, "rr": 2.0}
    param_grid = [{"fast": f, "rr": rr} for f in (20, 50) for rr in (1.5, 2.5)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        fe = features.get(bars)
        ef, e50, e200 = fe.ema(p["fast"]), fe.ema(50), fe.ema(200)
        lo5 = pd.Series(l).rolling(5, min_periods=5).min().to_numpy()
        hi5 = pd.Series(h).rolling(5, min_periods=5).max().to_numpy()
        with np.errstate(invalid="ignore"):
            up = (e50 > e200) & (_prev(l) <= _prev(ef)) & (c > ef) & (c > o)
            dn = (e50 < e200) & (_prev(h) >= _prev(ef)) & (c < ef) & (c < o)
        idx, d = _pack(up, dn)
        raw = np.where(d == 1, c[idx] - (lo5[idx] - 0.1 * a[idx]), (hi5[idx] + 0.1 * a[idx]) - c[idx])
        dist = np.clip(raw, 0.5 * a[idx], 3 * a[idx])
        st = c[idx] - d * dist
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]), c)


# =============================================================================== Filter für Finder-Ergebnisse
SITZUNGEN = {
    "alle": (0, 24),
    "asien": (0, 7),
    "london": (7, 12),
    "new_york": (12, 17),
}


@register
class FinderGefiltert(TradeStrategy):
    name = "finder_gefiltert"
    description = "Ergebnis des Strategie-Finders: Grundstrategie, nur zu bestimmten Uhrzeiten und/oder in eine Richtung"
    rules = ("Signale der Grundstrategie 'basis' mit 'basis_params'. Es zählen nur Signale, deren Einstiegskerze "
             "(Schluss der Signalkerze) zwischen von_utc und bis_utc Uhr UTC beginnt, und – wenn richtung nicht 0 ist "
             "– nur Käufe (+1) bzw. nur Verkäufe (−1). Stop, Ziel und Ausstieg wie die Grundstrategie.")
    default_params = {"basis": "ema_trend", "basis_params": {}, "von_utc": 0, "bis_utc": 24, "richtung": 0}
    param_grid = [{}]
    timeframes = ("M5", "M15", "M30", "H1", "H4")
    nur_finder = True          # nicht im allgemeinen Backtest (backtest_ausfuehren.py) – dort wären es Dubletten

    def __init__(self, **params):
        super().__init__(**params)
        if self.params["basis"] == self.name:
            raise ValueError("finder_gefiltert: Grundstrategie darf nicht finder_gefiltert sein")
        self.base = create(self.params["basis"], **dict(self.params["basis_params"] or {}))
        self.warmup_seconds = getattr(self.base, "warmup_seconds", 0)

    def entries(self, bars):
        sig = self.base.entries(bars)
        return filter_signals(sig, bars, self.params["von_utc"], self.params["bis_utc"], self.params["richtung"])


def entry_hours(bars: pd.DataFrame) -> np.ndarray:
    """Stunde (UTC), zu der die Einstiegskerze beginnt = Schluss der Signalkerze."""
    t = pd.DatetimeIndex(bars["time"])
    return (t + pd.Timedelta(seconds=_tf_seconds(t))).hour.to_numpy()


def _tf_seconds(t: pd.DatetimeIndex) -> int:
    d = np.diff(t.as_unit("ns").asi8)
    d = d[d > 0]
    return int(d.min() // 10**9) if len(d) else 0


def filter_signals(sig: Signals, bars: pd.DataFrame, von: int, bis: int, richtung: int,
                   hours: np.ndarray | None = None) -> Signals:
    if len(sig) == 0 or ((von, bis) == (0, 24) and not richtung):
        return sig
    hrs = entry_hours(bars) if hours is None else hours
    hh = hrs[sig.idx]
    keep = (hh >= von) & (hh < bis) if von < bis else (hh >= von) | (hh < bis)
    if richtung:
        keep &= sig.direction == richtung
    return Signals(sig.idx[keep], sig.direction[keep], sig.stop[keep], sig.target[keep], sig.max_bars,
                   sig.exit_long, sig.exit_short)


__all__ = ["SITZUNGEN", "filter_signals", "supertrend", "ATR_N"]
