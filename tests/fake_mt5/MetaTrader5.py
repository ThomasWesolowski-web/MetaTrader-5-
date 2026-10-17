"""Simuliertes MetaTrader5-Modul NUR für Tests unter Linux/ohne Terminal.

Liefert plausible Fake-Daten. Jeder Aufruf einer Order-Funktion löst einen Fehler aus,
damit Tests sicherstellen können, dass Phase 1 keine Trades auslöst.
"""
import random
import time
from collections import namedtuple

import numpy as np

__version__ = "fake-0.0"
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15 = 1, 5, 15
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
_TF_SECS = {1: 60, 5: 300, 15: 900, 16385: 3600, 16388: 14400, 16408: 86400}
COPY_TICKS_ALL = -1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

Terminal = namedtuple("Terminal", "name company path data_path build connected trade_allowed "
                      "tradeapi_disabled maxbars ping_last community_account language")
Account = namedtuple("Account", "login server company currency trade_mode leverage balance equity "
                     "margin_mode trade_allowed trade_expert")
Symbol = namedtuple("Symbol", "name description path currency_base currency_profit currency_margin digits point "
                    "trade_contract_size trade_tick_size trade_tick_value volume_min volume_max volume_step "
                    "trade_stops_level trade_freeze_level spread_float swap_long swap_short trade_mode "
                    "filling_mode visible bid last")
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags volume_real")

_NOW = int(time.time())
_PRICES = {"XAUUSD": (2650.0, 2, 0.01), "BTCUSD": (68000.0, 2, 0.01), "BTC": (36.0, 2, 0.01)}


def _sym(name, desc, path, base, digits, point, cs, vis=True):
    return Symbol(name, desc, path, base, "USD", "USD", digits, point, cs, point, 1.0, 0.01, 100.0, 0.01,
                  0, 0, True, -10.0, 2.0, 4, 3, vis, _PRICES.get(name, (1.1,))[0], 0.0)


_SYMBOLS = [
    _sym("EURUSD", "Euro vs US Dollar", "Forex\\EURUSD", "EUR", 5, 1e-5, 100000),
    _sym("XAUUSD", "Gold vs US Dollar", "Metals\\XAUUSD", "XAU", 2, 0.01, 100),
    _sym("XAUEUR", "Gold vs Euro", "Metals\\XAUEUR", "XAU", 2, 0.01, 100, vis=False),
    _sym("GOLD.mini", "Gold mini", "Metals\\GOLD.mini", "XAU", 2, 0.01, 10, vis=False),
    _sym("BTCUSD", "Bitcoin vs US Dollar", "Crypto\\BTCUSD", "BTC", 2, 0.01, 1),
    _sym("BTCEUR", "Bitcoin vs Euro", "Crypto\\BTCEUR", "BTC", 2, 0.01, 1, vis=False),
    # Wie beim echten MetaQuotes-Demo-Server: Aktie/ETF mit Kürzel "BTC" – darf NICHT gewählt werden
    _sym("BTC", "Grayscale Bitcoin Mini Trust", "Stocks\\NYSE\\BTC", "USD", 2, 0.01, 1),
    _sym("BTCL", "Bitcoin ETF leveraged", "ETFs\\BTCL", "USD", 2, 0.01, 1),
]
_calls = []


def _forbidden(*a, **k):
    raise RuntimeError("Order-Funktion in Phase 1 aufgerufen!")


order_send = order_check = _forbidden


def initialize(**kw): _calls.append(("initialize", kw)); return True
def shutdown(): return True
def last_error(): return (1, "Success")
def version(): return (500, 4620, "01 Oct 2026")


def terminal_info():
    return Terminal("MetaTrader 5", "Demo Broker Ltd", "C:\\MT5", "C:\\Users\\x\\AppData", 4620, True, False,
                    False, 100000, 23456, False, "German")


def account_info():
    return Account(12345678, "DemoBroker-Demo", "Demo Broker Ltd", "EUR", 0, 100, 10000.0, 10000.0, 2, True, True)


def symbols_get(): return tuple(_SYMBOLS)
def symbol_info(n): return next((s for s in _SYMBOLS if s.name == n), None)
def symbol_select(n, enable=True): return symbol_info(n) is not None


def symbol_info_tick(n):
    base, _, pt = _PRICES.get(n, (1.1, 5, 1e-5))
    p = round(base + random.uniform(-5, 5), 2)
    now_ms = int(time.time() * 1000)
    return Tick(now_ms // 1000, p, round(p + 25 * pt, 2), 0.0, 0, now_ms, 6, 0.0)


_RATE_DT = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
            ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]


def copy_rates_from_pos(n, tf, start, count):
    secs = _TF_SECS[tf]
    if count > terminal_info().maxbars:
        return None  # echtes MT5 lehnt zu große Anfragen ab: (-2, 'Terminal: Invalid params')
    t = _NOW - _NOW % secs - np.arange(count)[::-1] * secs
    base = _PRICES.get(n, (1.1,))[0]
    close = base + np.cumsum(np.random.normal(0, base * 0.0005, count))
    arr = np.zeros(count, dtype=_RATE_DT)
    arr["time"], arr["open"], arr["close"] = t, close, close
    arr["high"], arr["low"] = close + 1, close - 1
    arr["tick_volume"], arr["spread"] = 100, 25
    return arr


def copy_ticks_range(n, frm, to, flags):
    return np.zeros(5000, dtype=[("time", "<i8"), ("bid", "<f8"), ("ask", "<f8")])
