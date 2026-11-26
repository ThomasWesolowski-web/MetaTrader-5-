"""Gold-Bot: Order-Block-Strategie für XAUUSD auf M15, automatisch, nur Demokonto (docs/goldbot.md)."""

from .config import GoldConfig, load
from .strategie import Analysis, Setup, analyse, to_frame
from .trader import GoldBot, GoldStore

__all__ = ["Analysis", "GoldBot", "GoldConfig", "GoldStore", "Setup", "analyse", "load", "to_frame"]
