"""Kontosimulation in Kontowährung (Stufe 2).

Verarbeitet die Trades aus engine.simulate chronologisch (Ausstiege vor Einstiegen zum selben
Zeitpunkt) und wendet die Risikoregeln an:

  * Positionsgröße: Risiko = risk_per_trade × aktuelles Konto (realisiert). Volumen so, dass Verlust bis
    zum Stop + Kommission höchstens dieses Risiko kostet, ABGERUNDET auf das Volumenraster.
    Liegt das Ergebnis unter der Mindestlotgröße, wird der Trade NICHT ausgeführt (Grund "min_lot") –
    sonst würde das erlaubte Risiko überschritten.
  * Tagesverlust: Ist der realisierte Verlust des Broker-Tages >= max_daily_loss × Tagesanfangskonto,
    werden an diesem Tag keine neuen Positionen eröffnet.
  * Drawdown: max_drawdown ist die Testgrenze. Standard: Überschreitung wird markiert und die
    Simulation läuft weiter (halt_on_max_dd=False), damit die Kennzahlen vergleichbar bleiben.
  * Höchstens max_positions offene Positionen; Margin aller offenen Positionen <= Kontostand.
  * Gewinn/Verlust in der Gewinnwährung, umgerechnet mit dem Kurs zum Ausstiegszeitpunkt;
    Kommission und Swap gemäß Kostenszenario.

Swap: je Übernachtung (Mitternacht Serverzeit). Nicht-24/7-Instrumente: Mo–Fr, am Tag
swap_rollover3days dreifach (deckt das Wochenende ab). 24/7-Instrumente: jede Nacht einfach.
Die aktuellen MT5-Swapwerte werden für die gesamte Historie angenommen (historische Werte unbekannt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..instruments import CurrencyConverter, InstrumentSpec, margin_margin_currency, round_volume_down
from .costs import CostScenario


@dataclass
class RiskRules:
    initial: float = 1000.0
    # Freigabe des Nutzers am 20.10.2026 (vorher 0,25 % / 1 % / 5 %)
    risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_drawdown: float = 0.20
    max_positions: int = 2
    halt_on_max_dd: bool = False


@dataclass
class AccountResult:
    trades: pd.DataFrame
    skipped: dict
    equity_end: float
    max_dd_pct: float
    dd_breach_time: str | None
    daily_lock_days: int
    min_lot_risk_pct: list = field(default_factory=list)  # Risiko bei Mindestlot für übersprungene Trades


def _mt5_weekday(day_number: np.ndarray) -> np.ndarray:
    """Wochentag nach MT5 (0 = Sonntag) für Tage seit 1970-01-01 (Donnerstag)."""
    return (day_number + 4) % 7


def swap_nights(entry_server: int, exit_server: int, always_open: bool, rollover3: int) -> float:
    d0, d1 = entry_server // 86400, exit_server // 86400
    if d1 <= d0:
        return 0.0
    days = np.arange(d0, d1)                 # Tage, deren Mitternacht überschritten wurde
    if always_open:
        return float(len(days))
    wd = _mt5_weekday(days)
    mult = np.where((wd >= 1) & (wd <= 5), 1.0, 0.0)
    mult[wd == rollover3] = 3.0
    return float(mult.sum())


def swap_amount(spec: InstrumentSpec, direction: int, volume: float, entry: float, exit_: float,
                nights: float) -> tuple[float, str]:
    """Swap (Vorzeichen: negativ = Kosten) und Währung."""
    rate = spec.swap_long if direction == 1 else spec.swap_short
    m = spec.swap_mode
    if nights == 0 or m == 0 or rate == 0:
        return 0.0, spec.currency_profit
    if m == 1:      # Points
        return rate * spec.point * spec.contract_size * volume * nights, spec.currency_profit
    if m == 2:      # Basiswährung
        return rate * volume * nights, spec.currency_base
    if m == 3:      # Marginwährung
        return rate * volume * nights, spec.currency_margin
    if m == 4:      # Kontowährung (Währung "DEPOSIT")
        return rate * volume * nights, "DEPOSIT"
    if m in (5, 6):  # Zins p. a. auf aktuellen bzw. Eröffnungskurs
        px = exit_ if m == 5 else entry
        return px * spec.contract_size * volume * rate / 100 / 360 * nights, spec.currency_profit
    return 0.0, spec.currency_profit  # Modi 7/8 (Reopen) nicht modelliert -> dokumentiert


def run_account(raw: pd.DataFrame, spec: InstrumentSpec, conv: CurrencyConverter, account_ccy: str,
                leverage: float, costs: CostScenario, rules: RiskRules, always_open: bool,
                rollover3: int = 3) -> AccountResult:
    skipped = {"min_lot": 0, "daily_loss": 0, "max_dd_halt": 0, "max_positions": 0, "margin": 0, "no_rate": 0}
    cols = ["entry_time", "exit_time", "direction", "entry", "exit", "volume", "risk_eur", "pnl_eur",
            "commission_eur", "swap_eur", "net_eur", "r_net", "equity_after", "hold_hours", "exit_reason",
            "spread_cost_eur"]
    if raw.empty:
        return AccountResult(pd.DataFrame(columns=cols), skipped, rules.initial, 0.0, None, 0)

    n = len(raw)
    # Zeit als int64-Nanosekunden: to_numpy() liefert bei Zeitzonen-Spalten Python-Objekte, die
    # np.lexsort nicht zuverlässig chronologisch sortiert (führte zu nie geschlossenen Positionen)
    ev_t = np.concatenate([pd.DatetimeIndex(raw["entry_time"]).as_unit("ns").asi8,
                           pd.DatetimeIndex(raw["exit_time"]).as_unit("ns").asi8])
    # Reihenfolge bei gleicher Zeit: 0 = Ausstiege früherer Trades, 1 = Einstiege, 2 = Ausstiege von
    # Trades, die in derselben Kerze ein- und aussteigen (sonst käme ihr Ausstieg vor dem Einstieg und die
    # Position bliebe für immer offen)
    same_bar = ev_t[n:] == ev_t[:n]
    ev_kind = np.concatenate([np.ones(n, int), np.where(same_bar, 2, 0)])
    ev_k = np.concatenate([np.arange(n), np.arange(n)])
    order = np.lexsort((ev_kind, ev_t))
    entry_srv = raw["entry_server"].to_numpy()
    exit_srv = raw["exit_server"].to_numpy()
    A_entry = raw["entry"].to_numpy(dtype=float)
    A_exit = raw["exit"].to_numpy(dtype=float)
    A_risk = raw["risk"].to_numpy(dtype=float)
    A_dir = raw["direction"].to_numpy(dtype=int)
    A_spread = raw["spread_entry"].to_numpy(dtype=float)
    A_reason = raw["exit_reason"].to_numpy()
    T_entry = pd.DatetimeIndex(raw["entry_time"])
    T_exit = pd.DatetimeIndex(raw["exit_time"])
    m_rate = conv.rate(spec.margin_result_currency, account_ccy, raw["entry_time"])

    # Umrechnungskurse vektorisiert vorab (Kurs zum Zeitpunkt; NaN, wenn keiner verfügbar)
    r_entry = conv.rate(spec.currency_profit, account_ccy, raw["entry_time"])
    r_exit = conv.rate(spec.currency_profit, account_ccy, raw["exit_time"])

    equity, peak, max_dd = rules.initial, rules.initial, 0.0
    breach_time = None
    day, day_start, day_pnl, locked, lock_days = None, equity, 0.0, False, 0
    open_pos: dict[int, dict] = {}
    out = []
    minlot_risk = []
    halted = False

    def roll_day(srv_time):
        nonlocal day, day_start, day_pnl, locked
        dd_ = int(srv_time // 86400)
        if dd_ != day:
            day, day_start, day_pnl, locked = dd_, equity, 0.0, False

    for idx in order:
        k = int(ev_k[idx])
        if ev_kind[idx] == 1:  # Einstieg
            roll_day(entry_srv[k])
            if halted:
                skipped["max_dd_halt"] += 1
                continue
            if locked:
                skipped["daily_loss"] += 1
                continue
            if len(open_pos) >= rules.max_positions:
                skipped["max_positions"] += 1
                continue
            rate = r_entry[k]
            if not np.isfinite(rate):
                skipped["no_rate"] += 1
                continue
            entry = A_entry[k]
            risk_eur = equity * rules.risk_per_trade
            comm_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * entry * spec.contract_size) * rate
            loss_per_lot = A_risk[k] * spec.contract_size * rate + comm_lot
            vol = round_volume_down(spec, risk_eur / loss_per_lot)
            if vol <= 0:
                skipped["min_lot"] += 1
                minlot_risk.append(100 * loss_per_lot * spec.volume_min / equity)
                continue
            margin = float(margin_margin_currency(spec, vol, entry, leverage)) * m_rate[k]
            used = sum(p["margin"] for p in open_pos.values())
            if not np.isfinite(margin) or used + margin > equity:
                skipped["margin"] += 1
                continue
            open_pos[k] = {"vol": vol, "risk_eur": vol * loss_per_lot, "margin": margin}
        else:  # Ausstieg
            p = open_pos.pop(k, None)
            if p is None:
                continue
            roll_day(exit_srv[k])
            d = int(A_dir[k])
            entry, exit_ = A_entry[k], A_exit[k]
            vol, rate = p["vol"], r_exit[k] if np.isfinite(r_exit[k]) else r_entry[k]
            pnl = d * (exit_ - entry) * spec.contract_size * vol * rate
            comm = (costs.commission_per_lot_rt * vol +
                    costs.commission_frac_rt * spec.contract_size * vol * (entry + exit_) / 2) * rate
            sw = 0.0
            if costs.swap:
                nights = swap_nights(int(entry_srv[k]), int(exit_srv[k]), always_open, rollover3)
                amt, ccy = swap_amount(spec, d, vol, entry, exit_, nights)
                if amt:
                    sw = amt if ccy == "DEPOSIT" else float(amt * conv.rate(ccy, account_ccy, [T_exit[k]])[0])
            net = pnl - comm + sw
            equity += net
            day_pnl += net
            if day_pnl <= -rules.max_daily_loss * day_start and not locked:
                locked = True
                lock_days += 1
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
            if dd > rules.max_drawdown and breach_time is None:
                breach_time = str(T_exit[k])
                if rules.halt_on_max_dd:
                    halted = True
            hold_h = (T_exit[k] - T_entry[k]).total_seconds() / 3600
            out.append((T_entry[k], T_exit[k], d, entry, exit_, vol, p["risk_eur"], pnl, comm, sw, net,
                        net / p["risk_eur"], equity, hold_h, A_reason[k], A_spread[k] * spec.contract_size * vol * rate))
    trades = pd.DataFrame(out, columns=cols)
    return AccountResult(trades, skipped, equity, max_dd, breach_time, lock_days, minlot_risk)
