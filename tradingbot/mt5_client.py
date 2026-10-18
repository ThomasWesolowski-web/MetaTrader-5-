"""Rein lesender Zugriff auf das MetaTrader-5-Terminal.

Diese Klasse kapselt das MetaTrader5-Modul. Sie enthält bewusst KEINE Funktion zum
Senden oder Prüfen von Orders (order_send/order_check). order_calc_profit und
order_calc_margin sind reine Rechenfunktionen des Terminals – sie erzeugen keine Order
und werden nur zur Validierung der eigenen Gewinn-/Margin-Berechnung genutzt.

Für Tests kann statt des echten Moduls ein Fake-Modul übergeben werden.
"""

from __future__ import annotations

import time

import numpy as np

from .timeframes import mt5_constant


class MT5Error(RuntimeError):
    pass


class MT5Client:
    def __init__(self, mt5_module=None, terminal_path: str | None = None, retry_sleep: float = 0.5):
        if mt5_module is None:
            import MetaTrader5 as mt5_module  # nur unter Windows verfügbar
        self.mt5 = mt5_module
        self.terminal_path = terminal_path
        self.retry_sleep = retry_sleep
        self.request_count = 0

    # ------------------------------------------------------------------ Verbindung
    def connect(self) -> None:
        kwargs = {"path": self.terminal_path} if self.terminal_path else {}
        if not self.mt5.initialize(**kwargs):
            raise MT5Error(f"MT5 initialize() fehlgeschlagen: {self.mt5.last_error()} – "
                           f"Terminal geöffnet und eingeloggt?")

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def last_error(self):
        return self.mt5.last_error()

    # ------------------------------------------------------------------ Infos
    @staticmethod
    def _as_dict(obj) -> dict | None:
        return None if obj is None else obj._asdict()

    def account_info(self) -> dict:
        info = self._as_dict(self.mt5.account_info())
        if info is None:
            raise MT5Error(f"account_info() fehlgeschlagen: {self.mt5.last_error()}")
        return info

    def terminal_info(self) -> dict:
        return self._as_dict(self.mt5.terminal_info()) or {}

    def symbols(self) -> list[dict]:
        return [s._asdict() for s in (self.mt5.symbols_get() or ())]

    def symbol_info(self, symbol: str, select: bool = True) -> dict | None:
        if select:
            self.mt5.symbol_select(symbol, True)  # nur Marktübersicht, kein Handel
        return self._as_dict(self.mt5.symbol_info(symbol))

    def tick(self, symbol: str) -> dict | None:
        return self._as_dict(self.mt5.symbol_info_tick(symbol))

    # ------------------------------------------------------------------ Kursdaten
    def rates_range(self, symbol: str, tf: str, from_server: int, to_server: int,
                    retry_empty: bool = True) -> np.ndarray | None:
        """Kerzen mit Startzeit in [from_server, to_server] (beide inklusive).

        Die Zeitangaben sind MT5-Serverzeit als Epoch-Sekunden – genau die Semantik, die
        MT5 auch in den gelieferten Kerzen verwendet. Leeres Ergebnis -> einmal kurz warten
        und erneut versuchen, weil das Terminal ältere Historie erst vom Server nachlädt."""
        const = mt5_constant(self.mt5, tf)
        rates = None
        for attempt in range(2):
            self.request_count += 1
            rates = self.mt5.copy_rates_range(symbol, const, int(from_server), int(to_server))
            if rates is not None and len(rates) > 0:
                return rates
            if not retry_empty:
                break
            if attempt == 0 and self.retry_sleep:
                time.sleep(self.retry_sleep)
        return rates

    def rates_from_pos(self, symbol: str, tf: str, start: int, count: int) -> np.ndarray | None:
        self.request_count += 1
        return self.mt5.copy_rates_from_pos(symbol, mt5_constant(self.mt5, tf), start, count)

    # ------------------------------------------------------------------ Rechenfunktionen (keine Orders)
    def calc_profit_buy(self, symbol: str, volume: float, price_open: float, price_close: float) -> float | None:
        return self.mt5.order_calc_profit(self.mt5.ORDER_TYPE_BUY, symbol, volume, price_open, price_close)

    def calc_profit_sell(self, symbol: str, volume: float, price_open: float, price_close: float) -> float | None:
        return self.mt5.order_calc_profit(self.mt5.ORDER_TYPE_SELL, symbol, volume, price_open, price_close)

    def calc_margin_buy(self, symbol: str, volume: float, price: float) -> float | None:
        return self.mt5.order_calc_margin(self.mt5.ORDER_TYPE_BUY, symbol, volume, price)
