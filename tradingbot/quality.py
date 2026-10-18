"""Automatische Qualitätsprüfung von Kerzendaten.

Die Prüfung verändert keine Daten – sie markiert nur. Kategorien:

Integrität     Duplikate, falsche Reihenfolge, fehlende Werte, Preise <= 0,
               ungültige OHLC (High < Low, Open/Close außerhalb High-Low), negativer Spread
Lücken         jede Lücke zwischen zwei Kerzen wird eingeordnet als
                 weekend         Wochenende laut Handelskalender; bei 24/7-Märkten: Broker hatte
                                 am Wochenende geschlossen (Fr 15:00 – So 24:00 New York)
                 daily_break     tägliche Handelspause laut Kalender
                 holiday         Feiertag / verkürzter Handel (US-Feiertage bei Gold,
                                 Weihnachten/Neujahr bei 24/7-Märkten)
                 low_activity    kurze Lücke (M1/M5) ohne Ticks – kein Datenfehler
                 recurring_pause wiederholt zur gleichen Serverzeit (z. B. Broker-Wartung)
                 data_gap        unerklärte Lücke = möglicher Datenfehler
Ausreißer      outlier         Rendite mit robustem z-Wert > outlier_z
               spike           Ausschlag mit sofortiger Gegenbewegung (typischer Fehltick)
               range_outlier   extreme Kerzenspanne (Docht) gegenüber dem gleitenden Median
               Renditen über eine Lücke hinweg (z. B. Wochenend-Gap) werden nicht bewertet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import QualityConfig
from .sessions import Calendar, open_seconds_between
from .timeframes import seconds

GAP_EXPECTED = ("weekend", "daily_break", "holiday", "low_activity", "recurring_pause")
HOLIDAY_MAX_OPEN_H = 36
NY = "America/New_York"


@dataclass
class QualityResult:
    symbol: str
    tf: str
    summary: dict
    gaps: pd.DataFrame
    anomalies: pd.DataFrame
    invalid: pd.DataFrame = field(default_factory=pd.DataFrame)

    def unexplained_gaps(self) -> pd.DataFrame:
        return self.gaps[self.gaps["category"] == "data_gap"] if not self.gaps.empty else self.gaps


# ---------------------------------------------------------------------- Integrität
def check_integrity(df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    nan = df[["open", "high", "low", "close"]].isna().any(axis=1).to_numpy()
    nonpos = (np.minimum.reduce([o, h, l, c]) <= 0) & ~nan
    bad_ohlc = ((h < l) | (o > h) | (o < l) | (c > h) | (c < l)) & ~nan
    neg_spread = df["spread"].to_numpy() < 0
    ts = df["time_server"].to_numpy()
    dup = pd.Series(ts).duplicated().to_numpy()
    nonmono = np.concatenate([[False], np.diff(ts) < 0])
    mask = nan | nonpos | bad_ohlc | neg_spread | dup | nonmono
    summary = {
        "duplicates": int(dup.sum()), "non_monotonic": int(nonmono.sum()), "nan_rows": int(nan.sum()),
        "nonpositive_prices": int(nonpos.sum()), "invalid_ohlc": int(bad_ohlc.sum()),
        "negative_spread": int(neg_spread.sum()),
        "zero_tick_volume": int((df["tick_volume"].to_numpy() == 0).sum()),
    }
    invalid = df.loc[mask].assign(
        reason=np.select([dup[mask], nonmono[mask], nan[mask], nonpos[mask], bad_ohlc[mask]],
                         ["Duplikat", "Reihenfolge", "fehlender Wert", "Preis <= 0", "ungültige OHLC"],
                         "negativer Spread"))
    return summary, invalid


# ---------------------------------------------------------------------- Lücken
def classify_gaps(df: pd.DataFrame, tf: str, calendar: Calendar, cfg: QualityConfig) -> pd.DataFrame:
    tf_ns = seconds(tf) * 10**9
    t = pd.DatetimeIndex(df["time"]).as_unit("ns").asi8
    if len(t) < 2:
        return pd.DataFrame(columns=["start", "end", "hours", "missing_bars", "open_hours", "category"])
    diff = np.diff(t)
    idx = np.nonzero(diff > tf_ns)[0]
    if len(idx) == 0:
        return pd.DataFrame(columns=["start", "end", "hours", "missing_bars", "open_hours", "category"])
    g_start = t[idx] + tf_ns
    g_end = t[idx + 1]
    starts, ends = calendar.open_intervals(pd.Timestamp(t[0], tz="UTC"), pd.Timestamp(t[-1], tz="UTC"))
    open_s = open_seconds_between(starts, ends, g_start, g_end)
    dur_s = (g_end - g_start) / 1e9

    tol = cfg.session_tolerance_min * 60
    cat = np.full(len(idx), "data_gap", dtype=object)
    # Bei durchgehend handelbaren Märkten (24/7) gibt es keine planmäßig geschlossenen Zeiten
    closed = np.zeros(len(idx), dtype=bool) if calendar.always_open else (open_s <= tol)
    cat[closed & (dur_s >= 24 * 3600)] = "weekend"
    cat[closed & (dur_s < 24 * 3600)] = "daily_break"

    hol = calendar.holidays(pd.Timestamp(t[0], tz="UTC"), pd.Timestamp(t[-1], tz="UTC"))
    if hol:
        d0 = calendar.local_dates(g_start)
        d1 = calendar.local_dates(g_end)
        hol_dates = np.array(sorted(hol), dtype="datetime64[D]")
        a = np.array(d0, dtype="datetime64[D]")
        b = np.array(d1, dtype="datetime64[D]")
        # Feiertag liegt zwischen Lückenbeginn und -ende (inklusive)
        k = np.searchsorted(hol_dates, a, side="left")
        has = (k < len(hol_dates)) & (hol_dates[np.minimum(k, len(hol_dates) - 1)] <= b)
        # Nur kurze Lücken (<= 36 h Handelszeit) können ein Feiertag sein. Eine mehrjährige Lücke "berührt"
        # zwangsläufig Feiertage – sie wurde früher fälschlich als Feiertag eingestuft (Gold-Intraday 2007–2017)
        cat[~closed & has & (open_s <= HOLIDAY_MAX_OPEN_H * 3600)] = "holiday"

    if calendar.always_open:
        # 24/7-Markt, aber Broker hat am Wochenende geschlossen (Fr ab 15:00 bis So 24:00 New York)
        ny0 = pd.DatetimeIndex(pd.to_datetime(g_start, utc=True)).tz_convert(NY)
        ny1 = pd.DatetimeIndex(pd.to_datetime(g_end, utc=True)).tz_convert(NY)
        fri = ny0.normalize() - pd.to_timedelta((ny0.weekday - 4) % 7, unit="D") + pd.Timedelta(hours=15)
        in_weekend = (ny0 >= fri) & (ny1 <= fri + pd.Timedelta(days=2, hours=10)) & (dur_s >= 6 * 3600)
        cat[(cat == "data_gap") & np.asarray(in_weekend)] = "weekend"

    if seconds(tf) <= 300:
        cat[(cat == "data_gap") & (open_s <= cfg.low_activity_max_min * 60)] = "low_activity"

    gaps = pd.DataFrame({
        "start": pd.to_datetime(g_start, utc=True), "end": pd.to_datetime(g_end, utc=True),
        "hours": np.round(dur_s / 3600, 3), "missing_bars": (diff[idx] // tf_ns - 1).astype("int64"),
        "open_hours": np.round(open_s / 3600, 3), "category": cat,
        "server_start": df["time_server"].to_numpy()[idx] + seconds(tf),
    })

    # Wiederkehrende Pausen: gleiche Wochentag/Uhrzeit (Serverzeit, 15-Minuten-Raster) und Dauer
    cand = gaps["category"] == "data_gap"
    if cand.any():
        ss = gaps.loc[cand, "server_start"].to_numpy()
        minute_of_week = ((ss // 60 + 3 * 24 * 60) % (7 * 24 * 60)) // 15  # 1970-01-01 war Donnerstag
        dur_q = np.round(gaps.loc[cand, "hours"].to_numpy() * 4)
        week = (ss + 3 * 86400) // (7 * 86400)
        key = pd.Series(list(zip(minute_of_week, dur_q)), index=gaps.index[cand])
        weeks_per_key = pd.Series(week, index=key.index).groupby(key).nunique()
        recurring_keys = set(weeks_per_key[weeks_per_key >= cfg.recurring_min_weeks].index)
        rec = key.map(lambda k: k in recurring_keys)
        gaps.loc[rec[rec].index, "category"] = "recurring_pause"
    return gaps.drop(columns=["server_start"])


# ---------------------------------------------------------------------- Ausreißer
def detect_anomalies(df: pd.DataFrame, tf: str, cfg: QualityConfig) -> pd.DataFrame:
    n = len(df)
    if n < 50:
        return pd.DataFrame(columns=["time", "kind", "value", "score", "close", "prev_close"])
    tf_ns = seconds(tf) * 10**9
    t = pd.DatetimeIndex(df["time"]).as_unit("ns").asi8
    close = df["close"].to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.diff(np.log(close), prepend=np.nan)
        rng = np.log(df["high"].to_numpy() / df["low"].to_numpy())
    across_gap = np.concatenate([[True], np.diff(t) > tf_ns])
    r_eval = np.where(across_gap, np.nan, r)

    minp = min(100, max(20, n // 10))
    # Skala der normalen Schwankung: robuster Median, aber mindestens der Mittelwert. In ruhigen
    # Phasen ist mehr als die Hälfte der Renditen 0 (Median = 0) – ohne Mittelwert würde dann jede
    # kleine Bewegung zum "Ausreißer" (beobachtet bei BTCUSD M1 im Juli 2020).
    abs_r = pd.Series(np.abs(r_eval))
    roll = abs_r.rolling(cfg.window, min_periods=minp, center=True)
    scale = np.fmax(roll.median().to_numpy() * 1.4826, roll.mean().to_numpy() * 1.2533)
    scale = np.maximum(np.nan_to_num(scale, nan=np.inf), 1e-6)
    z = np.nan_to_num(np.abs(r_eval) / scale, nan=0.0)

    rroll = pd.Series(rng).rolling(cfg.window, min_periods=minp, center=True)
    rng_base = np.fmax(rroll.median().to_numpy(), rroll.mean().to_numpy())
    rng_ratio = np.nan_to_num(rng / np.maximum(np.nan_to_num(rng_base, nan=np.inf), 1e-6), nan=0.0)

    out = []
    # Kursspitze mit sofortiger Rückkehr
    z_next = np.concatenate([z[1:], [0.0]])
    r_next = np.concatenate([r_eval[1:], [np.nan]])
    with np.errstate(invalid="ignore"):
        spike = (z > cfg.spike_z) & (z_next > cfg.spike_z) & (np.sign(r_eval) == -np.sign(r_next)) & \
                (np.abs(r_eval + r_next) < 0.3 * np.abs(r_eval))
    for i in np.nonzero(spike)[0]:
        out.append((i, "spike", r_eval[i], z[i]))
    spike_related = spike | np.concatenate([[False], spike[:-1]])
    for i in np.nonzero((z > cfg.outlier_z) & ~spike_related)[0]:
        out.append((i, "outlier", r_eval[i], z[i]))
    for i in np.nonzero(rng_ratio > cfg.range_factor)[0]:
        out.append((i, "range_outlier", rng[i], rng_ratio[i]))
    if not out:
        return pd.DataFrame(columns=["time", "kind", "value", "score", "close", "prev_close"])
    idx = np.array([o[0] for o in out])
    res = pd.DataFrame({
        # nur die betroffenen Zeilen auswählen – to_numpy() auf der ganzen Spalte würde bei
        # Zeitzonen-Daten Millionen Python-Objekte erzeugen (Speicherfehler bei ~10 Mio. Kerzen)
        "time": df["time"].iloc[idx].reset_index(drop=True), "kind": [o[1] for o in out],
        "value": np.round([o[2] for o in out], 6), "score": np.round([o[3] for o in out], 1),
        "close": np.round(close[idx], 6), "prev_close": np.round(close[np.maximum(idx - 1, 0)], 6),
    })
    return res.sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------- Konsistenz zwischen Zeitrahmen
def aggregate_from_m1(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    """M1 in Serverzeit zu einem höheren Zeitrahmen zusammenfassen (wie MT5)."""
    tf_s = seconds(tf)
    key = m1["time_server"].to_numpy() // tf_s * tf_s
    g = m1.groupby(key)
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                         "low": g["low"].min(), "close": g["close"].last()})


def consistency_with_m1(m1: pd.DataFrame, other: pd.DataFrame, tf: str, rel_tol: float = 1e-6) -> dict | None:
    if m1.empty or other.empty:
        return None
    lo, hi = m1["time_server"].min(), m1["time_server"].max()
    tf_s = seconds(tf)
    sub = other[(other["time_server"] >= lo // tf_s * tf_s + tf_s) & (other["time_server"] + tf_s <= hi)]
    if sub.empty:
        return None
    agg = aggregate_from_m1(m1, tf)
    sub = sub.set_index("time_server")
    common = sub.index.intersection(agg.index)
    if len(common) == 0:
        return {"compared": 0}
    a, b = agg.loc[common], sub.loc[common, ["open", "high", "low", "close"]]
    close_ok = np.isclose(a["close"], b["close"], rtol=rel_tol)
    hl_ok = np.isclose(a["high"], b["high"], rtol=rel_tol) & np.isclose(a["low"], b["low"], rtol=rel_tol)
    return {"compared": int(len(common)), "missing_in_m1": int(len(sub) - len(common)),
            "close_match_pct": round(100 * close_ok.mean(), 2), "high_low_match_pct": round(100 * hl_ok.mean(), 2)}


def continuous_start(df: pd.DataFrame, max_gap_days: float = 14.0) -> pd.Timestamp:
    """Beginn der lückenlosen Historie: erste Kerze nach der letzten Lücke von mehr als max_gap_days
    Kalendertagen (Feiertage und Wochenenden sind deutlich kürzer)."""
    t = pd.DatetimeIndex(df["time"])
    if len(t) < 2:
        return t[0] if len(t) else pd.Timestamp("NaT")
    gaps = np.nonzero(np.diff(t.as_unit("ns").asi8) > max_gap_days * 86400 * 10**9)[0]
    return t[gaps[-1] + 1] if len(gaps) else t[0]


# ---------------------------------------------------------------------- Gesamtprüfung
def check_bars(df: pd.DataFrame, symbol: str, tf: str, calendar: Calendar, cfg: QualityConfig) -> QualityResult:
    if df.empty:
        return QualityResult(symbol, tf, {"rows": 0}, pd.DataFrame(), pd.DataFrame())
    df = df.sort_values("time_server").reset_index(drop=True)
    integ, invalid = check_integrity(df)
    clean = df.drop_duplicates("time_server")
    gaps = classify_gaps(clean, tf, calendar, cfg)
    anomalies = detect_anomalies(clean, tf, cfg)

    first, last = df["time"].iloc[0], df["time"].iloc[-1]
    tf_s = seconds(tf)
    starts, ends = calendar.open_intervals(first, last)
    open_total = float(open_seconds_between(starts, ends, np.array([first.value]),
                                            np.array([last.value + tf_s * 10**9]))[0])
    gap_counts = gaps["category"].value_counts().to_dict() if not gaps.empty else {}
    gap_hours = gaps.groupby("category")["hours"].sum().round(1).to_dict() if not gaps.empty else {}
    data_gap_open_h = float(gaps.loc[gaps["category"] == "data_gap", "open_hours"].sum()) if not gaps.empty else 0.0
    summary = {
        "rows": int(len(df)),
        "first_utc": first.isoformat(), "last_utc": last.isoformat(),
        "span_days": round((last - first).total_seconds() / 86400, 1),
        **integ,
        "gaps": {k: int(v) for k, v in gap_counts.items()},
        "gap_hours": gap_hours,
        "data_gap_open_hours": round(data_gap_open_h, 2),
        # Anteil der Handelszeit, der nicht durch unerklärte Lücken verloren ging
        "coverage_pct": round(100 * (1 - data_gap_open_h * 3600 / open_total), 3) if open_total > 0 else None,
        "anomalies": anomalies["kind"].value_counts().to_dict() if not anomalies.empty else {},
        # Datendichte: vorhandene Kerzen gegenüber der laut Kalender möglichen Anzahl
        "expected_bars": int(open_total // tf_s),
        "bar_density_pct": round(100 * len(df) / max(1, open_total // tf_s), 1),
        "largest_gap_days": round(float(gaps["hours"].max()) / 24, 1) if not gaps.empty else 0.0,
        "continuous_from_utc": continuous_start(df).isoformat(),
        "spread_median_points": float(np.median(df["spread"])),
        "spread_p95_points": float(np.percentile(df["spread"], 95)),
    }
    return QualityResult(symbol, tf, summary, gaps, anomalies, invalid)
