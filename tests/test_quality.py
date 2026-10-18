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


def test_quiet_market_does_not_inflate_outliers():
    """Mehr als die Hälfte der Kerzen ohne Kursänderung (Median-Rendite 0): kleine Bewegungen
    dürfen nicht als Ausreißer gelten, ein echter Fehltick schon (Fall BTCUSD M1 Juli 2020)."""
    n = 5000
    rng = np.random.default_rng(5)
    r = np.where(rng.random(n) < 0.7, 0.0, rng.normal(0, 5e-4, n))
    r[2000] = 0.002                       # normale 0,2-%-Bewegung
    r[3000], r[3001] = 0.03, -0.03        # Fehltick +3 % und zurück
    close = 9000 * np.exp(np.cumsum(r))
    t = pd.date_range("2020-07-01", periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame({"time": t, "time_server": t.as_unit("ns").asi8 // 10**9, "open": close,
                       "high": close, "low": close, "close": close, "tick_volume": 1, "spread": 1, "real_volume": 0})
    a = detect_anomalies(df, "M1", Q)
    assert t[2000] not in set(a["time"])
    assert list(a.loc[a["kind"] == "spike", "time"]) == [t[3000]]


def test_btc_broker_closures_on_weekend_and_holidays(fake_ny):
    """Beobachtet bei Vantage: Bitcoin früher am Wochenende geschlossen, jedes Jahr an Weihnachten
    und Neujahr. Das sind Broker-Schließzeiten, keine Datenfehler – ein Ausfall Mo–Mi bleibt unerklärt."""
    df = frame(fake_ny, "BTCUSD", "H1")
    cuts = [("2026-03-13 21:00", "2026-03-15 22:00"),   # Fr 17:00 – So 18:00 New York
            ("2025-12-24 22:00", "2025-12-25 22:00"),   # Weihnachten
            ("2026-03-30 21:00", "2026-04-01 21:00")]   # Mo–Mi: echter Ausfall
    keep = np.ones(len(df), bool)
    for a, b in cuts:
        keep &= ~((df["time"] >= a) & (df["time"] < b))
    res = check_bars(df[keep], "BTCUSD", "H1", get_calendar("24x7"), Q)
    cat = res.gaps.set_index("start")["category"]
    assert cat[pd.Timestamp(cuts[0][0], tz="UTC")] == "weekend"
    assert cat[pd.Timestamp(cuts[1][0], tz="UTC")] == "holiday"
    assert cat[pd.Timestamp(cuts[2][0], tz="UTC")] == "data_gap"


def test_multi_year_hole_is_not_a_holiday(fake_ny):
    """Regressionstest: Eine lange Datenlücke berührt Feiertage, ist aber ein Datenfehler (Fall Gold-Intraday
    2007–2017 bei Vantage) und muss als unerklärt gelten; 'lückenlos ab' zeigt den Neubeginn."""
    from tradingbot.quality import continuous_start
    df = frame(fake_ny, "XAUUSD", "H1")
    cut = (df["time"] > "2025-11-25") & (df["time"] < "2026-02-20")     # enthält Weihnachten, Neujahr, MLK
    res = check_bars(df[~cut], "XAUUSD", "H1", get_calendar("cme_metals"), Q)
    big = res.gaps.sort_values("hours").iloc[-1]
    assert big["category"] == "data_gap" and big["hours"] > 24 * 80
    assert pd.Timestamp(res.summary["continuous_from_utc"]) >= pd.Timestamp("2026-02-20", tz="UTC")
    assert continuous_start(df) == df["time"].iloc[0]


def _sparse_early(df, until, keep_every=3):
    """Vor 'until' nur jede keep_every-te Kerze behalten (dünne Historie wie Gold-Intraday bei Vantage)."""
    early = df["time"] < until
    pos = np.arange(len(df))
    return df[~early | (pos % keep_every == 0)].reset_index(drop=True)


def test_long_recurring_gaps_are_not_regular_pauses(fake_ny):
    """Regressionstest: Wiederkehrende, lange Lücken (fehlende Daten) dürfen nicht als 'regelmäßige Pause'
    gelten – nur kurze Wartungsfenster (<= 6 h)."""
    df = frame(fake_ny, "BTCUSD", "H1")
    hours = pd.DatetimeIndex(df["time"]).hour
    sparse = df[~((hours >= 2) & (hours < 14))]                 # jeden Tag 12 h ohne Daten
    res = check_bars(sparse, "BTCUSD", "H1", get_calendar("24x7"), Q)
    assert res.summary["gaps"].get("recurring_pause", 0) == 0
    assert res.summary["gaps"]["data_gap"] > 100
    assert res.summary["coverage_pct"] < 60


def test_dense_start_skips_sparse_early_history(fake_ny):
    from tradingbot.quality import dense_start, monthly_density
    df = _sparse_early(frame(fake_ny, "XAUUSD", "H1"), "2026-02-01")
    cal = get_calendar("cme_metals")
    dens = monthly_density(df, "H1", cal)
    assert dens.loc["2025-12-01"] < 0.5 < dens.loc["2026-03-01"]
    assert dense_start(df, "H1", cal) == pd.Timestamp("2026-02-01", tz="UTC")
    full = frame(fake_ny, "XAUUSD", "H1")
    assert dense_start(full, "H1", cal) == full["time"].iloc[0]
