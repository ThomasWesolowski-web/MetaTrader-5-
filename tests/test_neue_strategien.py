"""EMA 20 + Wochen-VWAP und Opening Range Breakout (Ergänzung Phase 4, 20.10.2026): Regeln nachgerechnet."""

import numpy as np
import pandas as pd
import pytest

from tradingbot import indicators as ind
from tradingbot.backtest.account import RiskRules
from tradingbot.backtest.runner import CRITERIA, _same_signals, _signals_blocked, strategies_for, trade_strategies
from tradingbot.strategies import base, create


def frame(t, o, h, l, c, vol=None):
    t = pd.DatetimeIndex(t)
    n = len(t)
    return pd.DataFrame({"time": t, "time_server": t.as_unit("ns").asi8 // 10**9, "open": o, "high": h, "low": l,
                         "close": c, "spread": 0, "tick_volume": np.ones(n, int) if vol is None else vol})


# ------------------------------------------------------------------ Wochen-VWAP
def test_weekly_vwap_resets_sunday_17_new_york_and_first_week_is_nan():
    # Winterzeit: So 17:00 NY = 22:00 UTC
    t = pd.DatetimeIndex(["2024-01-07 20:00", "2024-01-07 22:00", "2024-01-08 10:00", "2024-01-14 21:00",
                          "2024-01-14 22:00", "2024-01-15 01:00"], tz="UTC")
    h = np.array([11.0, 21, 31, 41, 51, 61])
    l = h - 2
    c = h - 1                                   # typischer Kurs = h - 1
    v = np.array([5, 1, 3, 2, 4, 0])
    vw = ind.weekly_vwap(t, h, l, c, v)
    assert np.isnan(vw[0])                      # erste Woche in den Daten unvollständig
    # zweite Woche beginnt So 22:00 UTC (Kerze 1); So 14.01. 21:00 UTC gehört noch dazu
    w2 = ind.weekly_vwap(t[1:], h[1:], l[1:], c[1:], v[1:])
    assert np.isnan(w2[:3]).all()               # nun ist diese Woche die erste
    assert vw[1] == pytest.approx(20.0) and vw[2] == pytest.approx((20 * 1 + 30 * 3) / 4)
    assert vw[3] == pytest.approx((20 * 1 + 30 * 3 + 40 * 2) / 6)
    assert vw[4] == pytest.approx(50.0)         # neue Woche: Neustart
    assert vw[5] == pytest.approx(50.0)         # Kerze ohne Volumen ändert den VWAP nicht


def test_ema_vwap_rules():
    s = create("ema_vwap", atr_mult=2.0)
    assert s.timeframes == ("M15", "M30", "H1", "H4")
    rng = np.random.default_rng(5)
    n = 3000
    t = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    bars = frame(t, o, np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1, c, rng.integers(1, 100, n))
    sig = s.entries(bars)
    assert len(sig) > 10
    e = ind.ema(c, 20)
    vw = ind.weekly_vwap(t, bars["high"].to_numpy(), bars["low"].to_numpy(), c, bars["tick_volume"].to_numpy())
    a = ind.atr(bars["high"].to_numpy(), bars["low"].to_numpy(), c, 14)
    for i, d, st in zip(sig.idx, sig.direction, sig.stop):
        assert d * (c[i] - e[i]) > 0 and d * (c[i] - vw[i]) > 0          # beide Bedingungen erfüllt ...
        assert not (d * (c[i - 1] - e[i - 1]) > 0 and d * (c[i - 1] - vw[i - 1]) > 0)   # ... erstmals
        assert st == pytest.approx(c[i] - d * 2.0 * a[i])
    assert np.isnan(sig.target).all()
    assert np.array_equal(sig.exit_long, c < e) and np.array_equal(sig.exit_short, c > e)


