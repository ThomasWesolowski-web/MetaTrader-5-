"""Kostenszenarien der Zeitmaschine: drei Stufen, jeweils auf den echten Kerzen-Spread des Brokers.

basis        historischer Spread je Kerze (MT5 speichert meist den kleinsten Spread der Kerze, also eher günstig),
             wenig Slippage, keine Kommission – ein reines Spread-Konto an ruhigen Tagen
konservativ  Spread x 1,5, dreifache Slippage, Kommission wie ein Raw-/ECN-Konto (aus tradingbot/backtest/costs.py)
hart         Spread x 2,5, zehnfache Slippage, Kommission – Stresstest: hektische Phasen, Nachrichten, schlechte Fills

Swap (Übernachtung) mit den aktuellen MT5-Werten des Symbols, in allen Szenarien.
Eine Strategie, die nur in "basis" verdient, verdient in der Praxis meist nichts.
"""

from __future__ import annotations

from ..backtest.costs import CostScenario, scenarios_for

ORDER = ("basis", "konservativ", "hart")


def scenarios(symbol: str) -> dict[str, CostScenario]:
    s = scenarios_for(symbol)
    b, k = s["basis"], s["konservativ"]
    s["hart"] = CostScenario("hart", 2.5, max(k.min_spread_points, 1), b.slippage_frac * 10,
                             k.commission_per_lot_rt, k.commission_frac_rt)
    return {n: s[n] for n in ORDER}
