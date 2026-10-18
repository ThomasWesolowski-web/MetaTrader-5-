"""Kennzahlen eines Backtests (auf Basis der ausgeführten Trades in Kontowährung)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .account import AccountResult


def _safe(x, nd=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


def compute(res: AccountResult, initial: float, start: pd.Timestamp | None = None,
            end: pd.Timestamp | None = None, raw_skipped: dict | None = None) -> dict:
    tr = res.trades
    n = len(tr)
    skipped = {**(raw_skipped or {}), **res.skipped}
    attempted = n + res.skipped.get("min_lot", 0)
    base = {
        "trades": n,
        "net_profit_eur": _safe(res.equity_end - initial, 2),
        "return_pct": _safe(100 * (res.equity_end / initial - 1), 2),
        "max_drawdown_pct": _safe(100 * res.max_dd_pct, 2),
        "dd_limit_breached": res.dd_breach_time is not None,
        "dd_breach_time": res.dd_breach_time,
        "daily_loss_lock_days": res.daily_lock_days,
        "skipped": skipped,
        "min_lot_skip_pct": _safe(100 * res.skipped.get("min_lot", 0) / attempted, 1) if attempted else None,
        "min_lot_risk_pct_median": _safe(float(np.median(res.min_lot_risk_pct)), 2) if res.min_lot_risk_pct else None,
    }
    if n == 0:
        return {**base, "profit_factor": None, "win_rate_pct": None, "expectancy_eur": None, "expectancy_r": None,
                "t_stat": None, "sharpe": None, "avg_hold_hours": None, "yearly": {}, "positive_years_pct": None,
                "costs_eur": {}}
    net = tr["net_eur"].to_numpy()
    r = tr["r_net"].to_numpy()
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    pf = gp / gl if gl > 0 else (math.inf if gp > 0 else None)
    t_stat = r.mean() / r.std(ddof=1) * math.sqrt(n) if n > 1 and r.std(ddof=1) > 0 else None

    # Tägliche Kontorendite über alle Kalendertage des Zeitraums (realisiert) -> Sharpe (×√365)
    eq = pd.Series(tr["equity_after"].to_numpy(), index=pd.DatetimeIndex(tr["exit_time"])).groupby(
        lambda x: x.normalize()).last()
    s = start.normalize() if start is not None else eq.index.min()
    e = end.normalize() if end is not None else eq.index.max()
    days = pd.date_range(s, e, freq="D", tz="UTC")
    daily = eq.reindex(days.union(eq.index)).ffill().fillna(initial).reindex(days)
    rets = daily.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * math.sqrt(365) if len(rets) > 2 and rets.std() > 0 else None

    years = pd.DatetimeIndex(tr["exit_time"]).year
    yearly = {}
    eq_start = initial
    for y in sorted(set(years)):
        pnl_y = net[years == y].sum()
        yearly[str(y)] = _safe(100 * pnl_y / eq_start, 2)
        eq_start += pnl_y
    pos_years = sum(1 for v in yearly.values() if v and v > 0)
    return {
        **base,
        "profit_factor": _safe(pf, 3) if pf is not math.inf else 99.0,
        "win_rate_pct": _safe(100 * (net > 0).mean(), 1),
        "expectancy_eur": _safe(net.mean(), 3),
        "expectancy_r": _safe(r.mean(), 4),
        "t_stat": _safe(t_stat, 2),
        "sharpe": _safe(sharpe, 2),
        "avg_hold_hours": _safe(tr["hold_hours"].mean(), 2),
        "yearly": yearly,
        "positive_years_pct": _safe(100 * pos_years / len(yearly), 1) if yearly else None,
        "long_share_pct": _safe(100 * (tr["direction"] == 1).mean(), 1),
        "exit_reasons": tr["exit_reason"].value_counts().to_dict(),
        "costs_eur": {"commission": _safe(tr["commission_eur"].sum(), 2), "swap": _safe(tr["swap_eur"].sum(), 2),
                      "spread_est": _safe(tr["spread_cost_eur"].sum(), 2)},
        "avg_volume": _safe(tr["volume"].mean(), 3),
    }
