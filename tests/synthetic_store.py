"""Synthetischer Datenbestand über mehrere Jahre für Backtest-Tests (Zufallspfad, ohne echte Kante).

Gold: Mo–Fr (Serverzeit ny_close), Bitcoin: 24/7, EURUSD: Mo–Fr. Spezifikationen wie bei Vantage
(BTCUSD im Modus FOREX_NO_LEVERAGE). Optional lässt sich eine künstliche Kante einbauen."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.server_time import parse_scheme
from tradingbot.storage import MarketStore, rates_to_frame

TF = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "D1": 86400}
DT = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
      ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]

SPECS = {
    "XAUUSD": dict(symbol="XAUUSD", description="Gold", currency_base="XAU", currency_profit="USD",
                   currency_margin="USD", digits=2, point=0.01, contract_size=100.0, tick_size=0.01, tick_value=0.86,
                   volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=20, freeze_level=0,
                   calc_mode="CFDLEVERAGE", swap_long=-77.44, swap_short=36.7, swap_mode=1, margin_rate=1.0,
                   swap_rollover3days=3),
    "BTCUSD": dict(symbol="BTCUSD", description="Bitcoin", currency_base="BTC", currency_profit="USD",
                   currency_margin="BTC", digits=2, point=0.01, contract_size=1.0, tick_size=0.01, tick_value=0.0086,
                   volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=0, freeze_level=0,
                   calc_mode="FOREX_NO_LEVERAGE", swap_long=-20.0, swap_short=0.0, swap_mode=5, margin_rate=0.002,
                   swap_rollover3days=3),
}


def _series(start, end, freq_s, weekdays_only, price, vol, spread, rng, edge=None):
    t = pd.date_range(start, end, freq=f"{freq_s}s", tz="UTC", inclusive="left")
    if weekdays_only:
        t = t[t.weekday < 5]
    r = rng.normal(0, vol, len(t))
    if edge is not None:
        r = edge(t, r)
    close = price * np.exp(np.cumsum(r))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wig = np.abs(rng.normal(0, vol * 0.6, len(t))) * close
    a = np.zeros(len(t), dtype=DT)
    a["time"] = parse_scheme("ny_close").to_server(t)
    a["open"], a["close"] = open_, close
    a["high"] = np.maximum(open_, close) + wig
    a["low"] = np.minimum(open_, close) - wig
    a["tick_volume"], a["spread"] = 10, spread
    return a


def build(root, start="2018-01-01", end="2024-01-01", tfs=("M15", "H1", "D1"), seed=1, edges=None) -> MarketStore:
    st = MarketStore(root)
    sch = parse_scheme("ny_close")
    rng = np.random.default_rng(seed)
    edges = edges or {}
    cfgs = {"XAUUSD": (True, 1800.0, 0.0012, 25), "BTCUSD": (False, 20000.0, 0.004, 1500),
            "EURUSD": (True, 1.10, 0.0006, 8)}
    for sym, (wk, px, vol_h, spr) in cfgs.items():
        for tf in tfs:
            if sym == "EURUSD" and tf not in ("M15", "H1", "D1"):
                continue
            vol = vol_h * np.sqrt(TF[tf] / 3600)
            st.upsert(sym, tf, rates_to_frame(_series(start, end, TF[tf], wk, px, vol, spr, rng, edges.get(sym)), sch))
    st.write_json("specs.json", {
        "account": {"currency": "EUR", "leverage": 500, "trade_mode": "DEMO", "server": "Synth-Demo"},
        "time_scheme": {"scheme": "ny_close"},
        "instruments": SPECS,
        "conversions": [{"symbol": "EURUSD", "base": "EUR", "quote": "USD"}],
    })
    return st
