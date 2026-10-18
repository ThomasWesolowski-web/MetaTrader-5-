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
class PaperCandidate:
    """Ein beobachteter Parametersatz (Paper-Trading, Phase 4) – Parameter unverändert aus Phase 3."""
    symbol: str
    timeframe: str
    strategy: str
    params: dict

    @property
    def key(self) -> str:
        return f"{self.symbol}_{self.timeframe}_{self.strategy}"


def _default_candidates() -> list[PaperCandidate]:
    # Forschungskandidat "Bitcoin-Trendfolge auf M30/H1" aus Phase 3 (docs/phase3_ergebnis.md)
    return [PaperCandidate("BTCUSD", "H1", "kombi_breakout_momentum", {"n": 55, "rr": 3.0}),
            PaperCandidate("BTCUSD", "H1", "donchian_breakout", {"n": 55, "atr_mult": 2.5}),
            PaperCandidate("BTCUSD", "H1", "ema_trend", {"fast": 50, "slow": 200, "atr_mult": 3.0}),
            PaperCandidate("BTCUSD", "M30", "ema_trend", {"fast": 50, "slow": 200, "atr_mult": 3.0})]


@dataclass
class PaperConfig:
    candidates: list[PaperCandidate] = field(default_factory=_default_candidates)
    interval_s: int = 60            # Sekunden zwischen zwei Durchläufen
    warmup_bars: int = 3000         # Kerzen vor Beobachtungsbeginn für die Indikatoren (EMA 200 eingeschwungen)
    stale_after_s: int = 300        # Tick älter als das: keine aktuellen Kurse (Markt geschlossen/keine Verbindung)
    max_delay_s: int = 300          # Signal später als das nach Kerzenschluss erkannt: nicht gehandelt ("verspätet")
    cost_scenario: str = "konservativ"   # wie die Auswahl in Phase 3


@dataclass
class Config:
    data_root: Path = Path("data")
    report_dir: Path = Path("reports")
    symbols: list[str] = field(default_factory=lambda: ["XAUUSD", "BTCUSD"])
    timeframes: list[str] = field(default_factory=lambda: ["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    # Umrechnungssymbole (z. B. EURUSD) brauchen keine M1-Daten: M5 genügt für die EUR-Umrechnung
    conversion_timeframes: list[str] = field(default_factory=lambda: ["M5", "H1", "D1"])
    # Handelskalender je Symbol (siehe sessions.CALENDARS)
    calendars: dict[str, str] = field(default_factory=lambda: {"XAUUSD": "cme_metals", "BTCUSD": "24x7"})
    terminal_path: str | None = None
    chunk_bars: int = 40_000
    earliest: str = "2000-01-01"
    # "auto" oder fest: "ny_close", "eu_dst", "fixed:+03:00"
    server_timezone: str = "auto"
    timezone_reference_symbol: str = "XAUUSD"
    quality: QualityConfig = field(default_factory=QualityConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)

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
    cfg.conversion_timeframes = list(data.get("conversion_timeframes", cfg.conversion_timeframes))
    cfg.chunk_bars = int(data.get("chunk_bars", cfg.chunk_bars))
    cfg.earliest = str(data.get("earliest", cfg.earliest))
    for tf in cfg.timeframes + cfg.conversion_timeframes:
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

    pp = raw.get("paper", {})
    if "candidates" in pp:
        cfg.paper.candidates = [PaperCandidate(str(c["symbol"]), str(c["timeframe"]), str(c["strategy"]),
                                               dict(c.get("params", {}))) for c in pp["candidates"]]
    for k in ("interval_s", "warmup_bars", "stale_after_s", "max_delay_s", "cost_scenario"):
        if k in pp:
            setattr(cfg.paper, k, type(getattr(cfg.paper, k))(pp[k]))
    unknown = set(pp) - {"candidates", "interval_s", "warmup_bars", "stale_after_s", "max_delay_s", "cost_scenario"}
    if unknown:
        raise ValueError(f"Unbekannte Einstellung [paper].{sorted(unknown)[0]}")
    for c in cfg.paper.candidates:
        seconds(c.timeframe)
    return cfg
