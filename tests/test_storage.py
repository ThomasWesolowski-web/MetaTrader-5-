import numpy as np
import pandas as pd

from tradingbot.server_time import parse_scheme
from tradingbot.storage import MarketStore, rates_to_frame

RATE_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]


def rates(start_server, n, step=3600, price=100.0):
    a = np.zeros(n, dtype=RATE_DTYPE)
    a["time"] = start_server + np.arange(n) * step
    a["open"] = a["high"] = a["low"] = a["close"] = price + np.arange(n)
    a["high"] += 1
    a["tick_volume"] = 5
    return a


S = parse_scheme("fixed:+00:00")
T0 = int(pd.Timestamp("2025-12-31 20:00", tz="UTC").timestamp())


def test_upsert_partitions_by_year_and_counts(tmp_path):
    st = MarketStore(tmp_path)
    added = st.upsert("XAUUSD", "H1", rates_to_frame(rates(T0, 10), S))
    assert added == 10
    files = sorted(p.name for p in (tmp_path / "market" / "XAUUSD" / "H1").glob("*.parquet"))
    assert files == ["2025.parquet", "2026.parquet"]
    assert not list((tmp_path / "market").rglob("*.tmp"))
    df = st.load("XAUUSD", "H1")
    assert len(df) == 10 and df["time_server"].is_monotonic_increasing
    assert str(df["time"].dtype) == "datetime64[ns, UTC]"


def test_upsert_deduplicates_and_keeps_latest(tmp_path):
    st = MarketStore(tmp_path)
    st.upsert("X", "H1", rates_to_frame(rates(T0, 10), S))
    newer = rates(T0 + 5 * 3600, 10, price=500.0)  # 5 überlappende + 5 neue Kerzen
    assert st.upsert("X", "H1", rates_to_frame(newer, S)) == 5
    df = st.load("X", "H1")
    assert len(df) == 15
    assert df["time_server"].is_unique
    assert df.loc[df["time_server"] == T0 + 5 * 3600, "close"].item() == 500.0


def test_load_range_and_bounds(tmp_path):
    st = MarketStore(tmp_path)
    st.upsert("X", "H1", rates_to_frame(rates(T0, 48), S))
    part = st.load("X", "H1", start="2026-01-01 00:00", end="2026-01-01 06:00")
    assert len(part) == 6
    b = st.bounds("X", "H1")
    assert b == {"first_server": T0, "last_server": T0 + 47 * 3600, "rows": 48}
    assert st.bounds("X", "M1") is None
    assert st.load("X", "M1").empty


def test_rewrite_utc_keeps_server_time(tmp_path):
    st = MarketStore(tmp_path)
    st.upsert("X", "H1", rates_to_frame(rates(T0, 10), S))
    st.rewrite_utc("X", "H1", parse_scheme("fixed:+02:00"))
    df = st.load("X", "H1")
    assert df["time_server"].iloc[0] == T0
    assert df["time"].iloc[0] == pd.Timestamp("2025-12-31 18:00", tz="UTC")


def test_meta_roundtrip(tmp_path):
    st = MarketStore(tmp_path)
    st.write_meta("X", {"a": 1})
    assert st.read_meta("X") == {"a": 1}
    assert st.read_meta("Y") == {}
