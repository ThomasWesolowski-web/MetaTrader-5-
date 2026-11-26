"""Telegram-Signale (z. B. "FX Culture | Premium FX") automatisch handeln – siehe docs/telegram_signale.md."""

from .config import TelegramConfig, load
from .parser import Signal, parse_followup, parse_signal
from .trader import SignalStore, SignalTrader

__all__ = ["TelegramConfig", "load", "Signal", "parse_signal", "parse_followup", "SignalStore", "SignalTrader"]
