"""Kursdaten, Uhrzeit und Ablage für die Day-Trader-Skills.

Kerzen kommen aus der lokalen Datenbank (data/market, von daten_aktualisieren.py) und – wenn das MT5-Terminal
offen ist – zusätzlich live aus MT5 (nur lesend, MT5Client). Verwendet werden nur abgeschlossene Kerzen.
Ablage unter <data>/daytrader/: scan.json, plan.json, zeitmaschine.json, ticket.json, protokoll.jsonl.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Config
from ..server_time import parse_scheme
from ..storage import MarketStore, rates_to_frame
from ..timeframes import seconds
from .config import DayTraderConfig, Session

BERLIN = "Europe/Berlin"
LOG = "protokoll.jsonl"


@dataclass
class Context:
    cfg: Config
    dt: DayTraderConfig
    client: object | None = None          # MT5Client oder None (dann nur Datenbank)
    now_utc: pd.Timestamp | None = None   # feste Uhrzeit (Tests, --jetzt)

    def __post_init__(self):
        self.market = MarketStore(self.cfg.data_root)
        self.dir = Path(self.cfg.data_root) / "daytrader"
        self.doc = self.market.read_json("specs.json", {}) or {}
        ts = (self.doc.get("time_scheme") or {}).get("scheme")
        self.scheme = parse_scheme(ts) if ts else None

    # ------------------------------------------------------------------ Uhrzeit
    def now(self) -> pd.Timestamp:
        return self.now_utc if self.now_utc is not None else pd.Timestamp.now(tz="UTC")

    @property
    def live(self) -> bool:
        return self.client is not None

    # ------------------------------------------------------------------ Ablage
    def write(self, name: str, obj) -> None:
        self.market.write_json(self.dir / name, obj)

    def read(self, name: str, default=None):
        return self.market.read_json(self.dir / name, default)

    def log(self, kind: str, **data) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        ev = {"utc": self.now().isoformat(), "art": kind, **data}
        with open(self.dir / LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        return ev

    def events(self) -> list[dict]:
        p = self.dir / LOG
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ------------------------------------------------------------------ Kerzen
    def bars(self, symbol: str, tf: str | None = None, days: int = 70) -> pd.DataFrame:
        """Abgeschlossene Kerzen der letzten `days` Tage: time (UTC), open, high, low, close, spread."""
        tf = tf or self.dt.timeframe
        tf_s = seconds(tf)
        now = self.now()
        start = now - pd.Timedelta(days=days)
        parts = []
        df = self.market.load(symbol, tf, start=start, columns=["open", "high", "low", "close", "spread"])
        if not df.empty:
            parts.append(df)
        if self.client is not None and self.scheme is not None:
            count = min(50_000, int(days * 86400 / tf_s) + 10)
            r = self.client.rates_from_pos(symbol, tf, 1, count)        # ab 1: laufende Kerze ausgelassen
            if r is not None and len(r):
                parts.append(rates_to_frame(r, self.scheme)[["time", "time_server", "open", "high", "low",
                                                             "close", "spread"]])
        if not parts:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "spread"])
        df = pd.concat(parts, ignore_index=True)
        df["time"] = pd.DatetimeIndex(df["time"]).as_unit("ns")
        df = df.drop_duplicates("time", keep="last").sort_values("time")
        end = pd.DatetimeIndex(df["time"]) + pd.Timedelta(seconds=tf_s)
        df = df[(end <= now) & (pd.DatetimeIndex(df["time"]) >= start)]
        return df.reset_index(drop=True)[["time", "open", "high", "low", "close", "spread"]]

    def spec(self, symbol: str) -> dict:
        """Kontraktdaten: live aus MT5, sonst aus data/specs.json."""
        if self.client is not None:
            info = self.client.symbol_info(symbol)
            if info:
                from ..instruments import InstrumentSpec
                d = InstrumentSpec.from_mt5(info).to_dict()
                stored = (self.doc.get("instruments") or {}).get(symbol) or {}
                d["margin_rate"] = float(stored.get("margin_rate", 1.0))
                return d
        d = (self.doc.get("instruments") or {}).get(symbol)
        if not d:
            raise RuntimeError(f"Keine Kontraktdaten für {symbol}: MT5 öffnen oder daten_aktualisieren.py ausführen")
        return d

    def tick(self, symbol: str) -> dict | None:
        if self.client is None:
            return None
        t = self.client.tick(symbol)
        return t if t and t.get("bid") else None


# ---------------------------------------------------------------------- Sitzungen
def local_frame(bars: pd.DataFrame, sess: Session) -> pd.DataFrame:
    """Kerzen mit Ortszeit der Sitzung: day (Datum), mins (Minuten nach Mitternacht)."""
    t = pd.DatetimeIndex(bars["time"]).tz_convert(sess.tz)
    out = bars.copy()
    out["day"] = t.tz_localize(None).normalize()
    out["mins"] = (t.hour * 60 + t.minute).to_numpy()
    return out


def session_day(now_utc: pd.Timestamp, sess: Session) -> tuple[pd.Timestamp, int]:
    loc = pd.Timestamp(now_utc).tz_convert(sess.tz)
    return loc.tz_localize(None).normalize(), loc.hour * 60 + loc.minute


def opening_range_of(day_bars: pd.DataFrame, sess: Session, tf_min: int) -> tuple[float, float] | None:
    """Hoch/Tief der Kerzen im Fenster [start, start+range_min) – nur wenn alle Kerzen vorhanden sind."""
    rng = day_bars[(day_bars["mins"] >= sess.start) & (day_bars["mins"] + tf_min <= sess.range_end)]
    if tf_min <= 0 or sess.range_min % tf_min or len(rng) != sess.range_min // tf_min:
        return None
    return float(rng["high"].max()), float(rng["low"].min())


def fmt_local(ts_utc, tz: str = BERLIN) -> str:
    return pd.Timestamp(ts_utc).tz_convert(tz).strftime("%d.%m.%Y %H:%M")


def parse_now(text: str | None) -> pd.Timestamp | None:
    """--jetzt "2026-11-23 15:45" = deutsche Zeit (Europe/Berlin), außer mit Zeitzone angegeben."""
    if not text:
        return None
    ts = pd.Timestamp(text)
    return (ts.tz_localize(BERLIN) if ts.tzinfo is None else ts).tz_convert("UTC")


def round_price(x: float, digits: int) -> float:
    return float(np.round(x, digits))
