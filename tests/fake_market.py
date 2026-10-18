"""Simulierter MT5-Markt für Tests (unabhängig von den Produktionsmodulen implementiert).

Bildet nach:
  * Serverzeit-Semantik: time = Wanduhr des Servers als Epoch (Schema ny_close, eu_dst oder fest)
  * Gold: So 18:00 – Fr 17:00 New York, Tagespause 17:00–18:00 (Broker schließt 2 Min. früher),
    US-Feiertage (geschlossen bzw. verkürzt), eine eingebaute echte Datenlücke
  * Bitcoin: 24/7, wöchentliche Wartung Sa 04:00–04:30 Serverzeit, einzelne Minuten ohne Ticks,
    ein Fehltick (Kursspitze mit sofortiger Rückkehr)
  * EURUSD: So 17:00 – Fr 17:00 New York
  * begrenzte Historie je Zeitrahmen (wie "Max. Balken im Chart") und Ablehnung zu großer
    Abfragen mit (-2, 'Terminal: Invalid params')
  * order_calc_profit / order_calc_margin; order_send/order_check lösen einen Fehler aus
"""

from __future__ import annotations

from collections import namedtuple

import numpy as np
import pandas as pd

TF = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
RATE_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
NYZ = "America/New_York"

Info = namedtuple("Info", "name description path currency_base currency_profit currency_margin digits point "
                          "trade_contract_size trade_tick_size trade_tick_value volume_min volume_max volume_step "
                          "trade_stops_level trade_freeze_level trade_calc_mode swap_long swap_short swap_mode visible "
                          "bid ask")
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags volume_real")
Account = namedtuple("Account", "login server company currency trade_mode leverage balance equity margin_mode")
Terminal = namedtuple("Terminal", "build maxbars connected")

GOLD_GAP_NY = ("2026-02-11 09:00", "2026-02-11 12:00")   # eingebaute echte Datenlücke (New Yorker Zeit)
BTC_SPIKE_UTC = "2026-03-18 14:07"                        # eingebauter Fehltick


def server_epoch(utc: pd.DatetimeIndex, scheme: str) -> np.ndarray:
    utc_s = utc.as_unit("ns").asi8 // 10**9
    naive = utc.tz_localize(None)
    if scheme == "ny_close":
        off = (utc.tz_convert(NYZ).tz_localize(None) - naive).total_seconds().to_numpy() + 7 * 3600
    elif scheme == "eu_dst":
        off = (utc.tz_convert("Europe/Athens").tz_localize(None) - naive).total_seconds().to_numpy()
    else:
        off = np.full(len(utc), int(scheme.split(":")[1]) * 3600)
    return (utc_s + off.astype("int64")).astype("int64")


def _gold_open(utc: pd.DatetimeIndex) -> np.ndarray:
    ny = utc.tz_convert(NYZ)
    wd, mins = ny.weekday.to_numpy(), (ny.hour * 60 + ny.minute).to_numpy()
    date = ny.normalize().tz_localize(None)
    is_open = np.zeros(len(utc), bool)
    is_open |= (wd <= 3) & ((mins < 16 * 60 + 58) | (mins >= 18 * 60))  # Mo–Do, Pause 16:58–18:00
    is_open |= (wd == 4) & (mins < 16 * 60 + 58)                          # Fr bis 16:58
    is_open |= (wd == 6) & (mins >= 18 * 60)                              # So ab 18:00
    full_closed = pd.to_datetime(["2025-12-25", "2026-01-01", "2026-04-03"])
    early_close = pd.to_datetime(["2026-01-19", "2026-02-16"])            # MLK, Presidents Day: 13:30–18:00
    for d in full_closed:   # Feiertag: Handel ruht vom Vortag 17:00 bis zum Feiertag 18:00
        is_open &= ~((date == d) & (mins < 18 * 60))
        is_open &= ~((date == d - pd.Timedelta(days=1)) & (mins >= 17 * 60))
    for d in early_close:
        is_open &= ~((date == d) & (mins >= 13 * 60 + 30) & (mins < 18 * 60))
    g0, g1 = (pd.Timestamp(x, tz=NYZ) for x in GOLD_GAP_NY)
    is_open &= ~((ny >= g0) & (ny < g1))
    return is_open


