"""MT5-Serverzeit <-> UTC.

MT5-Semantik: Das Feld `time` von Kerzen und Ticks enthält Sekunden seit 1970 – aber
gemessen in der *Ortszeit des Handelsservers* (Wanduhrzeit des Servers, als wäre sie UTC).
Es ist also kein echter UTC-Zeitstempel. Auch Datumsangaben für copy_rates_range werden
in dieser Serverzeit interpretiert.

Der Abstand Serverzeit–UTC ist je Broker verschieden und ändert sich meist mit einer
Sommerzeitregel. Verbreitete Schemata:
    ny_close   Server = New Yorker Zeit + 7 h  (UTC+2 im Winter, UTC+3 während US-Sommerzeit)
    eu_dst     Server = osteuropäische Zeit     (UTC+2 / UTC+3 nach EU-Sommerzeitregel)
    fixed:+HH:MM  fester Abstand ohne Sommerzeit

Erkennung (detect_scheme): Gold hat eine tägliche Handelspause, die an New Yorker Zeit
gebunden ist (17:00–18:00 ET). Unter dem richtigen Schema liegt diese Pause – nach UTC
und dann nach New Yorker Zeit umgerechnet – das ganze Jahr zur gleichen Uhrzeit. Unter
einem falschen Schema springt sie bei den Sommerzeitwechseln. Zusätzlich muss das Schema
zum aktuell gemessenen Abstand (frischer Tick vs. PC-Uhr) passen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

NY = "America/New_York"


class TimeScheme:
    name: str

    def to_utc(self, server_epoch) -> pd.DatetimeIndex:
        raise NotImplementedError

    def to_server(self, utc) -> np.ndarray:
        raise NotImplementedError

    def offset_at(self, utc: pd.Timestamp) -> int:
        """Abstand Serverzeit - UTC in Sekunden zum Zeitpunkt utc."""
        ts = pd.DatetimeIndex([pd.Timestamp(utc).tz_convert("UTC") if pd.Timestamp(utc).tzinfo
                               else pd.Timestamp(utc, tz="UTC")])
        return int(self.to_server(ts)[0] - ts.as_unit("ns").asi8[0] // 10**9)

    def __repr__(self) -> str:
        return f"TimeScheme({self.name})"


class FixedScheme(TimeScheme):
    def __init__(self, offset_seconds: int):
        self.offset = int(offset_seconds)
        sign = "+" if self.offset >= 0 else "-"
        h, m = divmod(abs(self.offset) // 60, 60)
        self.name = f"fixed:{sign}{h:02d}:{m:02d}"

    def to_utc(self, server_epoch) -> pd.DatetimeIndex:
        arr = np.asarray(server_epoch, dtype="int64")
        return pd.DatetimeIndex(pd.to_datetime(arr - self.offset, unit="s", utc=True)).as_unit("ns")

    def to_server(self, utc) -> np.ndarray:
        idx = pd.DatetimeIndex(utc)
        return idx.as_unit("ns").asi8 // 10**9 + self.offset


class ZoneScheme(TimeScheme):
    """Server-Wanduhr = Wanduhr der Zeitzone `zone` + `shift_hours`."""

    def __init__(self, name: str, zone: str, shift_hours: int):
        self.name, self.zone, self.shift = name, zone, pd.Timedelta(hours=shift_hours)

    def to_utc(self, server_epoch) -> pd.DatetimeIndex:
        arr = np.asarray(server_epoch, dtype="int64")
        wall = pd.DatetimeIndex(pd.to_datetime(arr, unit="s")) - self.shift
        try:
            local = wall.tz_localize(self.zone, ambiguous="infer", nonexistent="shift_forward")
        except Exception:
            # Bei der Rückstellung auf Winterzeit ist eine Stunde Serverzeit doppelt belegt und
            # nicht eindeutig zuordenbar -> als Winterzeit interpretieren (dokumentierte Einschränkung).
            local = wall.tz_localize(self.zone, ambiguous=np.zeros(len(wall), dtype=bool),
                                     nonexistent="shift_forward")
        return local.tz_convert("UTC").as_unit("ns")

    def to_server(self, utc) -> np.ndarray:
        idx = pd.DatetimeIndex(utc)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        wall = idx.tz_convert(self.zone).tz_localize(None) + self.shift
        return wall.as_unit("ns").asi8 // 10**9


def parse_scheme(spec: str) -> TimeScheme:
    spec = spec.strip().lower()
    if spec == "ny_close":
        return ZoneScheme("ny_close", NY, 7)
    if spec == "eu_dst":
        return ZoneScheme("eu_dst", "Europe/Athens", 0)
    if spec.startswith("fixed:"):
        val = spec[6:]
        sign = -1 if val.startswith("-") else 1
        h, _, m = val.lstrip("+-").partition(":")
        return FixedScheme(sign * (int(h) * 3600 + int(m or 0) * 60))
    raise ValueError(f"Unbekanntes Zeitschema '{spec}' (erlaubt: ny_close, eu_dst, fixed:+HH:MM)")


def candidate_schemes() -> list[TimeScheme]:
    return [parse_scheme("ny_close"), parse_scheme("eu_dst")] + \
           [FixedScheme(h * 3600) for h in range(-1, 5)]


def estimate_live_offset(tick_server_time: int, utc_now: float, max_age_s: int = 120) -> int | None:
    """Aktuellen Abstand Serverzeit-UTC aus einem frischen Tick schätzen.

    Gibt den auf 15 Minuten gerundeten Abstand zurück – oder None, wenn der Tick zu alt ist
    (dann liegt die Differenz nicht nahe an einem 15-Minuten-Raster)."""
    diff = int(tick_server_time) - float(utc_now)
    rounded = int(round(diff / 900.0)) * 900
    if abs(diff - rounded) > max_age_s:
        return None
    return rounded


@dataclass
class Detection:
    scheme: TimeScheme
    method: str
    confidence: str  # "hoch" | "mittel" | "niedrig"
    events: int = 0
    break_hour_ny: float | None = None
    live_offset_s: int | None = None
    scores: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"scheme": self.scheme.name, "method": self.method, "confidence": self.confidence,
                "events": self.events, "break_hour_ny": self.break_hour_ny,
                "live_offset_hours": None if self.live_offset_s is None else self.live_offset_s / 3600,
                "scores": self.scores, "notes": self.notes}


def daily_break_events(h1_server_times) -> np.ndarray:
    """Serverzeit-Startpunkte von genau einer fehlenden H1-Kerze (= tägliche Handelspause)."""
    t = np.sort(np.asarray(h1_server_times, dtype="int64"))
    if len(t) < 2:
        return np.array([], dtype="int64")
    d = np.diff(t)
    idx = np.nonzero(d == 2 * 3600)[0]
    return t[idx] + 3600


def detect_scheme(h1_server_times, live_offset_s: int | None, now_utc: pd.Timestamp | None = None,
                  min_events: int = 20) -> Detection:
    now_utc = now_utc or pd.Timestamp.now(tz="UTC")
    events = daily_break_events(h1_server_times)
    cands = candidate_schemes()
    notes = []

    if live_offset_s is not None:
        matching = [c for c in cands if c.offset_at(now_utc) == live_offset_s]
        if not matching:
            fs = FixedScheme(live_offset_s)
            return Detection(fs, "live", "niedrig", len(events), None, live_offset_s, {},
                             ["Kein bekanntes Schema passt zum gemessenen Abstand – fester Abstand verwendet."])
        cands = matching
    else:
        notes.append("Kein frischer Tick: aktueller Abstand Serverzeit–UTC nicht messbar.")

    if len(events) < min_events:
        notes.append(f"Nur {len(events)} tägliche Pausen in den Referenzdaten – Sommerzeitregel nicht bestimmbar.")
        best = cands[0]
        return Detection(best, "live" if live_offset_s is not None else "default", "niedrig",
                         len(events), None, live_offset_s, {}, notes)

    scores = {}
    hours = {}
    for c in cands:
        ny = c.to_utc(events).tz_convert(NY)
        h = ny.hour + ny.minute / 60
        vals, counts = np.unique(np.round(h, 2), return_counts=True)
        scores[c.name] = round(float(counts.max() / counts.sum()), 4)
        hours[c.name] = float(vals[counts.argmax()])
    # Bestes Schema: höchste Konstanz; bei Gleichstand das mit Pause um 17:00 New York
    ranked = sorted(cands, key=lambda c: (-scores[c.name], abs(hours[c.name] - 17)))
    best = ranked[0]
    margin = scores[best.name] - (scores[ranked[1].name] if len(ranked) > 1 else 0)
    if scores[best.name] >= 0.97 and (live_offset_s is not None) and (margin > 0 or len(ranked) == 1):
        conf = "hoch"
    elif scores[best.name] >= 0.9:
        conf = "mittel"
    else:
        conf = "niedrig"
    if margin == 0 and len(ranked) > 1:
        notes.append(f"Gleichstand zwischen {best.name} und {ranked[1].name} – Daten decken keinen "
                     f"Zeitraum ab, in dem sich die Regeln unterscheiden.")
    if abs(hours[best.name] - 17) > 0.5:
        notes.append(f"Tägliche Pause liegt bei {hours[best.name]:.2f} Uhr New York statt 17:00 – Broker-Besonderheit.")
    return Detection(best, "goldpause+live" if live_offset_s is not None else "goldpause", conf,
                     len(events), hours[best.name], live_offset_s, scores, notes)
