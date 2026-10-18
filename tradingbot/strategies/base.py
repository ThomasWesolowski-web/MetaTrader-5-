"""Schnittstelle für austauschbare Handelsstrategien (Grundlage für Phase 3).

Jede Strategie ist eine eigene Klasse, die sich über @register anmeldet. Sie bekommt
nur Kerzendaten und liefert eine Zielposition je Kerze – sie weiß nichts über MT5,
Orders oder Speicherorte. Dadurch lassen sich Strategien unabhängig voneinander
testen, vergleichen und später im Demokonto ausführen.

Konvention gegen Look-ahead-Bias:
    Das Signal einer Kerze darf nur Daten bis einschließlich ihres Schlusskurses nutzen.
    Ausgeführt wird frühestens zum Eröffnungskurs der FOLGENDEN Kerze (Aufgabe des
    Backtesters in Phase 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

_REGISTRY: dict[str, type["Strategy"]] = {}


class MarketData:
    """Lesezugriff auf die lokale Datenbank mit Zwischenspeicher."""

    def __init__(self, store):
        self.store = store
        self._cache: dict = {}

    def bars(self, symbol: str, tf: str, start=None, end=None) -> pd.DataFrame:
        key = (symbol, tf)
        if key not in self._cache:
            df = self.store.load(symbol, tf)
            self._cache[key] = df.set_index("time")
        df = self._cache[key]
        if start is not None:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end is not None:
            df = df[df.index < pd.Timestamp(end, tz="UTC")]
        return df


class Strategy(ABC):
    name: str = "basis"
    symbols: tuple[str, ...] = ("XAUUSD", "BTCUSD")
    timeframe: str = "H1"
    default_params: dict = {}

    def __init__(self, **params):
        unknown = set(params) - set(self.default_params)
        if unknown:
            raise ValueError(f"{self.name}: unbekannte Parameter {sorted(unknown)}")
        self.params = {**self.default_params, **params}

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        """Zielposition je Kerze: +1 long, -1 short, 0 flach (Index = bars.index)."""

    def warmup_bars(self) -> int:
        """Kerzen, die die Strategie vor dem ersten gültigen Signal benötigt."""
        return 0

    def validate_signals(self, bars: pd.DataFrame, signals: pd.Series) -> pd.Series:
        if not signals.index.equals(bars.index):
            raise ValueError(f"{self.name}: Signalindex passt nicht zu den Kerzen")
        if not signals.dropna().isin([-1, 0, 1]).all():
            raise ValueError(f"{self.name}: Signale müssen -1, 0 oder 1 sein")
        return signals.fillna(0).astype(int)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params})"


@dataclass
class Signals:
    """Einstiegssignale einer Strategie für den Backtest.

    idx        Kerze, an deren Schlusskurs das Signal feststeht (Ausführung frühestens bei idx+1)
    direction  +1 long, -1 short
    stop       Stop-Kurs (Pflicht – ohne Stop kein Positionsrisiko berechenbar)
    target     Kursziel oder NaN
    max_bars   Zeitstopp in Kerzen (0 = keiner)
    exit_long / exit_short  optionale Ausstiegssignale je Kerze (Ausführung bei der nächsten Eröffnung)
    """
    idx: np.ndarray
    direction: np.ndarray
    stop: np.ndarray
    target: np.ndarray
    max_bars: int = 0
    exit_long: np.ndarray | None = None
    exit_short: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.idx)

    @classmethod
    def empty(cls) -> "Signals":
        e = np.array([], dtype=float)
        return cls(np.array([], dtype=int), np.array([], dtype=int), e, e)

    @classmethod
    def build(cls, idx, direction, stop, target, close, max_bars=0, exit_long=None, exit_short=None) -> "Signals":
        """Signale bereinigen: gültiger Stop auf der richtigen Seite des Schlusskurses, sortiert,
        höchstens ein Signal je Kerze und Richtung."""
        idx = np.asarray(idx, dtype=int)
        direction = np.asarray(direction, dtype=int)
        stop = np.asarray(stop, dtype=float)
        target = np.asarray(target, dtype=float)
        if len(idx) == 0:
            out = cls.empty()
            out.max_bars, out.exit_long, out.exit_short = max_bars, exit_long, exit_short
            return out
        c = close[idx]
        ok = np.isfinite(stop) & np.isfinite(c) & (direction * (c - stop) > 0)
        ok &= ~np.isfinite(target) | (direction * (target - c) > 0)
        idx, direction, stop, target = idx[ok], direction[ok], stop[ok], target[ok]
        order = np.lexsort((direction, idx))
        idx, direction, stop, target = idx[order], direction[order], stop[order], target[order]
        keep = np.ones(len(idx), dtype=bool)
        keep[1:] = (idx[1:] != idx[:-1]) | (direction[1:] != direction[:-1])
        return cls(idx[keep], direction[keep], stop[keep], target[keep], max_bars, exit_long, exit_short)


class TradeStrategy(Strategy):
    """Strategie für den Backtest: liefert Einstiegssignale mit Stop/Ziel (siehe Signals).

    param_grid: kleine Liste von Parametersätzen, aus der der Walk-Forward-Test NUR anhand der
    Trainingsdaten auswählt. Bewusst klein gehalten, um Überoptimierung zu begrenzen."""

    param_grid: list[dict] = []
    description: str = ""
    rules: str = ""

    @abstractmethod
    def entries(self, bars: pd.DataFrame) -> Signals:
        """bars: Spalten open, high, low, close, spread, time (UTC). Nur Daten bis Kerze i verwenden."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        raise NotImplementedError("Backtest-Strategien liefern Einstiegssignale über entries()")


def register(cls: type[Strategy]) -> type[Strategy]:
    if cls.name in _REGISTRY:
        raise ValueError(f"Strategie '{cls.name}' ist bereits registriert")
    _REGISTRY[cls.name] = cls
    return cls


def available() -> list[str]:
    return sorted(_REGISTRY)


def create(name: str, **params) -> Strategy:
    try:
        return _REGISTRY[name](**params)
    except KeyError:
        raise ValueError(f"Unbekannte Strategie '{name}'. Verfügbar: {available()}") from None
