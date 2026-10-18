"""Strategiebibliothek (Phase 3).

Jede Strategie hat eindeutige, programmierbare Regeln. Signale entstehen am Schlusskurs der
Kerze i und nutzen nur Daten bis i; ausgeführt wird bei der Eröffnung von i+1 (Backtester).
Strukturpunkte (Swing-Hochs/-Tiefs) gelten erst nach Bestätigung durch k Folgekerzen.

Gemeinsame Bausteine: ATR(14), EMA, RSI, bestätigte Swing-Punkte, Break of Structure (BOS),
Market Structure Shift (MSS), Fair Value Gaps (FVG) – siehe tradingbot/indicators.py.
Kursziele werden als Vielfaches (rr) des Abstands Schlusskurs–Stop gesetzt.

Neue Strategien: Klasse von TradeStrategy ableiten, @register, entries() implementieren.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import Signals, TradeStrategy, register

ATR_N = 14
STOP_BUFFER_ATR = 0.1   # Puffer jenseits des Strukturpunkts, damit der Stop nicht exakt am Extrem liegt


def _arrays(bars: pd.DataFrame):
    o = bars["open"].to_numpy(dtype=float)
    h = bars["high"].to_numpy(dtype=float)
    l = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    return o, h, l, c, ind.atr(h, l, c, ATR_N)


def _rr_target(c, idx, d, stop, rr):
    return c[idx] + d * rr * np.abs(c[idx] - stop)


def _first_per_level(mask: np.ndarray, level: np.ndarray) -> np.ndarray:
    """Nur das erste True je (fortlaufendem) Niveau."""
    lid = pd.Series(level).ne(pd.Series(level).shift()).cumsum().to_numpy()
    return mask & (pd.Series(mask).groupby(lid).cumsum().to_numpy() == 1)


# =============================================================================== 1. SMC
@register
class SmcOrderBlock(TradeStrategy):
    name = "smc_orderblock"
    description = "Smart Money Concepts: Trend per Break of Structure, Einstieg am Order Block"
    rules = ("Long: Schlusskurs bricht das letzte bestätigte Swing-Hoch (BOS). Order Block = letzte bärische "
             "Kerze in den 10 Kerzen davor. Einstieg, wenn der Kurs innerhalb von 50 Kerzen erstmals in den Order "
             "Block zurückkommt (Tief <= OB-Hoch, Schluss >= OB-Tief), ohne vorher darunter zu schließen. "
             "Stop: OB-Tief − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"k": 5, "rr": 2.0, "ob_lookback": 10, "max_wait": 50}
    param_grid = [{"k": k, "rr": rr} for k in (3, 5) for rr in (2.0, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        sh, sl, _, _ = ind.swing_points(h, l, p["k"])
        bos_up, bos_dn, _, _, _ = ind.market_structure(c, sh, sl)
        n = len(c)
        pos = np.arange(n, dtype=float)
        out_i, out_d, out_s = [], [], []
        for d, bos, cand in ((1, bos_up, c < o), (-1, bos_dn, c > o)):
            last = pd.Series(np.where(cand, pos, np.nan)).ffill().shift(1).to_numpy()  # letzte Gegenkerze vor i
            ev = np.nonzero(bos & np.isfinite(last) & (pos - last <= p["ob_lookback"]))[0]
            j = last[ev].astype(int)
            lo = np.full(n, np.nan)
            hi = np.full(n, np.nan)
            lo[ev], hi[ev] = l[j], h[j]
            for e, t in ind.first_touch_after(ev, lo, hi, d, h, l, c, p["max_wait"]):
                out_i.append(t)
                out_d.append(d)
                out_s.append(lo[e] - STOP_BUFFER_ATR * a[t] if d == 1 else hi[e] + STOP_BUFFER_ATR * a[t])
        idx, d, s = np.array(out_i, int), np.array(out_d, int), np.array(out_s)
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]) if len(idx) else s, c)


# =============================================================================== 2. ICT
@register
class IctSilverBullet(TradeStrategy):
    name = "ict_silver_bullet"
    description = "ICT: Fair Value Gap in den Killzones (London 3–4 Uhr, New York 10–11 und 14–15 Uhr NY-Zeit)"
    rules = ("Ein FVG (Mindestgröße min_fvg·ATR) entsteht in einer Killzone. Einstieg, wenn der Kurs innerhalb von "
             "12 Kerzen – ebenfalls in einer Killzone – erstmals in die Lücke zurückkehrt. Stop: jenseits der ersten "
             "Kerze des FVG (± 0,1·ATR). Ziel: rr × Risiko.")
    default_params = {"rr": 2.0, "min_fvg": 0.2, "max_wait": 12, "windows": ((3, 4), (10, 11), (14, 15))}
    param_grid = [{"rr": rr, "min_fvg": m} for rr in (1.5, 2.5) for m in (0.1, 0.3)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        kz = ind.in_session(pd.DatetimeIndex(bars["time"]), list(p["windows"]))
        bull, bear, bottom, top = ind.fair_value_gaps(h, l, a, p["min_fvg"])
        l2 = np.concatenate([[np.nan, np.nan], np.fmin(l[:-2], l[1:-1])])
        h2 = np.concatenate([[np.nan, np.nan], np.fmax(h[:-2], h[1:-1])])
        out_i, out_d, out_s = [], [], []
        for d, ev_mask, stop_ref in ((1, bull, l2), (-1, bear, h2)):
            ev = np.nonzero(ev_mask & kz)[0]
            for e, t in ind.first_touch_after(ev, bottom, top, d, h, l, c, p["max_wait"]):
                if kz[t]:
                    out_i.append(t)
                    out_d.append(d)
                    out_s.append(stop_ref[e] - d * STOP_BUFFER_ATR * a[t])
        idx, d, s = np.array(out_i, int), np.array(out_d, int), np.array(out_s)
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]) if len(idx) else s, c)


# =============================================================================== 3. Liquidity Sweep
@register
class LiquiditySweep(TradeStrategy):
    name = "liquidity_sweep"
    description = "Liquidity Sweep: Kurs sticht unter ein Swing-Tief (über ein Swing-Hoch) und schließt zurück"
    rules = ("Long: Tief der Kerze < letztes bestätigtes Swing-Tief, Schluss > dieses Swing-Tief und Schluss in der "
             "oberen Hälfte der Kerze (nur erster Sweep je Niveau). Stop: Kerzentief − 0,1·ATR. Ziel: rr × Risiko. "
             "Short spiegelbildlich.")
    default_params = {"k": 5, "rr": 2.0}
    param_grid = [{"k": k, "rr": rr} for k in (3, 5) for rr in (1.5, 2.5)]

    @staticmethod
    def sweeps(h, l, c, k):
        sh, sl, _, _ = ind.swing_points(h, l, k)
        sl_prev = np.concatenate([[np.nan], sl[:-1]])
        sh_prev = np.concatenate([[np.nan], sh[:-1]])
        mid = (h + l) / 2
        with np.errstate(invalid="ignore"):
            up = (l < sl_prev) & (c > sl_prev) & (c >= mid)
            dn = (h > sh_prev) & (c < sh_prev) & (c <= mid)
        return _first_per_level(up, sl_prev), _first_per_level(dn, sh_prev)

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        up, dn = self.sweeps(h, l, c, p["k"])
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        s = np.where(d == 1, l[idx] - STOP_BUFFER_ATR * a[idx], h[idx] + STOP_BUFFER_ATR * a[idx])
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]), c)


# =============================================================================== 4. FVG
@register
class FvgRetrace(TradeStrategy):
    name = "fvg_retrace"
    description = "Fair Value Gap: Rücklauf in eine Lücke in Trendrichtung (EMA 200)"
    rules = ("Bullisches FVG (Größe >= min_fvg·ATR) bei Schluss über EMA 200. Einstieg beim ersten Rücklauf in die "
             "Lücke innerhalb von 20 Kerzen (ohne Schluss unter der Lücke). Stop: Lückenunterkante − 0,2·ATR. "
             "Ziel: rr × Risiko. Short spiegelbildlich unter EMA 200.")
    default_params = {"min_fvg": 0.3, "rr": 2.0, "max_wait": 20, "trend_ema": 200}
    param_grid = [{"min_fvg": m, "rr": rr} for m in (0.3, 0.6) for rr in (1.5, 2.5)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        e200 = ind.ema(c, p["trend_ema"])
        bull, bear, bottom, top = ind.fair_value_gaps(h, l, a, p["min_fvg"])
        out_i, out_d, out_s = [], [], []
        with np.errstate(invalid="ignore"):
            masks = ((1, bull & (c > e200)), (-1, bear & (c < e200)))
        for d, m in masks:
            for e, t in ind.first_touch_after(np.nonzero(m)[0], bottom, top, d, h, l, c, p["max_wait"]):
                out_i.append(t)
                out_d.append(d)
                out_s.append(bottom[e] - 0.2 * a[t] if d == 1 else top[e] + 0.2 * a[t])
        idx, d, s = np.array(out_i, int), np.array(out_d, int), np.array(out_s)
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]) if len(idx) else s, c)


# =============================================================================== 5. MSS
@register
class MarketStructureShift(TradeStrategy):
    name = "mss"
    description = "Market Structure Shift: erster Bruch gegen die bisherige Struktur"
    rules = ("Long: Abwärtsstruktur (letzter BOS abwärts) und Schlusskurs bricht über das letzte bestätigte "
             "Swing-Hoch. Stop: letztes bestätigtes Swing-Tief − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"k": 5, "rr": 2.0}
    param_grid = [{"k": k, "rr": rr} for k in (3, 5) for rr in (2.0, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        sh, sl, _, _ = ind.swing_points(h, l, p["k"])
        _, _, _, mss_up, mss_dn = ind.market_structure(c, sh, sl)
        iu, idn = np.nonzero(mss_up)[0], np.nonzero(mss_dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        s = np.where(d == 1, sl[idx] - STOP_BUFFER_ATR * a[idx], sh[idx] + STOP_BUFFER_ATR * a[idx])
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]), c)


# =============================================================================== 6. Breakout
@register
class DonchianBreakout(TradeStrategy):
    name = "donchian_breakout"
    description = "Breakout: Schluss über dem n-Kerzen-Hoch (Donchian-Kanal)"
    rules = ("Long: Schluss > Hoch der vorherigen n Kerzen (erste Kerze des Ausbruchs). Stop: Schluss − "
             "atr_mult·ATR. Ausstieg: Schluss < Tief der vorherigen n/2 Kerzen oder nach 5·n Kerzen. "
             "Short spiegelbildlich.")
    default_params = {"n": 20, "atr_mult": 2.0}
    param_grid = [{"n": n, "atr_mult": m} for n in (20, 55) for m in (1.5, 2.5)]

    @staticmethod
    def breakouts(h, l, c, n):
        up_lvl, dn_lvl = ind.rolling_max_prev(h, n), ind.rolling_min_prev(l, n)
        with np.errstate(invalid="ignore"):
            up, dn = c > up_lvl, c < dn_lvl
        up &= ~np.concatenate([[False], up[:-1]])
        dn &= ~np.concatenate([[False], dn[:-1]])
        return up, dn

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        up, dn = self.breakouts(h, l, c, p["n"])
        m = max(2, p["n"] // 2)
        with np.errstate(invalid="ignore"):
            ex_l = c < ind.rolling_min_prev(l, m)
            ex_s = c > ind.rolling_max_prev(h, m)
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        s = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, s, np.full(len(idx), np.nan), c, max_bars=5 * p["n"],
                             exit_long=ex_l, exit_short=ex_s)


# =============================================================================== 7. EMA-Trend
@register
class EmaTrend(TradeStrategy):
    name = "ema_trend"
    description = "Trendfolge: Kreuzung schnelle/langsame EMA"
    rules = ("Long: schnelle EMA kreuzt über die langsame. Stop: Schluss − atr_mult·ATR. Ausstieg: Kreuzung in "
             "Gegenrichtung. Short spiegelbildlich.")
    default_params = {"fast": 20, "slow": 50, "atr_mult": 2.5}
    param_grid = [{"fast": f, "slow": s, "atr_mult": m} for f, s in ((20, 50), (50, 200)) for m in (2.0, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        f, s_ = ind.ema(c, p["fast"]), ind.ema(c, p["slow"])
        with np.errstate(invalid="ignore"):
            above = f > s_
        prev = np.concatenate([[False], above[:-1]])
        valid = np.isfinite(f) & np.isfinite(s_) & np.concatenate([[False], np.isfinite(s_[:-1])])
        up, dn = above & ~prev & valid, ~above & prev & valid
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, exit_long=dn, exit_short=up)


# =============================================================================== 8. RSI Mean Reversion
@register
class RsiMeanReversion(TradeStrategy):
    name = "rsi_mean_reversion"
    description = "RSI Mean Reversion mit Trendfilter (EMA 200)"
    rules = ("Long: RSI fällt unter 'lower' und Schluss > EMA 200. Stop: Schluss − atr_mult·ATR. Ausstieg: RSI > 50 "
             "oder nach 20 Kerzen. Short: RSI steigt über 'upper' und Schluss < EMA 200.")
    default_params = {"rsi_n": 2, "lower": 10.0, "upper": 90.0, "atr_mult": 2.0, "trend_ema": 200, "max_bars": 20}
    param_grid = [{"rsi_n": n, "lower": lo, "upper": up, "atr_mult": m}
                  for n, lo, up in ((2, 10.0, 90.0), (14, 30.0, 70.0)) for m in (1.5, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        r = ind.rsi(c, p["rsi_n"])
        e = ind.ema(c, p["trend_ema"])
        rp = np.concatenate([[np.nan], r[:-1]])
        with np.errstate(invalid="ignore"):
            up = (r < p["lower"]) & (rp >= p["lower"]) & (c > e)
            dn = (r > p["upper"]) & (rp <= p["upper"]) & (c < e)
            ex_l, ex_s = r > 50, r < 50
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, max_bars=p["max_bars"],
                             exit_long=ex_l, exit_short=ex_s)


# =============================================================================== 9. Momentum
@register
class Momentum(TradeStrategy):
    name = "momentum"
    description = "Momentum: volatilitätsnormierte n-Kerzen-Rendite über Schwelle, mit EMA-50-Filter"
    rules = ("Momentum = (Schluss − Schluss vor n Kerzen) / (ATR·√n). Long: Momentum steigt über z und Schluss > "
             "EMA 50. Stop: Schluss − 1,5·ATR. Ziel: 2 × Risiko. Zeitstopp 2·n Kerzen. Short spiegelbildlich.")
    default_params = {"n": 12, "z": 2.0, "atr_mult": 1.5, "rr": 2.0, "ema": 50}
    # Schwellen bewusst moderat: die ATR enthält Dochte, Momentum 1,75 ≈ 2,5 Standardabweichungen
    param_grid = [{"n": n, "z": z} for n in (12, 48) for z in (1.0, 1.75)]

    @staticmethod
    def score(c, a, n):
        prev = np.concatenate([np.full(n, np.nan), c[:-n]])
        with np.errstate(invalid="ignore", divide="ignore"):
            return (c - prev) / (a * np.sqrt(n))

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        m = self.score(c, a, p["n"])
        mp = np.concatenate([[np.nan], m[:-1]])
        e = ind.ema(c, p["ema"])
        with np.errstate(invalid="ignore"):
            up = (m > p["z"]) & (mp <= p["z"]) & (c > e)
            dn = (m < -p["z"]) & (mp >= -p["z"]) & (c < e)
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]), c, max_bars=2 * p["n"])


# =============================================================================== 10. Kombinationen
@register
class KombiSweepMss(TradeStrategy):
    name = "kombi_sweep_mss"
    description = "Kombination: Liquidity Sweep, danach Market Structure Shift in dieselbe Richtung"
    rules = ("Long: MSS aufwärts, und innerhalb der 20 Kerzen davor gab es einen bullischen Liquidity Sweep. "
             "Stop: tiefstes Tief seit dem Sweep − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich.")
    default_params = {"k": 5, "window": 20, "rr": 2.0}
    param_grid = [{"k": k, "rr": rr} for k in (3, 5) for rr in (2.0, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        sw_up, sw_dn = LiquiditySweep.sweeps(h, l, c, p["k"])
        sh, sl, _, _ = ind.swing_points(h, l, p["k"])
        _, _, _, mss_up, mss_dn = ind.market_structure(c, sh, sl)
        n = len(c)
        pos = np.arange(n, dtype=float)
        out_i, out_d, out_s = [], [], []
        for d, sw, mss, ext in ((1, sw_up, mss_up, l), (-1, sw_dn, mss_dn, h)):
            last_sw = pd.Series(np.where(sw, pos, np.nan)).ffill().to_numpy()
            ev = np.nonzero(mss & np.isfinite(last_sw) & (pos - last_sw <= p["window"]))[0]
            for i in ev:
                j = int(last_sw[i])
                seg = ext[j:i + 1]
                out_i.append(i)
                out_d.append(d)
                out_s.append(seg.min() - STOP_BUFFER_ATR * a[i] if d == 1 else seg.max() + STOP_BUFFER_ATR * a[i])
        idx, d, s = np.array(out_i, int), np.array(out_d, int), np.array(out_s)
        return Signals.build(idx, d, s, _rr_target(c, idx, d, s, p["rr"]) if len(idx) else s, c)


@register
class KombiTrendRsi(TradeStrategy):
    name = "kombi_trend_rsi"
    description = "Kombination: EMA-Trend (50/200) als Filter, RSI(2)-Rücksetzer als Einstieg"
    rules = ("Long: EMA 50 > EMA 200 und RSI(2) fällt unter 'lower'. Stop: Schluss − atr_mult·ATR. Ausstieg: Schluss "
             "über EMA 20 oder nach 15 Kerzen. Short spiegelbildlich (EMA 50 < EMA 200, RSI über 100 − lower).")
    default_params = {"lower": 10.0, "atr_mult": 2.0}
    param_grid = [{"lower": lo, "atr_mult": m} for lo in (5.0, 15.0) for m in (1.5, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
        r = ind.rsi(c, 2)
        rp = np.concatenate([[np.nan], r[:-1]])
        hi = 100 - p["lower"]
        with np.errstate(invalid="ignore"):
            up = (e50 > e200) & (r < p["lower"]) & (rp >= p["lower"])
            dn = (e50 < e200) & (r > hi) & (rp <= hi)
            ex_l, ex_s = c > e20, c < e20
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        st = c[idx] - d * p["atr_mult"] * a[idx]
        return Signals.build(idx, d, st, np.full(len(idx), np.nan), c, max_bars=15, exit_long=ex_l, exit_short=ex_s)


@register
class KombiBreakoutMomentum(TradeStrategy):
    name = "kombi_breakout_momentum"
    description = "Kombination: Donchian-Breakout nur mit bestätigendem Momentum und steigender Volatilität"
    rules = ("Long: Donchian-Breakout (n Kerzen) und Momentum(n) > 1 und ATR > EMA(ATR, 50). Stop: Schluss − 2·ATR. "
             "Ziel: rr × Risiko, Zeitstopp 3·n Kerzen. Short spiegelbildlich.")
    default_params = {"n": 20, "rr": 2.0}
    param_grid = [{"n": n, "rr": rr} for n in (20, 55) for rr in (1.5, 3.0)]

    def entries(self, bars):
        p = self.params
        o, h, l, c, a = _arrays(bars)
        up, dn = DonchianBreakout.breakouts(h, l, c, p["n"])
        m = Momentum.score(c, a, p["n"])
        a_ema = ind.ema(a, 50)
        with np.errstate(invalid="ignore"):
            vol_up = a > a_ema
            up &= (m > 1) & vol_up
            dn &= (m < -1) & vol_up
        iu, idn = np.nonzero(up)[0], np.nonzero(dn)[0]
        idx = np.concatenate([iu, idn])
        d = np.concatenate([np.ones(len(iu), int), -np.ones(len(idn), int)])
        st = c[idx] - d * 2.0 * a[idx]
        return Signals.build(idx, d, st, _rr_target(c, idx, d, st, p["rr"]), c, max_bars=3 * p["n"])
