"""Kontraktspezifikationen und Mindestlot gegen das geplante Risiko prüfen (Phase 4, Punkt 3 – nur Dokumentation).

Frage: Bis zu welchem Stop-Abstand bleibt ein Trade mit der kleinsten handelbaren Lotgröße innerhalb des geplanten
Risikos (0,25 % von 1 000 € = 2,50 €)? Verlust bis zum Stop je Lot = Abstand × Kontraktgröße × Umrechnungskurs
+ Kommission (Annahme aus dem Kostenszenario). Der Abstand wird ab dem tatsächlichen Einstiegskurs gemessen, der Spread
ist also darin enthalten. Es werden weder Kapital noch Risikogrenzen verändert.
"""

from __future__ import annotations

import math

import numpy as np

from .backtest.account import RiskRules
from .backtest.costs import CostScenario
from .instruments import InstrumentSpec

COMPARE_FIELDS = ("contract_size", "volume_min", "volume_step", "volume_max", "tick_size", "digits", "point",
                  "calc_mode", "currency_profit", "currency_margin", "swap_long", "swap_short", "swap_mode",
                  "stops_level")


def spec_differences(live: InstrumentSpec, stored: dict | None) -> list[dict]:
    """Abweichungen zwischen live aus MT5 gelesenen und gespeicherten Spezifikationen (data/specs.json)."""
    if not stored:
        return [{"field": "alle", "live": "–", "gespeichert": "nicht vorhanden"}]
    out = []
    for f in COMPARE_FIELDS:
        a, b = getattr(live, f), stored.get(f)
        same = (math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a == b)
        if not same:
            out.append({"field": f, "live": a, "gespeichert": b})
    return out


def max_stop_at_min_lot(spec: InstrumentSpec, profit_to_eur: float, price: float, costs: CostScenario,
                        risk_eur: float) -> float:
    """Größter Stop-Abstand (Preiseinheiten), bei dem die Mindestlotgröße höchstens risk_eur verliert."""
    if not (profit_to_eur > 0 and price > 0):
        return float("nan")
    vol = spec.volume_min
    comm_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * price * spec.contract_size) * profit_to_eur
    per_unit_lot = spec.contract_size * profit_to_eur
    return max(0.0, (risk_eur / vol - comm_lot) / per_unit_lot)


def check_symbol(spec: InstrumentSpec, profit_to_eur: float, price: float, costs: CostScenario,
                 atr_by_tf: dict[str, float], rules: RiskRules | None = None, stored: dict | None = None) -> dict:
    rules = rules or RiskRules()
    risk_eur = rules.initial * rules.risk_per_trade
    ms = max_stop_at_min_lot(spec, profit_to_eur, price, costs, risk_eur)
    min_stop = spec.stops_level * spec.point          # Mindestabstand des Brokers für Stops
    per_unit_min_lot = spec.volume_min * spec.contract_size * profit_to_eur
    comm_min_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * price * spec.contract_size) \
        * profit_to_eur * spec.volume_min
    rows = {}
    for tf, atr in atr_by_tf.items():
        if not (atr and np.isfinite(atr)):
            rows[tf] = {"atr14": None}
            continue
        rows[tf] = {
            "atr14": round(float(atr), 4),
            "max_stop_in_atr": round(ms / atr, 2) if np.isfinite(ms) else None,
            # Risiko bei Mindestlot für übliche Stop-Abstände (nur Information, keine Änderung der Grenze)
            "risk_eur_min_lot_1_5atr": round(1.5 * atr * per_unit_min_lot + comm_min_lot, 2),
            "risk_eur_min_lot_3atr": round(3.0 * atr * per_unit_min_lot + comm_min_lot, 2),
            "within_limit_1_5atr": bool(1.5 * atr <= ms),
            "within_limit_3atr": bool(3.0 * atr <= ms),
        }
    return {
        "symbol": spec.symbol,
        "spec": {"contract_size": spec.contract_size, "volume_min": spec.volume_min, "volume_step": spec.volume_step,
                 "volume_max": spec.volume_max, "tick_size": spec.tick_size, "point": spec.point,
                 "digits": spec.digits, "calc_mode": spec.calc_mode, "currency_profit": spec.currency_profit,
                 "stops_level_points": spec.stops_level, "swap_long": spec.swap_long, "swap_short": spec.swap_short,
                 "swap_mode": spec.swap_mode},
        "price": round(float(price), spec.digits),
        "profit_ccy_to_eur": round(float(profit_to_eur), 6),
        "reference_capital_eur": rules.initial,
        "risk_per_trade_pct": round(100 * rules.risk_per_trade, 4),
        "risk_eur": round(risk_eur, 2),
        "commission_assumption": {"per_lot_rt": costs.commission_per_lot_rt, "frac_rt": costs.commission_frac_rt,
                                  "scenario": costs.name, "eur_min_lot": round(comm_min_lot, 4)},
        "value_per_price_unit_min_lot_eur": round(per_unit_min_lot, 6),
        "max_stop_distance": None if not np.isfinite(ms) else round(ms, spec.digits),
        "max_stop_pct_of_price": None if not np.isfinite(ms) else round(100 * ms / price, 4),
        "broker_min_stop_distance": round(min_stop, spec.digits),
        "feasible": bool(np.isfinite(ms) and ms > min_stop),
        "by_timeframe": rows,
        "spec_differences": spec_differences(spec, stored) if stored is not None else None,
    }