def _fx_open(utc: pd.DatetimeIndex) -> np.ndarray:
    ny = utc.tz_convert(NYZ)
    wd, mins = ny.weekday.to_numpy(), (ny.hour * 60 + ny.minute).to_numpy()
    return (wd <= 3) | ((wd == 4) & (mins < 17 * 60)) | ((wd == 6) & (mins >= 17 * 60))


class FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
    ORDER_TYPE_BUY = 0
    _CONST_TF = {1: "M1", 5: "M5", 15: "M15", 30: "M30", 16385: "H1", 16388: "H4", 16408: "D1"}

    def __init__(self, scheme="ny_close", now_utc="2026-04-20 12:00", days=150, available_bars=None,
                 max_request_bars=50_000, leverage=500, seed=7):
        self.scheme = scheme
        self.now_utc = pd.Timestamp(now_utc, tz="UTC")
        self.max_request_bars = max_request_bars
        self.leverage = leverage
        self.available_bars = dict(available_bars or {})   # tf -> sichtbare Kerzen (wie maxbars)
        self._err = (1, "Success")
        self.calls = []
        rng = np.random.default_rng(seed)
        end = self.now_utc + pd.Timedelta(days=30)          # Daten "in der Zukunft" erst sichtbar, wenn now fortschreitet
        minutes = pd.date_range(self.now_utc - pd.Timedelta(days=days), end, freq="1min", inclusive="left")
        self.specs = {
            "XAUUSD": dict(base="XAU", profit="USD", cs=100.0, point=0.01, digits=2, price=2600.0, vol=0.0004,
                           calc=4, margin_rate=1.0, spread=25, path="Metals\\XAUUSD"),
            "BTCUSD": dict(base="BTC", profit="USD", cs=1.0, point=0.01, digits=2, price=80000.0, vol=0.0008,
                           calc=4, margin_rate=25.0, spread=1700, path="Crypto\\BTCUSD"),
            "EURUSD": dict(base="EUR", profit="USD", cs=100000.0, point=1e-5, digits=5, price=1.08, vol=0.0001,
                           calc=0, margin_rate=1.0, spread=8, path="Forex\\EURUSD"),
        }
        self.m1, self.utc_m1 = {}, {}
        for sym, sp in self.specs.items():
            if sym == "XAUUSD":
                mask = _gold_open(minutes)
            elif sym == "EURUSD":
                mask = _fx_open(minutes)
            else:
                ts = server_epoch(minutes, scheme)
                srv = pd.to_datetime(ts, unit="s")
                maint = (srv.weekday == 5) & (srv.hour == 4) & (srv.minute < 30)
                mask = ~maint & (rng.random(len(minutes)) > 0.002)   # einzelne Minuten ohne Ticks
            utc = minutes[mask]
            r = rng.normal(0, sp["vol"], len(utc))
            close = sp["price"] * np.exp(np.cumsum(r))
            if sym == "BTCUSD":
                i = utc.get_indexer([pd.Timestamp(BTC_SPIKE_UTC, tz="UTC")])[0]
                close[i] *= 1.03                                        # Fehltick: +3 %, nächste Minute zurück
            open_ = np.concatenate([[close[0]], close[:-1]])
            wig = np.abs(rng.normal(0, sp["vol"] / 2, len(utc))) * close
            df = pd.DataFrame({"time": server_epoch(utc, scheme), "open": open_, "close": close,
                               "high": np.maximum(open_, close) + wig, "low": np.minimum(open_, close) - wig,
                               "tick_volume": rng.integers(1, 50, len(utc)), "spread": sp["spread"],
                               "real_volume": 0})
            df["utc"] = utc
            df = df.drop_duplicates("time", keep="last").reset_index(drop=True)
            self.utc_m1[sym] = pd.DatetimeIndex(df.pop("utc"))   # wahre UTC-Zeit jeder M1-Kerze
            self.m1[sym] = df
        self.bars = {sym: {tf: self._aggregate(df, tf) for tf in TF} for sym, df in self.m1.items()}

    # ------------------------------------------------------------------ Hilfen
    @staticmethod
    def _aggregate(m1: pd.DataFrame, tf: str) -> np.ndarray:
        if tf == "M1":
            df = m1
        else:
            key = m1["time"] // TF[tf] * TF[tf]
            g = m1.groupby(key)
            df = pd.DataFrame({"time": g["time"].first() // TF[tf] * TF[tf], "open": g["open"].first(),
                               "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                               "tick_volume": g["tick_volume"].sum(), "spread": g["spread"].min(),
                               "real_volume": g["real_volume"].sum()})
        arr = np.zeros(len(df), dtype=RATE_DTYPE)
        for name, _ in RATE_DTYPE:
            arr[name] = df[name].to_numpy()
        return arr

    def now_server(self) -> int:
        return int(server_epoch(pd.DatetimeIndex([self.now_utc]), self.scheme)[0])

    def _visible(self, sym: str, tf: str) -> np.ndarray:
        arr = self.bars[sym][tf]
        arr = arr[arr["time"] <= self.now_server()]   # inkl. laufender Kerze (wie MT5)
        n = self.available_bars.get(tf)
        return arr[-n:] if n else arr

    # ------------------------------------------------------------------ MT5-API
    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return True

    def shutdown(self):
        return True

    def last_error(self):
        return self._err

    def account_info(self):
        return Account(123456, "FakeBroker-Demo", "Fake Broker", "EUR", 0, self.leverage, 1000.0, 1000.0, 2)

    def terminal_info(self):
        return Terminal(6272, 100000, True)

    def _info(self, sym):
        sp = self.specs[sym]
        last = self.m1[sym]["close"].iloc[-1]
        eurusd = self._eurusd()
        tick_value = sp["cs"] * sp["point"] / eurusd
        return Info(sym, sym, sp["path"], sp["base"], sp["profit"], sp["base"] if sp["calc"] == 0 else sp["profit"],
                    sp["digits"], sp["point"], sp["cs"], sp["point"], tick_value, 0.01, 100.0, 0.01, 0, 0,
                    sp["calc"], -5.0, 1.0, 1, True, last, last)

    def symbols_get(self):
        return tuple(self._info(s) for s in self.specs)

    def symbol_info(self, sym):
        return self._info(sym) if sym in self.specs else None

    def symbol_select(self, sym, enable=True):
        return sym in self.specs

    def _eurusd(self):
        arr = self._visible("EURUSD", "M1")
        return float(arr["close"][-1])

    def symbol_info_tick(self, sym):
        if sym not in self.specs:
            return None
        arr = self._visible(sym, "M1")
        last = arr[-1]
        t = int(last["time"]) + 30 if sym == "BTCUSD" else int(last["time"])
        sp = self.specs[sym]
        bid = float(last["close"])
        return Tick(t, bid, bid + sp["spread"] * sp["point"], 0.0, 0, t * 1000, 6, 0.0)

    def copy_rates_from_pos(self, sym, tf_const, start, count):
        self.calls.append(("from_pos", sym, tf_const, start, count))
        if count > self.max_request_bars:
            self._err = (-2, "Terminal: Invalid params")
            return None
        arr = self._visible(sym, self._CONST_TF[tf_const])
        self._err = (1, "Success")
        return arr[max(0, len(arr) - start - count): len(arr) - start]

    def copy_rates_range(self, sym, tf_const, date_from, date_to):
        tf = self._CONST_TF[tf_const]
        self.calls.append(("range", sym, tf, int(date_from), int(date_to)))
        if (int(date_to) - int(date_from)) / TF[tf] > self.max_request_bars:
            self._err = (-2, "Terminal: Invalid params")
            return None
        arr = self._visible(sym, tf)
        sel = arr[(arr["time"] >= int(date_from)) & (arr["time"] <= int(date_to))]
        self._err = (1, "Success")
        return sel

    def order_calc_profit(self, action, sym, volume, price_open, price_close):
        sp = self.specs[sym]
        return round((price_close - price_open) * sp["cs"] * volume / self._eurusd(), 2)

    def order_calc_margin(self, action, sym, volume, price):
        sp = self.specs[sym]
        usd = volume * sp["cs"] * price / self.leverage * sp["margin_rate"]
        return round(usd / self._eurusd(), 2)

    def order_send(self, *a, **k):
        raise AssertionError("order_send darf in Phase 2 nie aufgerufen werden")

    order_check = order_send
