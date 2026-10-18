import pandas as pd

from tradingbot.backtest import walkforward as wf


def test_periods_are_chronological_and_disjoint():
    p = wf.split_periods("2018-01-01", "2026-10-18")
    assert p.start < p.train_end < p.val_end < p.end
    a, b = p.window("train")
    c, d = p.window("validation")
    e, f = p.window("test")
    assert b == c and d == e


def test_wf_windows_stay_inside_training():
    p = wf.split_periods("2007-06-22", "2026-10-16")
    ws = wf.wf_windows(p.start, p.train_end)
    assert len(ws) >= 10
    for is_a, is_b, oos_a, oos_b in ws:
        assert is_a < is_b == oos_a < oos_b <= p.train_end


def test_selection_only_sees_trades_closed_inside_window():
    t = pd.to_datetime(["2020-01-10", "2020-06-25", "2020-07-05"], utc=True)
    raw = pd.DataFrame({"entry_time": t, "exit_time": t + pd.Timedelta(days=10)})
    a, b = pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2020-07-01", tz="UTC")
    assert len(wf.slice_trades(raw, a, b, closed_within=True)) == 1      # Trade vom 25.06. endet nach b
    assert len(wf.slice_trades(raw, a, b)) == 2


def test_objective_requires_minimum_trades():
    assert wf.objective({"trades": 5, "t_stat": 5.0}) == float("-inf")
    assert wf.objective({"trades": 100, "t_stat": 2.0, "dd_limit_breached": True}) == 1.0
