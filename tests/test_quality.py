import numpy as np
import pandas as pd
import pytest

from fake_market import BTC_SPIKE_UTC, GOLD_GAP_NY
from tradingbot.config import QualityConfig
from tradingbot.quality import check_bars, check_integrity, consistency_with_m1, detect_anomalies
from tradingbot.server_time import parse_scheme
from tradingbot.sessions import get_calendar
from tradingbot.storage import rates_to_frame

Q = QualityConfig()


def frame(fake, sym, tf):
    return rates_to_frame(fake.bars[sym][tf][fake.bars[sym][tf]["time"] <= fake.now_server()],
                          parse_scheme(fake.scheme))


@pytest.fixture(scope="module")
def gold_m5(fake_ny):
    return check_bars(frame(fake_ny, "XAUUSD", "M5"), "XAUUSD", "M5", get_calendar("cme_metals"), Q)


def test_gold_only_injected_gap_is_unexplained(gold_m5):
    ug = gold_m5.unexplained_gaps()
    assert len(ug) == 1
    g0 = pd.Timestamp(GOLD_GAP_NY[0], tz="America/New_York").tz_convert("UTC")
    assert ug["start"].iloc[0] == g0 and ug["hours"].iloc[0] == 3.0


def test_gold_regular_pauses_are_recognised(gold_m5):
    g = gold_m5.summary["gaps"]
    assert g["weekend"] >= 20
    assert g["daily_break"] >= 70
    assert g["holiday"] == 5          # Weihnachten, Neujahr, MLK, Presidents Day, Karfreitag
    hol = gold_m5.gaps[gold_m5.gaps["category"] == "holiday"]
    assert pd.Timestamp("2026-04-02 21:00", tz="UTC") in set(hol["start"])  # Karfreitag (Do 17:00 EDT)


def test_btc_pauses_and_spike(fake_ny):
    res = check_bars(frame(fake_ny, "BTCUSD", "M1"), "BTCUSD", "M1", get_calendar("24x7"), Q)
    g = res.summary["gaps"]
    assert g.get("data_gap", 0) == 0
    assert g["low_activity"] > 100       # einzelne Minuten ohne Ticks
    assert g["recurring_pause"] >= 20    # wöchentliche Wartung
    spikes = res.anomalies[res.anomalies["kind"] == "spike"]
    assert list(spikes["time"]) == [pd.Timestamp(BTC_SPIKE_UTC, tz="UTC")]
    assert (res.anomalies["kind"] == "outlier").sum() == 0


def test_real_gap_in_24x7_market_is_unexplained(fake_ny):
    df = frame(fake_ny, "BTCUSD", "M1")
    cut = (df["time"] >= "2026-04-01 10:00") & (df["time"] < "2026-04-01 16:00")
    res = check_bars(df[~cut], "BTCUSD", "M1", get_calendar("24x7"), Q)
    ug = res.unexplained_gaps()
    assert len(ug) == 1 and ug["hours"].iloc[0] == pytest.approx(6.0, abs=0.05)


def test_integrity_findings():
    df = pd.DataFrame({
        "time": pd.date_range("2026-01-05", periods=6, freq="1min", tz="UTC"),
        "time_server": [1, 2, 2, 4, 3, 6],
        "open": [10, 10, 10, 10, 10, -1.0], "high": [11, 11, 9, 11, 11, 11.0],
        "low": [9, 9, 9.5, 9, 9, 9.0], "close": [10, 10, 10, 12, 10, 10.0],
        "tick_volume": [1, 1, 1, 1, 0, 1], "spread": [1, 1, 1, 1, 1, -3], "real_volume": 0,
    })
    s, invalid = check_integrity(df)
    assert s["duplicates"] == 1 and s["non_monotonic"] == 1
    assert s["invalid_ohlc"] == 3          # High<Low, Close>High, Open<Low (bei Preis -1)
    assert s["nonpositive_prices"] == 1 and s["negative_spread"] == 1 and s["zero_tick_volume"] == 1
    assert len(invalid) == 4               # betroffene Zeilen (manche haben mehrere Fehler)


def test_weekend_jump_is_not_an_outlier():
    t = pd.date_range("2026-01-05", periods=3000, freq="1min", tz="UTC")
    t = t[(t < "2026-01-06 12:00") | (t >= "2026-01-07 12:00")]  # 24h Lücke
    close = 100 + np.random.default_rng(3).normal(0, 0.01, len(t)).cumsum()
    close[t >= "2026-01-07 12:00"] += 5.0                     # großer Sprung über die Lücke
    df = pd.DataFrame({"time": t, "time_server": t.as_unit("ns").asi8 // 10**9, "open": close,
                       "high": close + 0.01, "low": close - 0.01, "close": close,
                       "tick_volume": 1, "spread": 1, "real_volume": 0})
    assert detect_anomalies(df, "M1", Q).empty


def test_consistency_detects_tampered_bar(fake_ny):
    m1 = frame(fake_ny, "XAUUSD", "M1")
    h1 = frame(fake_ny, "XAUUSD", "H1")
    ok = consistency_with_m1(m1, h1, "H1")
    assert ok["close_match_pct"] == 100.0
    h1.loc[h1.index[len(h1) // 2], "close"] += 5
    bad = consistency_with_m1(m1, h1, "H1")
    assert bad["close_match_pct"] < 100.0
