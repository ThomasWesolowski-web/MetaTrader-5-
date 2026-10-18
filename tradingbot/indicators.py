"""Indikatoren und Marktstruktur – ohne Blick in die Zukunft.

Regel für alle Funktionen: Der Wert an Position i darf nur Kerzen 0..i verwenden.
Strukturpunkte (Swing-Hochs/-Tiefs) brauchen Bestätigung durch k Folgekerzen und werden
deshalb erst an Position i+k sichtbar ("bestätigt"). Ein Test prüft für jede Strategie, dass
Signale auf gekürzten Daten identisch sind (tests/test_no_lookahead.py).

Alle Funktionen arbeiten auf numpy-Arrays bzw. pandas-Series und sind vektorisiert.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(x).ewm(span=period, adjust=False, min_periods=period).mean().to_numpy()


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range nach Wilder."""
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev), np.abs(low - prev)))
    tr[0] = high[0] - low[0]
    return pd.Series(tr).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy()


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index nach Wilder (0..100)."""
    d = np.diff(close, prepend=np.nan)
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up.to_numpy() / dn.to_numpy()
        out = 100 - 100 / (1 + rs)
    out[(dn.to_numpy() == 0) & (up.to_numpy() > 0)] = 100.0
    return out


def rolling_max_prev(x: np.ndarray, n: int) -> np.ndarray:
    """Maximum der n Kerzen VOR i (ohne Kerze i)."""
    return pd.Series(x).shift(1).rolling(n, min_periods=n).max().to_numpy()


def rolling_min_prev(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).shift(1).rolling(n, min_periods=n).min().to_numpy()


# ---------------------------------------------------------------------- Swing-Punkte (Fraktale)
def swing_points(high: np.ndarray, low: np.ndarray, k: int):
    """Bestätigte Swing-Hochs/-Tiefs.

    Ein Swing-Hoch an Kerze j liegt vor, wenn high[j] das Maximum von j-k..j+k ist. Bekannt ist
    das erst an Kerze j+k. Rückgabe (alle Arrays mit Länge n, Wert an Position i = Stand bei i):
        sh_price, sl_price : Preis des zuletzt bestätigten Swing-Hochs/-Tiefs (NaN, wenn keins)
        sh_new, sl_new     : True an der Kerze, an der ein neues Swing-Hoch/-Tief bestätigt wird
    """
    h = pd.Series(high)
    l = pd.Series(low)
    win = 2 * k + 1
    is_sh = h.eq(h.rolling(win, center=True, min_periods=win).max())
    is_sl = l.eq(l.rolling(win, center=True, min_periods=win).min())
    sh_new = is_sh.shift(k, fill_value=False).to_numpy(dtype=bool)
    sl_new = is_sl.shift(k, fill_value=False).to_numpy(dtype=bool)
    sh_price = h.shift(k).where(sh_new).ffill().to_numpy()
    sl_price = l.shift(k).where(sl_new).ffill().to_numpy()
    return sh_price, sl_price, sh_new, sl_new


def market_structure(close: np.ndarray, sh_price: np.ndarray, sl_price: np.ndarray):
    """Break of Structure (BOS) und Market Structure Shift (MSS).

    bos_up[i]   Schlusskurs bricht erstmals über das zuletzt bestätigte Swing-Hoch
    bos_dn[i]   Schlusskurs bricht erstmals unter das zuletzt bestätigte Swing-Tief
    trend[i]    Richtung des letzten BOS (+1/-1, 0 = noch keiner)
    mss_up[i]   bos_up, während der Trend vorher abwärts war (Strukturwechsel)
    mss_dn[i]   bos_dn, während der Trend vorher aufwärts war
    Jedes Swing-Niveau kann nur einmal gebrochen werden (erste Schlusskurs-Überschreitung).
    """
    c = pd.Series(close)
    above = (c > pd.Series(sh_price)).to_numpy()
    below = (c < pd.Series(sl_price)).to_numpy()
    # "erstmals" je Swing-Niveau: Gruppen nach Niveau, erste True-Stelle je Gruppe
    sh_id = pd.Series(sh_price).ne(pd.Series(sh_price).shift()).cumsum().to_numpy()
    sl_id = pd.Series(sl_price).ne(pd.Series(sl_price).shift()).cumsum().to_numpy()
    bos_up = above & (pd.Series(above).groupby(sh_id).cumsum().to_numpy() == 1)
    bos_dn = below & (pd.Series(below).groupby(sl_id).cumsum().to_numpy() == 1)
    ev = np.where(bos_up, 1.0, np.where(bos_dn, -1.0, np.nan))
    trend = pd.Series(ev).ffill().fillna(0).to_numpy()
    prev_trend = np.concatenate([[0.0], trend[:-1]])
    mss_up = bos_up & (prev_trend == -1)
    mss_dn = bos_dn & (prev_trend == 1)
    return bos_up, bos_dn, trend.astype(int), mss_up, mss_dn


# ---------------------------------------------------------------------- Fair Value Gaps
def fair_value_gaps(high: np.ndarray, low: np.ndarray, atr_arr: np.ndarray, min_atr: float = 0.0):
    """3-Kerzen-Lücken, bekannt am Schluss der dritten Kerze i.

    bullisch: low[i] > high[i-2]  -> Zone [high[i-2], low[i]]
    bärisch : high[i] < low[i-2]  -> Zone [high[i], low[i-2]]
    Rückgabe: bull (bool), bear (bool), bottom, top (Zonen an den Formationskerzen, sonst NaN)
    """
    h2 = np.concatenate([[np.nan, np.nan], high[:-2]])
    l2 = np.concatenate([[np.nan, np.nan], low[:-2]])
    size_ok_bull = (low - h2) >= min_atr * atr_arr
    size_ok_bear = (l2 - high) >= min_atr * atr_arr
    with np.errstate(invalid="ignore"):
        bull = (low > h2) & size_ok_bull
        bear = (high < l2) & size_ok_bear
    bottom = np.where(bull, h2, np.where(bear, high, np.nan))
    top = np.where(bull, low, np.where(bear, l2, np.nan))
    return bull, bear, bottom, top


def first_touch_after(events: np.ndarray, zone_lo: np.ndarray, zone_hi: np.ndarray, direction: int,
                      high: np.ndarray, low: np.ndarray, close: np.ndarray, max_wait: int) -> list[tuple[int, int]]:
    """Für jedes Ereignis e (Zone bekannt am Schluss von e): erste Kerze j in e+1..e+max_wait,
    deren Kurs in die Zone zurückkehrt, ohne sie per Schlusskurs zu durchbrechen.

    long  (direction=+1): low[j] <= zone_hi und close[j] >= zone_lo; ungültig, wenn vorher close < zone_lo
    short (direction=-1): high[j] >= zone_lo und close[j] <= zone_hi; ungültig, wenn vorher close > zone_hi
    Rückgabe: Liste (ereignis, einstiegssignal_kerze)."""
    out = []
    n = len(close)
    for e in events:
        lo, hi = zone_lo[e], zone_hi[e]
        if not (np.isfinite(lo) and np.isfinite(hi)):
            continue
        a, b = e + 1, min(n, e + 1 + max_wait)
        if a >= b:
            continue
        if direction == 1:
            broken = close[a:b] < lo
            touch = (low[a:b] <= hi) & (close[a:b] >= lo)
        else:
            broken = close[a:b] > hi
            touch = (high[a:b] >= lo) & (close[a:b] <= hi)
        tb = np.argmax(touch) if touch.any() else -1
        bb = np.argmax(broken) if broken.any() else -1
        if tb >= 0 and (bb < 0 or tb < bb):
            out.append((int(e), int(a + tb)))
    return out


def in_session(time_utc: pd.DatetimeIndex, windows_ny: list[tuple[int, int]]) -> np.ndarray:
    """True, wenn die Kerze in einem der Zeitfenster (Stunde von, bis – New Yorker Zeit) beginnt."""
    h = pd.DatetimeIndex(time_utc).tz_convert("America/New_York").hour.to_numpy()
    m = np.zeros(len(h), dtype=bool)
    for a, b in windows_ny:
        m |= (h >= a) & (h < b)
    return m
