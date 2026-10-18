import numpy as np
import pandas as pd
import pytest

from fake_market import FakeMT5
from tradingbot.server_time import (FixedScheme, daily_break_events, detect_scheme, estimate_live_offset,
                                    parse_scheme)


def ts(s):
    return pd.Timestamp(s, tz="UTC")


@pytest.mark.parametrize("scheme,utc,offset_h", [
    ("ny_close", "2026-01-15 12:00", 2),   # US-Winterzeit
    ("ny_close", "2026-07-01 12:00", 3),   # US-Sommerzeit
    ("ny_close", "2026-03-20 12:00", 3),   # nach US-, vor EU-Umstellung
    ("eu_dst", "2026-03-20 12:00", 2),
    ("eu_dst", "2026-07-01 12:00", 3),
    ("fixed:+03:00", "2026-01-15 12:00", 3),
    ("fixed:-01:30", "2026-01-15 12:00", -1.5),
])
def test_offsets(scheme, utc, offset_h):
    assert parse_scheme(scheme).offset_at(ts(utc)) == offset_h * 3600


@pytest.mark.parametrize("scheme", ["ny_close", "eu_dst", "fixed:+02:00"])
def test_roundtrip_utc_server_utc(scheme):
    s = parse_scheme(scheme)
    utc = pd.date_range("2026-01-01", "2026-10-15", freq="37min", tz="UTC")  # vor der Herbst-Umstellung
    back = s.to_utc(s.to_server(utc))
    assert (back == utc).all()


def test_server_epoch_semantics():
    # Server zeigt 15:00 an, wenn es 12:00 UTC ist (Sommer, UTC+3) -> Epoch der Wanduhr 15:00
    s = parse_scheme("ny_close")
    server = s.to_server(pd.DatetimeIndex([ts("2026-07-01 12:00")]))[0]
    assert server == pd.Timestamp("2026-07-01 15:00", tz="UTC").value // 10**9


def test_unknown_scheme():
    with pytest.raises(ValueError):
        parse_scheme("gmt+3")


def test_live_offset():
    now = ts("2026-10-18 09:00").timestamp()
    assert estimate_live_offset(int(now) + 3 * 3600 - 5, now) == 3 * 3600
    assert estimate_live_offset(int(now) + 2 * 3600, now) == 2 * 3600
    assert estimate_live_offset(int(now) + 3 * 3600 - 600, now) is None  # 10 Minuten alter Tick


@pytest.mark.parametrize("scheme,expected", [("ny_close", "ny_close"), ("eu_dst", "eu_dst"),
                                             ("fixed:+2", "fixed:+02:00")])
def test_detect_with_live_offset(scheme, expected):
    f = FakeMT5(scheme)
    live = estimate_live_offset(f.symbol_info_tick("BTCUSD").time, f.now_utc.timestamp())
    det = detect_scheme(f.bars["XAUUSD"]["H1"]["time"], live, f.now_utc)
    assert det.scheme.name == expected
    assert det.events > 50
    if scheme != "fixed:+2":
        assert det.confidence == "hoch"
        assert det.break_hour_ny == 17.0


@pytest.mark.parametrize("scheme", ["ny_close", "eu_dst"])
def test_detect_without_live_offset(scheme):
    f = FakeMT5(scheme)
    det = detect_scheme(f.bars["XAUUSD"]["H1"]["time"], None, f.now_utc)
    assert det.scheme.name == scheme
    assert det.scores[scheme] == 1.0
    assert max(v for k, v in det.scores.items() if k != scheme) < 0.95


def test_detect_too_few_events_is_low_confidence(fake_ny):
    h1 = fake_ny.bars["XAUUSD"]["H1"]["time"][-40:]
    det = detect_scheme(h1, 3 * 3600, fake_ny.now_utc)
    assert det.confidence == "niedrig"
    assert det.scheme.offset_at(fake_ny.now_utc) == 3 * 3600


def test_daily_break_events():
    t = np.array([0, 3600, 7200, 14400, 18000]) + 1_000_000_000 // 3600 * 3600
    assert list(daily_break_events(t)) == [t[2] + 3600]


def test_fixed_scheme_name():
    assert FixedScheme(-5400).name == "fixed:-01:30"
