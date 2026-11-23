"""Einstellungen der Day-Trader-Skills aus daytrader.toml (mit Standardwerten)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from ..timeframes import seconds


def _minutes(hhmm: str) -> int:
    h, _, m = str(hhmm).partition(":")
    v = int(h) * 60 + int(m or 0)
    if not 0 <= v < 24 * 60:
        raise ValueError(f"Ungültige Uhrzeit '{hhmm}' (erwartet HH:MM)")
    return v


@dataclass
class Session:
    name: str
    tz: str
    start: int            # Minuten nach Mitternacht (Ortszeit)
    range_min: int
    entry_until: int
    flat_by: int

    @property
    def range_end(self) -> int:
        return self.start + self.range_min

    def hhmm(self, minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"


DEFAULT_SESSIONS = {
    "XAUUSD": Session("COMEX-Eröffnung", "America/New_York", _minutes("08:20"), 15, _minutes("11:30"), _minutes("16:30")),
    "BTCUSD": Session("US-Börseneröffnung", "America/New_York", _minutes("09:30"), 15, _minutes("12:00"), _minutes("16:00")),
}


@dataclass
class RiskLimits:
    risk_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_trades_per_day: int = 3
    max_positions: int = 1
    min_rr: float = 1.0
    max_plan_age_min: int = 30
    max_drift_r: float = 0.25
    approval_valid_min: int = 10
    deviation_points: int = 100


RISK_KEYS = {"risiko_je_trade_pct": "risk_pct", "max_tagesverlust_pct": "max_daily_loss_pct",
             "max_trades_pro_tag": "max_trades_per_day", "max_positionen": "max_positions", "min_crv": "min_rr",
             "max_plan_alter_min": "max_plan_age_min", "max_kursabweichung_r": "max_drift_r",
             "freigabe_gueltig_min": "approval_valid_min", "abweichung_points": "deviation_points"}


@dataclass
class DayTraderConfig:
    symbols: list[str] = field(default_factory=lambda: ["XAUUSD", "BTCUSD"])
    timeframe: str = "M5"
    cost_scenario: str = "konservativ"
    risk: RiskLimits = field(default_factory=RiskLimits)
    sessions: dict[str, Session] = field(default_factory=lambda: dict(DEFAULT_SESSIONS))

    def session(self, symbol: str) -> Session:
        try:
            return self.sessions[symbol]
        except KeyError:
            raise ValueError(f"Für {symbol} ist keine Sitzung eingetragen – in daytrader.toml "
                             f"[sitzungen.{symbol}] ergänzen") from None


def load_daytrader_config(path: str | Path | None = None) -> DayTraderConfig:
    cfg = DayTraderConfig()
    if path is None or not Path(path).exists():
        return cfg
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(raw) - {"symbole", "zeitrahmen", "kostenszenario", "risiko", "sitzungen"}
    if unknown:
        raise ValueError(f"Unbekannte Einstellung in daytrader.toml: {sorted(unknown)[0]}")
    cfg.symbols = [str(s) for s in raw.get("symbole", cfg.symbols)]
    cfg.timeframe = str(raw.get("zeitrahmen", cfg.timeframe))
    if seconds(cfg.timeframe) > 3600:
        raise ValueError("zeitrahmen: höchstens H1 (Day-Trading)")
    cfg.cost_scenario = str(raw.get("kostenszenario", cfg.cost_scenario))
    for k, v in raw.get("risiko", {}).items():
        if k not in RISK_KEYS:
            raise ValueError(f"Unbekannte Einstellung [risiko].{k}")
        attr = RISK_KEYS[k]
        setattr(cfg.risk, attr, type(getattr(cfg.risk, attr))(v))
    if not 0 < cfg.risk.risk_pct <= 5:
        raise ValueError("[risiko].risiko_je_trade_pct muss zwischen 0 und 5 liegen")
    for sym, s in raw.get("sitzungen", {}).items():
        ZoneInfo(s["zeitzone"])                               # unbekannte Zeitzone -> Fehler
        sess = Session(str(s.get("name", sym)), s["zeitzone"], _minutes(s["start"]), int(s.get("range_min", 15)),
                       _minutes(s["einstieg_bis"]), _minutes(s["glattstellen"]))
        if not sess.start < sess.range_end <= sess.entry_until < sess.flat_by:
            raise ValueError(f"[sitzungen.{sym}]: es muss gelten start < start+range_min <= einstieg_bis < glattstellen")
        cfg.sessions[sym] = sess
    return cfg
