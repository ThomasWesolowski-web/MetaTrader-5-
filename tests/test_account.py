import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest.account import RiskRules, run_account, swap_amount, swap_nights
from tradingbot.backtest.costs import CostScenario
from tradingbot.instruments import CurrencyConverter, InstrumentSpec

from synthetic_store import SPECS

GOLD = InstrumentSpec.from_dict(SPECS["XAUUSD"])
BTC = InstrumentSpec.from_dict(SPECS["BTCUSD"])
NOCOST = CostScenario("x", 1, 0, 0, 0, 0, swap=False)


def conv(eurusd=1.25):
    c = CurrencyConverter()
    c.add_live("EURUSD", "EUR", "USD", eurusd)
    c.add_live("BTCUSD", "BTC", "USD", 50000.0)
    return c


def raw(trades):
    rows = []
    for e_t, x_t, d, entry, exit_, stop in trades:
        e_t, x_t = pd.Timestamp(e_t, tz="UTC"), pd.Timestamp(x_t, tz="UTC")
        rows.append({"entry_time": e_t, "exit_time": x_t, "entry_server": int(e_t.timestamp()) + 3 * 3600,
                     "exit_server": int(x_t.timestamp()) + 3 * 3600, "direction": d, "entry": entry, "exit": exit_,
                     "stop": stop, "target": np.nan, "risk": abs(entry - stop), "spread_entry": 0.2,
                     "exit_reason": "x"})
    return pd.DataFrame(rows)


def acct(r, spec=GOLD, costs=NOCOST, rules=None, always_open=False):
    return run_account(r, spec, conv(), "EUR", 500, costs, rules or RiskRules(), always_open)


def test_position_size_and_pnl_in_eur():
    # 2,50 € Risiko, Stop 1,25 USD: 0,01 Lot verliert 1,25 USD = 1,00 € -> 0,02 Lot
    res = acct(raw([("2026-03-02 10:00", "2026-03-02 12:00", 1, 4000.0, 4005.0, 3998.75)]))
    t = res.trades.iloc[0]
    assert t.volume == 0.02
    assert t.pnl_eur == pytest.approx(5.0 * 100 * 0.02 / 1.25)   # 8 €
    assert res.equity_end == pytest.approx(1008.0)


def test_min_lot_blocks_trade_when_risk_too_high():
    # Stop 10 USD: schon 0,01 Lot riskiert 10 USD = 8 € > 2,50 € -> kein Trade
    res = acct(raw([("2026-03-02 10:00", "2026-03-02 12:00", 1, 4000.0, 3990.0, 3990.0)]))
    assert res.trades.empty and res.skipped["min_lot"] == 1
    assert res.min_lot_risk_pct[0] == pytest.approx(0.8)


def test_daily_loss_limit_blocks_further_entries_same_day():
    day = [("2026-03-02 0%d:00" % h, "2026-03-02 0%d:30" % h, 1, 4000.0, 3998.75, 3998.75) for h in range(1, 8)]
    nxt = [("2026-03-03 10:00", "2026-03-03 11:00", 1, 4000.0, 4001.0, 3998.75)]
    res = acct(raw(day + nxt))
    # 0,02 Lot (abgerundet) verlieren je 2,00 €: nach 5 Verlusten = 10 € = 1 % -> Rest des Tages gesperrt,
    # am nächsten Tag wieder Handel
    assert res.skipped["daily_loss"] == 2 and res.daily_lock_days == 1
    assert len(res.trades) == 6 and res.trades["entry_time"].iloc[-1].day == 3


def test_drawdown_breach_is_flagged():
    losses = [("2026-03-%02d 10:00" % d, "2026-03-%02d 11:00" % d, 1, 4000.0, 3998.75, 3998.75) for d in range(2, 32)]  # 30 Verluste à 2 € = 6 %
    res = acct(raw(losses))
    assert res.max_dd_pct > 0.05 and res.dd_breach_time is not None
    res2 = acct(raw(losses), rules=RiskRules(halt_on_max_dd=True))
    assert res2.skipped["max_dd_halt"] > 0


def test_commission_and_swap():
    costs = CostScenario("k", 1, 0, 0, 7.0, 0.0, swap=True)
    # Mo 10:00 -> Do 10:00 Serverzeit: Nächte Mo, Di, Mi(×3) = 5
    r = raw([("2026-03-02 07:00", "2026-03-05 07:00", 1, 4000.0, 4000.0, 3998.75)])
    t = acct(r, costs=costs).trades.iloc[0]
    vol = t.volume
    assert t.commission_eur == pytest.approx(7.0 * vol / 1.25)
    assert t.swap_eur == pytest.approx(-77.44 * 0.01 * 100 * vol * 5 / 1.25)


