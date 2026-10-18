"""Kostenszenarien.

Historisch bekannt sind nur die Spreads je Kerze (MT5-Feld `spread`, in Points; MT5 speichert je
Kerze typischerweise den geringsten Spread – daher eher optimistisch). Kommission und Slippage sind
für das Konto NICHT historisch bekannt und werden als dokumentierte Annahmen geführt:

basis        historischer Kerzen-Spread, geringe Slippage, keine Kommission
             (entspricht einem reinen Spread-Konto ohne Kommission)
konservativ  Spread × 1,5, dreifache Slippage, Kommission wie bei einem Raw-/ECN-Konto
             – Grundlage für Auswahl und Rangliste

Swap: aktuelle MT5-Werte (specs.json) für die gesamte Historie – historische Swaps sind unbekannt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class CostScenario:
    name: str
    spread_factor: float           # Multiplikator auf den historischen Kerzen-Spread
    min_spread_points: float       # Untergrenze je Kerze (Spread 0 kommt in Daten vor)
    slippage_frac: float           # Slippage je Markt-/Stopp-Ausführung als Anteil vom Kurs
    commission_per_lot_rt: float   # Kommission je Lot Hin+Rück in Gewinnwährung
    commission_frac_rt: float      # Kommission als Anteil vom Nominalwert Hin+Rück
    swap: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def spread_price(self, spread_points: np.ndarray, point: float) -> np.ndarray:
        sp = np.maximum(np.asarray(spread_points, dtype=float), self.min_spread_points)
        return sp * self.spread_factor * point


# Annahmen je Symbol. Gold-Slippage 0,001 % ≈ 0,04 USD bei 4000; Bitcoin 0,005 % ≈ 4 USD bei 80 000.
DEFAULTS = {
    "XAUUSD": {
        "basis": CostScenario("basis", 1.0, 1, 0.00001, 0.0, 0.0),
        "konservativ": CostScenario("konservativ", 1.5, 1, 0.00003, 7.0, 0.0),
    },
    "BTCUSD": {
        "basis": CostScenario("basis", 1.0, 1, 0.00005, 0.0, 0.0),
        "konservativ": CostScenario("konservativ", 1.5, 1, 0.00015, 0.0, 0.0004),
    },
    "*": {
        "basis": CostScenario("basis", 1.0, 1, 0.00002, 0.0, 0.0),
        "konservativ": CostScenario("konservativ", 1.5, 1, 0.00006, 0.0, 0.0002),
    },
}


def scenarios_for(symbol: str, overrides: dict | None = None) -> dict[str, CostScenario]:
    base = {k: CostScenario(**v.to_dict()) for k, v in DEFAULTS.get(symbol, DEFAULTS["*"]).items()}
    for name, vals in ((overrides or {}).get(symbol, {}) or {}).items():
        if name in base:
            for k, v in vals.items():
                setattr(base[name], k, type(getattr(base[name], k))(v))
    return base
