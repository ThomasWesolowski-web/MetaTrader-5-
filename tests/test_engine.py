import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest.engine import simulate
from tradingbot.strategies.base import Signals


def bars_from(rows, start="2026-03-02 10:00", freq="1h", gap_after=None):
    t = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    if gap_after is not None:  # 48 Stunden Lücke nach Kerze gap_after
        t = t.where(np.arange(len(rows)) <= gap_after, t + pd.Timedelta(hours=48))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "time", t)
    df["time_server"] = t.as_unit("ns").asi8 // 10**9
    df["spread"] = 0
    return df


def sig(idx, d, stop, target=np.nan, **kw):
    return Signals(np.array([idx]), np.array([d]), np.array([stop], float), np.array([target], float), **kw)


def run(df, s, spread=0.0, slip=0.0, maxpos=2):
    return simulate(df, s, 3600, np.full(len(df), spread), slip, maxpos)


FLAT = [(100, 101, 99, 100)] * 3


def test_long_target_and_entry_at_next_open_plus_spread():
    df = bars_from(FLAT + [(100, 100.5, 99.5, 100), (100, 103, 99.8, 102.5), (102, 102, 101, 101)])
    tr, _ = run(df, sig(2, 1, 98.0, 102.0), spread=0.2)
    t = tr.iloc[0]
    assert t.entry_i == 3 and t.entry == pytest.approx(100.2)     # Eröffnung + Spread (Ask)
    assert t.exit_reason == "target" and t.exit == 102.0 and t.exit_i == 4


def test_stop_wins_if_stop_and_target_in_same_bar():
    df = bars_from(FLAT + [(100, 100.5, 99.5, 100), (100, 103, 97, 101)])
    tr, _ = run(df, sig(2, 1, 98.0, 102.0))
    assert tr.iloc[0].exit_reason == "stop" and tr.iloc[0].exit == 98.0


def test_gap_through_stop_fills_at_open_not_at_stop():
    df = bars_from(FLAT + [(100, 100.5, 99.5, 100), (95, 96, 94, 95)])
    tr, _ = run(df, sig(2, 1, 98.0, 110.0), slip=0.0)
    assert tr.iloc[0].exit_reason == "stop_gap" and tr.iloc[0].exit == 95.0


def test_short_uses_ask_for_stop():
    df = bars_from(FLAT + [(100, 100.5, 99.5, 100), (100, 101.6, 99.9, 100.5)])
    tr, _ = run(df, sig(2, -1, 102.0, 95.0), spread=0.5)        # Ask-Hoch = 101,6 + 0,5 = 102,1
    t = tr.iloc[0]
    assert t.entry == 100.0 and t.exit_reason == "stop" and t.exit == 102.0


def test_no_entry_across_data_gap():
    df = bars_from(FLAT + [(100, 101, 99, 100)] * 3, gap_after=2)   # Lücke nach Kerze 2
    tr, skipped = run(df, sig(2, 1, 98.0, 102.0))
    assert tr.empty and skipped["gap"] == 1


def test_exit_signal_and_time_stop():
    rows = FLAT + [(100, 100.5, 99.5, 100)] * 6
    df = bars_from(rows)
    ex = np.zeros(len(rows), bool)
    ex[4] = True
    tr, _ = run(df, sig(2, 1, 90.0, exit_long=ex))
    assert tr.iloc[0].exit_reason == "signal" and tr.iloc[0].exit_i == 5   # Ausführung zur nächsten Eröffnung
    tr, _ = run(df, sig(2, 1, 90.0, max_bars=2))
    assert tr.iloc[0].exit_reason == "time" and tr.iloc[0].exit_i == 5


def test_max_positions():
    rows = FLAT + [(100, 100.5, 99.5, 100)] * 10
    df = bars_from(rows)
    s = Signals(np.array([2, 3, 4, 5]), np.ones(4, int), np.full(4, 90.0), np.full(4, np.nan))
    tr, skipped = run(df, s, maxpos=2)
    assert len(tr) == 2 and skipped["max_positions"] == 2


def test_invalid_when_open_already_beyond_stop():
    df = bars_from(FLAT + [(97, 98, 96, 97), (97, 98, 96, 97)])
    tr, skipped = run(df, sig(2, 1, 98.0, 105.0))
    assert tr.empty and skipped["invalid_stop"] == 1