def test_swap_nights_rules():
    mon = int(pd.Timestamp("2026-03-02 12:00", tz="UTC").timestamp())
    assert swap_nights(mon, mon + 7 * 86400, False, 3) == 7.0   # Woche: Mo,Di,Mi×3,Do,Fr
    assert swap_nights(mon, mon + 7 * 86400, True, 3) == 7.0    # 24/7: jede Nacht einfach
    assert swap_nights(mon, mon + 3600, False, 3) == 0.0


def test_btc_margin_in_btc_converted_via_cross_rate():
    # FOREX_NO_LEVERAGE: 0,01 Lot × 1 BTC × 0,002 = 0,00002 BTC -> × 50 000 USD / 1,25 = 0,80 €
    amt, ccy = swap_amount(BTC, 1, 0.01, 50000, 50000, 1)
    assert ccy == "USD" and amt == pytest.approx(50000 * 0.01 * -20 / 100 / 360)
    res = acct(raw([("2026-03-02 10:00", "2026-03-02 12:00", 1, 50000.0, 50100.0, 49900.0)]), spec=BTC,
               always_open=True)
    assert len(res.trades) == 1 and res.trades.iloc[0].volume == 0.03   # 2,50 € / (100 USD×0,01/1,25=0,80 €)


def test_margin_check_blocks_oversized_positions():
    # Hebel 1:1 simuliert über Marginsatz 500 -> 0,02 Lot Gold à 4000 = 8000 USD Margin > Konto
    spec = InstrumentSpec.from_dict({**SPECS["XAUUSD"], "margin_rate": 500.0})
    res = acct(raw([("2026-03-02 10:00", "2026-03-02 12:00", 1, 4000.0, 4005.0, 3998.75)]), spec=spec)
    assert res.trades.empty and res.skipped["margin"] == 1


def test_trade_stopped_in_entry_bar_is_closed():
    """Stop in der Einstiegskerze (Einstiegszeit = Ausstiegszeit): Position muss geschlossen werden und
    darf keine weiteren Trades blockieren (Regressionstest)."""
    rows = [("2026-03-02 10:00", "2026-03-02 10:00", 1, 4000.0, 3999.0, 3999.0),
            ("2026-03-02 10:05", "2026-03-02 10:05", -1, 4000.0, 4001.0, 4001.0)]
    rows += [("2026-03-02 11:%02d" % m, "2026-03-02 11:%02d" % (m + 1), 1, 4000.0, 4000.5, 3999.0)
             for m in range(0, 20, 2)]
    res = acct(raw(rows))
    assert len(res.trades) == 12 and res.skipped["max_positions"] == 0


def test_back_to_back_trades_are_processed_in_time_order():
    """Ausstieg und nächster Einstieg zur selben Zeit, viele Trades: alle müssen ausgeführt werden
    (Regressionstest: Sortierung über Objekt-Zeitstempel ließ Positionen offen)."""
    t0 = pd.Timestamp("2026-03-02 00:00", tz="UTC")
    rows = []
    for i in range(400):
        a, b = t0 + pd.Timedelta(minutes=5 * i), t0 + pd.Timedelta(minutes=5 * (i + 1))
        rows.append((str(a)[:16], str(b)[:16], 1 if i % 2 else -1, 4000.0, 4000.1 if i % 2 else 3999.9,
                     3999.0 if i % 2 else 4001.0))
    r = raw(rows)
    res = acct(r)
    assert len(res.trades) + res.skipped["daily_loss"] == 400
    assert res.skipped["max_positions"] == 0
    assert res.trades["entry_time"].is_monotonic_increasing


@pytest.mark.parametrize("spec", [GOLD, BTC], ids=["XAUUSD", "BTCUSD"])
def test_account_uses_same_formulas_as_mt5_validated_calculator(spec):
    """Gewinn und Margin der Kontosimulation = RiskCalculator (in Phase 2 gegen order_calc_profit/
    order_calc_margin geprüft; zusätzlich: backtest_ausfuehren.py --mt5-abgleich)."""
    from tradingbot.instruments import RiskCalculator
    px = 4000.0 if spec is GOLD else 50000.0
    r = raw([("2026-03-02 10:00", "2026-03-02 12:00", -1, px, px * 0.999, px * 1.0001)])
    res = acct(r, spec=spec, always_open=spec is BTC)
    t = res.trades.iloc[0]
    rc = RiskCalculator(spec, conv(), "EUR", 500)
    assert t.pnl_eur == pytest.approx(float(rc.pnl(-1, t.volume, px, px * 0.999)[0]))
