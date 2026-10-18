import pandas as pd
import pytest

from tradingbot.strategies import MarketData, Strategy, available, create, register
from tradingbot.strategies import base as strategy_base


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(strategy_base._REGISTRY)
    yield
    strategy_base._REGISTRY.clear()
    strategy_base._REGISTRY.update(saved)


def test_register_create_and_signals():
    @register
    class AlwaysLong(Strategy):
        name = "test_long"
        default_params = {"size": 1}

        def generate_signals(self, bars):
            return pd.Series(1, index=bars.index)

    assert "test_long" in available()
    s = create("test_long", size=1)
    bars = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    assert list(s.validate_signals(bars, s.generate_signals(bars))) == [1, 1]
    with pytest.raises(ValueError):
        create("test_long", foo=2)
    with pytest.raises(ValueError):
        create("gibt_es_nicht")
    with pytest.raises(ValueError):
        register(AlwaysLong)


def test_invalid_signals_rejected():
    class Bad(Strategy):
        name = "bad"

        def generate_signals(self, bars):
            return pd.Series(2, index=bars.index)

    bars = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2026-01-01", periods=1, tz="UTC"))
    with pytest.raises(ValueError):
        Bad().validate_signals(bars, Bad().generate_signals(bars))


def test_market_data_reads_store(make_fake, client_for, tmp_path):
    from tradingbot.importer import Importer
    from tradingbot.server_time import parse_scheme
    from tradingbot.storage import MarketStore
    fake = make_fake()
    st = MarketStore(tmp_path)
    Importer(client_for(fake), st, parse_scheme("ny_close"), now_utc_fn=lambda: fake.now_utc,
             log=lambda *a: None).update("XAUUSD", "H1")
    md = MarketData(st)
    bars = md.bars("XAUUSD", "H1", start="2026-04-01", end="2026-04-02")
    assert bars.index.min() >= pd.Timestamp("2026-04-01", tz="UTC")
    assert bars.index.max() < pd.Timestamp("2026-04-02", tz="UTC")
    assert {"open", "high", "low", "close"} <= set(bars.columns)
