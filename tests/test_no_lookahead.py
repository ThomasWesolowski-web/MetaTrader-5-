"""Kein Blick in die Zukunft: Signale auf gekürzten Daten müssen mit denen auf vollständigen Daten
übereinstimmen (für alle Kerzen, die in beiden Datensätzen vollständig bekannt sind)."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest.runner import trade_strategies
from tradingbot.strategies import base, create


@pytest.fixture(scope="module")
def bars():
    rng = np.random.default_rng(11)
    n = 4000
    t = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    c = 2000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    w = np.abs(rng.normal(0, 0.0006, n)) * c   # Dochte realistisch: ATR ≈ 1,3–1,5 × Schlusskurs-Schwankung
    return pd.DataFrame({"time": t, "time_server": t.as_unit("ns").asi8 // 10**9, "open": o, "close": c,
                         "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "spread": 20,
                         "tick_volume": rng.integers(1, 200, n)})


@pytest.mark.parametrize("name", trade_strategies())
def test_signals_do_not_change_when_future_is_removed(bars, name):
    for params in (base._REGISTRY[name].param_grid or [{}])[:2]:
        full = create(name, **params).entries(bars)
        assert len(full) > 0, f"{name} {params}: keine Signale auf Testdaten"
        for cut in (1500, 2700, 3999):
            part = create(name, **params).entries(bars.iloc[:cut].reset_index(drop=True))
            m = full.idx < cut
            assert np.array_equal(part.idx, full.idx[m]), (name, params, cut)
            assert np.array_equal(part.direction, full.direction[m])
            assert np.allclose(part.stop, full.stop[m])
            assert np.allclose(part.target, full.target[m], equal_nan=True)
            for a, b in ((part.exit_long, full.exit_long), (part.exit_short, full.exit_short)):
                if b is not None:
                    assert np.array_equal(np.asarray(a), np.asarray(b)[:cut])


@pytest.mark.parametrize("name", trade_strategies())
def test_stops_on_correct_side_and_documented(bars, name):
    cls = base._REGISTRY[name]
    assert cls.description and cls.rules and cls.param_grid
    s = create(name).entries(bars)
    c = bars["close"].to_numpy()[s.idx]
    assert np.all(s.direction * (c - s.stop) > 0)
