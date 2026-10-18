"""Handelszeiten je Instrument.

Die MT5-Python-API liefert keine Handelssitzungen (SymbolInfoSessionTrade fehlt). Daher
werden die Handelszeiten als Kalender modelliert und bei der Lückenprüfung mit einer
Toleranz für Broker-Abweichungen verwendet.

    cme_metals  Gold/Silber nach CME-Globex-Zeiten in New Yorker Ortszeit:
                So 18:00 – Fr 17:00, tägliche Pause Mo–Do 17:00–18:00.
                US-Feiertage (geschlossen oder verkürzt) werden als Feiertagsdaten geführt.
    fx_24x5     Devisen: So 17:00 – Fr 17:00 New York (für Umrechnungssymbole wie EURUSD)
    24x7        durchgehend handelbar (Bitcoin-CFDs bei den meisten Brokern)
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

NY = "America/New_York"


def _easter(year: int) -> dt.date:
    """Ostersonntag (gregorianisch, anonymer Algorithmus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def us_market_holidays(year: int) -> dict[dt.date, str]:
    """US-Feiertage mit geschlossenem oder verkürztem Metallhandel (New Yorker Datum)."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    h = {
        _observed(dt.date(year, 1, 1)): "Neujahr",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Day",
        _nth_weekday(year, 2, 0, 3): "Presidents Day",
        _easter(year) - dt.timedelta(days=2): "Karfreitag",
        _last_weekday(year, 5, 0): "Memorial Day",
        _observed(dt.date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        thanksgiving: "Thanksgiving",
        thanksgiving + dt.timedelta(days=1): "Tag nach Thanksgiving",
        dt.date(year, 12, 24): "Heiligabend",
        _observed(dt.date(year, 12, 25)): "Weihnachten",
        dt.date(year, 12, 31): "Silvester",
    }
    if year >= 2022:
        h[_observed(dt.date(year, 6, 19))] = "Juneteenth"
    return h


class Calendar:
    name = "base"
    always_open = False

    def open_intervals(self, start: pd.Timestamp, end: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
        """Sortierte, überlappungsfreie Handelsintervalle [start, ende) als UTC-Nanosekunden."""
        raise NotImplementedError

    def holidays(self, start: pd.Timestamp, end: pd.Timestamp) -> dict[dt.date, str]:
        return {}

    def local_dates(self, utc_ns: np.ndarray) -> np.ndarray:
        """Datum im Referenzkalender (für Feiertagsabgleich)."""
        return pd.DatetimeIndex(pd.to_datetime(utc_ns, utc=True)).date


class AlwaysOpen(Calendar):
    """Durchgehend handelbar. Broker schließen Krypto-CFDs trotzdem teils an Weihnachten/Neujahr
    und (früher) am Wochenende – das wird in quality.classify_gaps gesondert eingeordnet."""
    name = "24x7"
    always_open = True

    def open_intervals(self, start, end):
        return (np.array([pd.Timestamp(start).value - 1], dtype="int64"),
                np.array([pd.Timestamp(end).value + 1], dtype="int64"))

    def holidays(self, start, end):
        out = {}
        for y in range(pd.Timestamp(start).year - 1, pd.Timestamp(end).year + 2):
            out[dt.date(y, 12, 25)] = "Weihnachten"
            out[dt.date(y, 1, 1)] = "Neujahr"
        return out


class CmeMetals(Calendar):
    name = "cme_metals"

    def open_intervals(self, start, end):
        s = pd.Timestamp(start).tz_convert(NY).normalize().tz_localize(None) - pd.Timedelta(days=3)
        e = pd.Timestamp(end).tz_convert(NY).normalize().tz_localize(None) + pd.Timedelta(days=3)
        days = pd.date_range(s, e, freq="D")
        days = days[days.weekday < 5]  # Handelstage Mo–Fr
        # Handelstag D läuft von (D-1) 18:00 bis D 17:00 New Yorker Zeit (Montag: ab Sonntag 18:00)
        opens = (days - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).tz_localize(NY).tz_convert("UTC")
        closes = (days + pd.Timedelta(hours=17)).tz_localize(NY).tz_convert("UTC")
        return opens.as_unit("ns").asi8.astype("int64"), closes.as_unit("ns").asi8.astype("int64")

    def holidays(self, start, end):
        out = {}
        for y in range(pd.Timestamp(start).year - 1, pd.Timestamp(end).year + 2):
            out.update(us_market_holidays(y))
        return out

    def local_dates(self, utc_ns):
        return pd.DatetimeIndex(pd.to_datetime(utc_ns, utc=True)).tz_convert(NY).date


class Fx24x5(CmeMetals):
    """Devisen (z. B. EURUSD als Umrechnungssymbol): So 17:00 – Fr 17:00 New York, ohne Tagespause."""
    name = "fx_24x5"

    def open_intervals(self, start, end):
        s = pd.Timestamp(start).tz_convert(NY).normalize().tz_localize(None) - pd.Timedelta(days=7)
        e = pd.Timestamp(end).tz_convert(NY).normalize().tz_localize(None) + pd.Timedelta(days=7)
        sundays = pd.date_range(s, e, freq="W-SUN")
        opens = (sundays + pd.Timedelta(hours=17)).tz_localize(NY).tz_convert("UTC")
        closes = (sundays + pd.Timedelta(days=5, hours=17)).tz_localize(NY).tz_convert("UTC")
        return opens.as_unit("ns").asi8.astype("int64"), closes.as_unit("ns").asi8.astype("int64")


CALENDARS = {"24x7": AlwaysOpen, "cme_metals": CmeMetals, "fx_24x5": Fx24x5}


def get_calendar(name: str) -> Calendar:
    try:
        return CALENDARS[name]()
    except KeyError:
        raise ValueError(f"Unbekannter Handelskalender '{name}' (erlaubt: {', '.join(CALENDARS)})") from None


def open_seconds_between(starts: np.ndarray, ends: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Handelszeit (Sekunden) innerhalb der Intervalle [a, b) – vektorisiert über kumulierte Summen."""
    starts = np.asarray(starts, dtype="int64")
    ends = np.asarray(ends, dtype="int64")
    dur = ends - starts
    cum = np.concatenate([[0], np.cumsum(dur)])

    def F(t):
        t = np.asarray(t, dtype="int64")
        k = np.searchsorted(ends, t, side="right")  # Intervalle, die vor t vollständig enden
        partial = np.zeros_like(t)
        inside = k < len(starts)
        kk = np.minimum(k, len(starts) - 1)
        partial[inside] = np.clip(t[inside] - starts[kk[inside]], 0, dur[kk[inside]])
        return cum[k] + partial

    return (F(b) - F(a)) / 1e9
