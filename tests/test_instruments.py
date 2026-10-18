import numpy as np
import pandas as pd
import pytest

from tradingbot.instruments import (CurrencyConverter, InstrumentSpec, RiskCalculator, find_conversion_symbol,
                                    margin_margin_currency, round_volume_down)
from tradingbot.pipeline import validate_calculations


def gold(**kw):
    d = dict(symbol="XAUUSD", description="Gold", currency_base="XAU", currency_profit="USD", currency_margin="USD",
             digits=2, point=0.01, contract_size=100.0, tick_size=0.01, tick_value=0.86, volume_min=0.01,
             volume_max=100.0, volume_step=0.01, stops_level=20, freeze_level=0, calc_mode="CFDLEVERAGE",
             swap_long=-50.0, swap_short=10.0, swap_mode=1)
    d.update(kw)
    return InstrumentSpec(**d)


def conv(eurusd=1.10):
    c = CurrencyConverter()
    c.add_live("EURUSD", "EUR", "USD", eurusd)
    return c


def test_pnl_in_eur():
    rc = RiskCalculator(gold(), conv(1.10), "EUR", 500)
    assert rc.pnl(1, 0.01, 4000.0, 4001.0)[0] == pytest.approx(1 / 1.10)       # 0,01 Lot, +1 USD
    assert rc.pnl(-1, 1.0, 4000.0, 3990.0)[0] == pytest.approx(1000 / 1.10)    # Short, 1 Lot, -10 USD


def test_position_size_rounds_down_and_respects_minimum():
    rc = RiskCalculator(gold(), conv(1.10), "EUR", 500)
    # 10 EUR Risiko, Stop 5 USD: 1 Lot verliert 500 USD = 454,55 EUR -> 0,022 Lot -> 0,02
    assert rc.position_size(10.0, 5.0) == 0.02
    assert rc.position_size(0.5, 10.0) == 0.0        # unter Mindestvolumen -> kein Trade
    assert rc.position_size(10.0, 0) == 0.0


def test_round_volume_down():
    assert round_volume_down(gold(), 0.029999) == 0.02
    assert round_volume_down(gold(), 0.03) == 0.03
    assert round_volume_down(gold(volume_step=1.0, volume_min=1.0), 2.7) == 2.0
    assert round_volume_down(gold(), 1000) == 100.0


@pytest.mark.parametrize("mode,expected", [("CFD", 100 * 4000.0), ("CFDLEVERAGE", 100 * 4000.0 / 500),
                                           ("FOREX", 100 / 500)])
def test_margin_modes(mode, expected):
    assert margin_margin_currency(gold(calc_mode=mode), 1.0, 4000.0, 500) == pytest.approx(expected)


def test_converter_uses_only_past_prices(tmp_path):
    from tradingbot.server_time import parse_scheme
    from tradingbot.storage import MarketStore, rates_to_frame
    st = MarketStore(tmp_path)
    a = np.zeros(3, dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                           ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])
    t0 = int(pd.Timestamp("2026-03-02 10:00", tz="UTC").timestamp())
    a["time"] = [t0, t0 + 3600, t0 + 7200]
    a["close"] = [1.10, 1.20, 1.30]
    a["open"] = a["high"] = a["low"] = a["close"]
    st.upsert("EURUSD", "H1", rates_to_frame(a, parse_scheme("fixed:+00:00")))
    c = CurrencyConverter.from_store(st, [{"symbol": "EURUSD", "base": "EUR", "quote": "USD"}])
    times = pd.to_datetime(["2026-03-02 10:30", "2026-03-02 11:00", "2026-03-02 12:59", "2026-03-02 20:00"], utc=True)
    r = c.rate("USD", "EUR", times)
    assert np.isnan(r[0])                              # erster Bar noch nicht abgeschlossen -> kein Kurs
    assert r[1:] == pytest.approx([1 / 1.10, 1 / 1.20, 1 / 1.30])
    assert c.rate("EUR", "USD", times[1:2])[0] == pytest.approx(1.10)
    with pytest.raises(KeyError):
        c.rate("USD", "JPY", times)


def test_find_conversion_symbol_ignores_stocks():
    syms = [{"name": "EURUSD.stock", "currency_base": "EUR", "currency_profit": "USD", "path": "Stocks\\X"},
            {"name": "EURUSD", "currency_base": "EUR", "currency_profit": "USD", "path": "Forex\\EURUSD"},
            {"name": "EURUSD.mini", "currency_base": "EUR", "currency_profit": "USD", "path": "Forex\\mini"}]
    assert find_conversion_symbol(syms, "EUR", "USD")["symbol"] == "EURUSD"
    assert find_conversion_symbol(syms, "EUR", "JPY") is None


def test_validation_against_terminal(make_fake, client_for):
    fake = make_fake()
    client = client_for(fake)
    specs = {s: InstrumentSpec.from_mt5(client.symbol_info(s)) for s in ("XAUUSD", "BTCUSD")}
    v = validate_calculations(client, specs, [{"symbol": "EURUSD", "base": "EUR", "quote": "USD"}], "EUR", 500)
    for s in specs:
        assert abs(v[s]["profit_1lot_plus1pct"]["abweichung_pct"]) < 0.05
        assert abs(v[s]["tick_value"]["abweichung_pct"]) < 0.05
    assert specs["BTCUSD"].margin_rate == pytest.approx(25.0, rel=1e-3)   # kalibriert: eff. Hebel 1:20
    assert v["BTCUSD"]["margin_1lot"]["effektiver_hebel"] == pytest.approx(20.0, rel=1e-2)


def test_spec_roundtrip():
    g = gold()
    assert InstrumentSpec.from_dict(g.to_dict()) == g