# ------------------------------------------------------------------ Opening Range Breakout
def day_m5(day: str, start_utc: str, closes: list[float], spread: float = 0.5):
    t = pd.date_range(f"{day} {start_utc}", periods=len(closes), freq="5min", tz="UTC")
    c = np.array(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return t, o, np.maximum(o, c) + spread, np.minimum(o, c) - spread, c


def test_orb_new_york_long_breakout_stop_target_and_close():
    # 15.01.2024 (Winterzeit): 09:30 NY = 14:30 UTC. Daten ab 14:00 UTC.
    closes = [100] * 6 + [100, 101, 99] + [100, 100, 102.5, 103, 101] + [100] * 70
    t, o, h, l, c = day_m5("2024-01-15", "14:00", closes)
    bars = frame(t, o, h, l, c)
    sig = create("opening_range_breakout", session="new_york", range_min=15, rr=2.0).entries(bars)
    # Range = Kerzen 14:30, 14:35, 14:40 UTC (Index 6–8): Hoch 101,5, Tief 98,5
    assert list(sig.idx) == [11] and list(sig.direction) == [1]         # erster Schluss über 101,5
    assert sig.stop[0] == pytest.approx(98.5)
    assert sig.target[0] == pytest.approx(102.5 + 2 * (102.5 - 98.5))
    # Ausstieg spätestens zum Handelsschluss 16:00 NY = 21:00 UTC: Kerze 20:55 UTC erreicht ihn
    flat_times = t[sig.exit_long]
    assert pd.Timestamp("2024-01-15 20:55", tz="UTC") in flat_times
    assert not sig.exit_long[(t >= "2024-01-15 14:45") & (t < "2024-01-15 20:55")].any()


def test_orb_one_trade_per_day_short_and_london_summer_time():
    # 15.07.2024 (Sommerzeit): 08:00 London = 07:00 UTC
    closes = [100] * 12 + [100, 100.2, 99.8] + [98.0, 97.0, 103.0, 96.0] + [100] * 20
    t, o, h, l, c = day_m5("2024-07-15", "06:00", closes, spread=0.1)
    sig = create("opening_range_breakout", session="london", range_min=15, rr=1.5).entries(frame(t, o, h, l, c))
    assert list(sig.idx) == [15] and list(sig.direction) == [-1]        # nur der erste Ausbruch des Tages
    assert sig.stop[0] == pytest.approx(100.3)                           # Range-Hoch


def test_orb_skips_incomplete_range_and_too_coarse_timeframe():
    closes = [100] * 6 + [100, 101, 99] + [100, 103] + [100] * 10
    t, o, h, l, c = day_m5("2024-01-15", "14:00", closes)
    keep = np.ones(len(t), bool)
    keep[7] = False                                                       # eine Kerze der Range fehlt
    b = frame(t[keep], o[keep], h[keep], l[keep], c[keep])
    assert len(create("opening_range_breakout", session="new_york", range_min=15).entries(b)) == 0
    # 15-Minuten-Range auf H1 nicht bildbar -> keine Signale statt falscher Range
    t1 = pd.date_range("2024-01-15", periods=100, freq="1h", tz="UTC")
    c1 = np.linspace(100, 120, 100)
    assert len(create("opening_range_breakout", range_min=15).entries(frame(t1, c1, c1 + 1, c1 - 1, c1))) == 0


def test_orb_unknown_session_rejected():
    t, o, h, l, c = day_m5("2024-01-15", "14:00", [100] * 20)
    with pytest.raises(ValueError):
        create("opening_range_breakout", session="tokio").entries(frame(t, o, h, l, c))


# ------------------------------------------------------------------ Einbindung in den Backtest
def test_timeframes_per_strategy():
    names = trade_strategies()
    assert "ema_vwap" in names and "opening_range_breakout" in names
    assert "ema_vwap" not in strategies_for("M5", names) and "ema_vwap" in strategies_for("H4", names)
    assert "opening_range_breakout" in strategies_for("M1", names)
    assert "opening_range_breakout" not in strategies_for("M30", names)
    assert strategies_for("H4", names) == ["ema_vwap"]                   # Phase-3-Strategien bleiben bei M1–H1
    assert "sma_box_trend" in strategies_for("M15", names) and "sma_box_trend" not in strategies_for("H1", names)
    old = [n for n in names if n not in ("ema_vwap", "opening_range_breakout", "sma_box_trend")]
    assert len(old) == 12 and all(len(strategies_for(tf, old)) == 12 for tf in ("M1", "M5", "M15", "M30", "H1"))


@pytest.mark.parametrize("name", ["ema_vwap", "opening_range_breakout"])
def test_blocks_use_time_based_warmup(name):
    """Auch mit sehr kurzem Kerzen-Vorlauf (100) muss die Blockberechnung exakt stimmen: Der Vorlauf wird für
    diese Strategien auf die nötige Zeitspanne verlängert (ganze Woche bzw. ganzer Tag)."""
    rng = np.random.default_rng(8)
    n = 30_000
    t = pd.date_range("2024-03-01", periods=n, freq="1min", tz="UTC")
    c = 2000 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    w = np.abs(rng.normal(0, 0.0003, n)) * c
    bars = frame(t, o, np.maximum(o, c) + w, np.minimum(o, c) - w, c, rng.integers(1, 100, n))
    grid = base._REGISTRY[name].param_grid
    full = _signals_blocked(name, grid, bars, None)
    part = _signals_blocked(name, grid, bars, 4_000, warmup=100)
    assert sum(len(s) for s in full) > 0
    for p, a, b in zip(grid, full, part):
        assert _same_signals(a, b), (name, p)


def test_risk_rules_approved_21_10_2026():
    r = RiskRules()
    assert (r.initial, r.risk_per_trade, r.max_daily_loss, r.max_drawdown, r.max_positions) == \
        (1000.0, 0.015, 0.045, 0.20, 2)
    assert CRITERIA["validation_max_dd_pct"] == 20.0 and CRITERIA["test_max_dd_pct"] == 20.0


# ------------------------------------------------------------------ SMA 20/50 mit Box-Ausbruch (21.10.2026)
def test_sma_matches_pandas_and_is_start_independent():
    x = np.random.default_rng(1).normal(100, 5, 3000)
    assert np.allclose(ind.sma(x, 50), pd.Series(x).rolling(50).mean().to_numpy(), equal_nan=True)
    assert np.array_equal(ind.sma(x, 50)[1000:], ind.sma(x[700:], 50)[300:])   # bitgenau unabhängig vom Start


def test_sma_box_trend_rules():
    s = create("sma_box_trend")
    assert s.timeframes == ("M15",) and base._REGISTRY["sma_box_trend"].param_grid == [{}]
    rng = np.random.default_rng(21)
    n = 20_000
    t = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0.00003, 0.003, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    h, l = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05
    sig = s.entries(frame(t, o, h, l, c))
    assert len(sig) > 20 and set(sig.direction) == {1, -1}
    sm = {k: pd.Series(c).rolling(k).mean().to_numpy() for k in (20, 50, 200, 1500)}
    up = sm[20] > sm[50]
    for j, d, st in zip(sig.idx, sig.direction, sig.stop):
        side = up if d == 1 else sm[20] < sm[50]
        # Kreuzung k innerhalb der 8 Kerzen davor, seitdem nicht zurückgenommen
        ks = [k for k in range(j - 8, j) if side[k] and not side[k - 1]]
        assert ks, j
        k = ks[-1]
        assert side[k:j + 1].all()
        hi, lo = h[k - 11:k + 1].max(), l[k - 11:k + 1].min()
        assert (c[j] > hi) if d == 1 else (c[j] < lo)
        assert not any((c[m] > hi) if d == 1 else (c[m] < lo) for m in range(k + 1, j))   # erster Ausbruch
        assert d * (c[j] - sm[200][j]) > 0 and d * (c[j] - sm[1500][j]) > 0
        assert st == (lo if d == 1 else hi)
    assert np.isnan(sig.target).all()
    assert np.array_equal(np.nonzero(sig.exit_long)[0],
                          [i for i in range(51, n) if sm[20][i] < sm[50][i] and not sm[20][i - 1] < sm[50][i - 1]])
