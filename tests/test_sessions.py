import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradingbot.sessions import CmeMetals, _easter, get_calendar, open_seconds_between, us_market_holidays


@pytest.mark.parametrize("year,date", [(2024, dt.date(2024, 3, 31)), (2025, dt.date(2025, 4, 20)),
                                       (2026, dt.date(2026, 4, 5)), (2027, dt.date(2027, 3, 28))])
def test_easter(year, date):
    assert _easter(year) == date


def test_us_holidays_2026():
    h = us_market_holidays(2026)
    assert h[dt.date(2026, 4, 3)] == "Karfreitag"
    assert h[dt.date(2026, 1, 19)] == "Martin Luther King Day"
    assert h[dt.date(2026, 11, 26)] == "Thanksgiving"
    assert h[dt.date(2026, 7, 3)] == "Independence Day"   # 4. Juli ist Samstag -> Freitag
    assert h[dt.date(2026, 6, 19)] == "Juneteenth"
    assert dt.date(2026, 12, 25) in h


def test_cme_sessions_follow_new_york_time():
    s, e = CmeMetals().open_intervals(pd.Timestamp("2026-07-06", tz="UTC"), pd.Timestamp("2026-07-07", tz="UTC"))
    starts = pd.to_datetime(s, utc=True)
    ends = pd.to_datetime(e, utc=True)
    assert pd.Timestamp("2026-07-05 22:00", tz="UTC") in starts   # So 18:00 EDT
    assert pd.Timestamp("2026-07-06 21:00", tz="UTC") in ends     # Mo 17:00 EDT
    s, _ = CmeMetals().open_intervals(pd.Timestamp("2026-01-12", tz="UTC"), pd.Timestamp("2026-01-13", tz="UTC"))
    assert pd.Timestamp("2026-01-11 23:00", tz="UTC") in pd.to_datetime(s, utc=True)  # So 18:00 EST


def test_no_session_on_saturday():
    cal = CmeMetals()
    a = pd.Timestamp("2026-07-11 00:00", tz="UTC")   # Samstag
    s, e = cal.open_intervals(a, a + pd.Timedelta(days=1))
    assert open_seconds_between(s, e, np.array([a.value]), np.array([(a + pd.Timedelta(hours=20)).value]))[0] == 0


def test_open_seconds_between_bruteforce():
    rng = np.random.default_rng(1)
    starts = np.cumsum(rng.integers(10, 100, 50)) * 10**9
    ends = starts + rng.integers(1, 9, 50) * 10**9
    a = rng.integers(0, starts[-1], 200)
    b = a + rng.integers(0, 500 * 10**9, 200)
    got = open_seconds_between(starts, ends, a, b)
    for i in range(len(a)):
        exp = sum(max(0, min(e, b[i]) - max(s, a[i])) for s, e in zip(starts, ends)) / 1e9
        assert got[i] == pytest.approx(exp)


def test_get_calendar():
    assert get_calendar("24x7").always_open
    with pytest.raises(ValueError):
        get_calendar("nyse")
