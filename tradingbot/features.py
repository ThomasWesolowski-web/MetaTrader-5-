"""Gemeinsamer Indikator-Speicher je Datenblock.

Mehrere Strategien und Parametersätze brauchen dieselben Indikatoren (ATR, EMA, Swing-Punkte, Struktur,
FVG …). Features berechnet jeden Indikator je Datenblock nur einmal und gibt bei weiteren Anfragen das
gespeicherte Ergebnis zurück. Fachlich identisch, weil exakt dieselben Funktionen mit denselben Eingaben
aufgerufen werden (Test: tests/test_blocks_and_cache.py).

Gespeicherte Arrays sind schreibgeschützt: Eine Strategie, die ein gemeinsam genutztes Array verändern
würde, löst sofort einen Fehler aus, statt andere Strategien unbemerkt zu verfälschen.

Verwendung im Backtest:   with features.shared(bars): ... strategie.entries(bars) ...
In Strategien:            f = features.get(bars); a = f.atr()
Ohne shared() (z. B. Einzelaufruf) gilt der Speicher nur für diesen einen Aufruf.
"""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pandas as pd

from . import indicators as ind

_ACTIVE: dict[int, "Features"] = {}


def _ro(x):
    if isinstance(x, np.ndarray):
        x.setflags(write=False)
    elif isinstance(x, tuple):
        for v in x:
            _ro(v)
    return x


class Features:
    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self._cache: dict = {}
        self.hits = 0
        self.misses = 0

    def _get(self, key, fn):
        if key in self._cache:
            self.hits += 1
        else:
            self.misses += 1
            self._cache[key] = _ro(fn())
        return self._cache[key]

    # Rohdaten
    def col(self, name: str) -> np.ndarray:
        return self._get(("col", name), lambda: self.bars[name].to_numpy(dtype=float).copy())

    @property
    def o(self):
        return self.col("open")

    @property
    def h(self):
        return self.col("high")

    @property
    def l(self):
        return self.col("low")

    @property
    def c(self):
        return self.col("close")

    # Indikatoren
    def atr(self, n: int = 14):
        return self._get(("atr", n), lambda: ind.atr(self.h, self.l, self.c, n))

    def ema(self, n: int):
        return self._get(("ema", n), lambda: ind.ema(self.c, n))

    def ema_atr(self, n: int, atr_n: int = 14):
        return self._get(("ema_atr", n, atr_n), lambda: ind.ema(self.atr(atr_n), n))

    def rsi(self, n: int):
        return self._get(("rsi", n), lambda: ind.rsi(self.c, n))

    def max_prev_high(self, n: int):
        return self._get(("maxh", n), lambda: ind.rolling_max_prev(self.h, n))

    def min_prev_low(self, n: int):
        return self._get(("minl", n), lambda: ind.rolling_min_prev(self.l, n))

    def swings(self, k: int):
        return self._get(("swings", k), lambda: ind.swing_points(self.h, self.l, k))

    def structure(self, k: int):
        def calc():
            sh, sl, _, _ = self.swings(k)
            return ind.market_structure(self.c, sh, sl)
        return self._get(("structure", k), calc)

    def fvg(self, min_atr: float, atr_n: int = 14):
        return self._get(("fvg", min_atr, atr_n), lambda: ind.fair_value_gaps(self.h, self.l, self.atr(atr_n), min_atr))

    def session(self, windows):
        windows = tuple(tuple(w) for w in windows)
        return self._get(("session", windows),
                         lambda: ind.in_session(pd.DatetimeIndex(self.bars["time"]), list(windows)))

    def sweeps(self, k: int):
        """Liquidity Sweeps (erster Sweep je Niveau) – siehe LiquiditySweep."""
        def calc():
            from .strategies.library import _first_per_level
            sh, sl, _, _ = self.swings(k)
            h, l, c = self.h, self.l, self.c
            sl_prev = np.concatenate([[np.nan], sl[:-1]])
            sh_prev = np.concatenate([[np.nan], sh[:-1]])
            mid = (h + l) / 2
            with np.errstate(invalid="ignore"):
                up = (l < sl_prev) & (c > sl_prev) & (c >= mid)
                dn = (h > sh_prev) & (c < sh_prev) & (c <= mid)
            return _first_per_level(up, sl_prev), _first_per_level(dn, sh_prev)
        return self._get(("sweeps", k), calc)

    def breakouts(self, n: int):
        """Donchian-Ausbruch (erste Kerze des Ausbruchs) – siehe DonchianBreakout."""
        def calc():
            c = self.c
            with np.errstate(invalid="ignore"):
                up, dn = c > self.max_prev_high(n), c < self.min_prev_low(n)
            up = up & ~np.concatenate([[False], up[:-1]])
            dn = dn & ~np.concatenate([[False], dn[:-1]])
            return up, dn
        return self._get(("breakouts", n), calc)

    def weekly_vwap(self):
        """Wochen-VWAP mit Tick-Volumen (MT5 liefert für Gold/Bitcoin kein echtes Handelsvolumen)."""
        return self._get(("weekly_vwap",), lambda: ind.weekly_vwap(pd.DatetimeIndex(self.bars["time"]), self.h, self.l,
                                                                   self.c, self.col("tick_volume")))

    def opening_range(self, tz: str, open_min: int, range_min: int):
        return self._get(("orb", tz, open_min, range_min),
                         lambda: ind.opening_range(pd.DatetimeIndex(self.bars["time"]), self.h, self.l, tz, open_min,
                                                   range_min))

    def momentum(self, n: int, atr_n: int = 14):
        def calc():
            c, a = self.c, self.atr(atr_n)
            prev = np.concatenate([np.full(n, np.nan), c[:-n]])
            with np.errstate(invalid="ignore", divide="ignore"):
                return (c - prev) / (a * np.sqrt(n))
        return self._get(("momentum", n, atr_n), calc)


def get(bars: pd.DataFrame) -> Features:
    f = _ACTIVE.get(id(bars))
    return f if f is not None and f.bars is bars else Features(bars)


@contextmanager
def shared(bars: pd.DataFrame):
    f = Features(bars)
    _ACTIVE[id(bars)] = f
    try:
        yield f
    finally:
        _ACTIVE.pop(id(bars), None)
