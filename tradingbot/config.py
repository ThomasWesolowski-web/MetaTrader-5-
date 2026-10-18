"""Einstellungen aus config.toml (mit sinnvollen Standardwerten)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .timeframes import seconds


@dataclass
class QualityConfig:
    # Toleranz für Abweichungen der Broker-Handelszeiten vom Referenzkalender (Minuten)
    session_tolerance_min: int = 20
    # Lücken bis zu dieser Länge innerhalb der Handelszeit gelten bei M1/M5 als "keine Ticks" (Minuten)
    low_activity_max_min: int = 5
    # Robuster z-Wert (Median/MAD), ab dem eine Rendite als Ausreißer gilt
    outlier_z: float = 15.0
    # z-Wert für Kursspitzen mit sofortiger Gegenbewegung (typische Fehlticks)
    spike_z: float = 8.0
    # Faktor, um den die Kerzenspanne (High-Low) den gleitenden Median übersteigen muss
    range_factor: float = 25.0
    # Fensterlänge (Kerzen) für die gleitenden Medianwerte
    window: int = 1000
    # Unerklärte Lücken, die zur gleichen Serverzeit in so vielen Wochen auftreten, gelten als regelmäßige Pause
    recurring_min_weeks: int = 3


@dataclass
class Config:
    data_root: Path = Path("data")
    report_dir: Path = Path("reports")
    symbols: list[str] = field(default_factory=lambda: ["XAUUSD", "BTCUSD"])
    timeframes: list[str] = field(default_factory=lambda: ["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    # Handelskalender je Symbol (siehe sessions.CALENDARS)
    calendars: dict[str, str] = field(default_factory=lambda: {"XAUUSD": "cme_metals", "BTCUSD": "24x7"})
    terminal_path: str | None = None
    chunk_bars: int = 40_000
    earliest: str = "2000-01-01"
    # "auto" oder fest: "ny_close", "eu_dst", "fixed:+03:00"
    server_timezone: str = "auto"
    timezone_reference_symbol: str = "XAUUSD"
    quality: QualityConfig = field(default_factory=QualityConfig)

    def calendar_for(self, symbol: str) -> str:
        return self.calendars.get(symbol, "24x7")


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path is None or not Path(path).exists():
        return cfg
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    data = raw.get("data", {})
    if "root" in data:
        cfg.data_root = base / data["root"]
    else:
        cfg.data_root = base / cfg.data_root
    cfg.report_dir = base / data.get("report_dir", str(cfg.report_dir))
    cfg.symbols = list(data.get("symbols", cfg.symbols))
    cfg.timeframes = list(data.get("timeframes", cfg.timeframes))
    cfg.chunk_bars = int(data.get("chunk_bars", cfg.chunk_bars))
    cfg.earliest = str(data.get("earliest", cfg.earliest))
    for tf in cfg.timeframes:
        seconds(tf)

    mt5 = raw.get("mt5", {})
    cfg.terminal_path = mt5.get("terminal_path") or None

    tz = raw.get("timezone", {})
    cfg.server_timezone = tz.get("server", cfg.server_timezone)
    cfg.timezone_reference_symbol = tz.get("reference_symbol", cfg.timezone_reference_symbol)

    cfg.calendars.update(raw.get("calendars", {}))

    q = raw.get("quality", {})
    for k, v in q.items():
        if not hasattr(cfg.quality, k):
            raise ValueError(f"Unbekannte Einstellung [quality].{k}")
        setattr(cfg.quality, k, type(getattr(cfg.quality, k))(v))
    return cfg
