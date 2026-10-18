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
