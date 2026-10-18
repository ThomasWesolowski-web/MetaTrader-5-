import numpy as np
import pytest

from tradingbot import indicators as ind


def test_swing_point_confirmed_only_after_k_bars():
    h = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3], float)
    l = h - 0.5
    sh, sl, sh_new, sl_new = ind.swing_points(h, l, 2)
    assert not sh_new[3] and not sh_new[4]       # an der Spitze selbst noch unbekannt
    assert sh_new[5] and sh[5] == 9              # bestätigt 2 Kerzen später
    assert np.isnan(sh[4])


def test_bos_and_mss():
    close = np.array([5, 6, 7, 11, 4, 3, 12, 13], float)
    sh = np.array([np.nan, 10, 10, 10, 10, 10, 10, 10])
    sl = np.array([np.nan, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5])
    bos_up, bos_dn, trend, mss_up, mss_dn = ind.market_structure(close, sh, sl)
    assert list(np.nonzero(bos_up)[0]) == [3]    # nur der erste Bruch je Niveau
    assert list(np.nonzero(bos_dn)[0]) == [4]
    assert mss_dn[4] and not mss_up.any()        # Wechsel von aufwärts nach abwärts
    assert list(trend) == [0, 0, 0, 1, -1, -1, -1, -1]


def test_fvg_detection():
    h = np.array([10, 12, 15, 16], float)
    l = np.array([9, 10, 11, 14], float)
    bull, bear, bottom, top = ind.fair_value_gaps(h, l, np.ones(4), 0.0)
    assert bull[2] and bottom[2] == 10 and top[2] == 11     # low[2]=11 > high[0]=10
    assert bull[3] and bottom[3] == 12 and top[3] == 14


def test_first_touch_invalidated_by_close_through_zone():
    h = np.array([10, 10, 10, 10, 10], float)
    l = np.array([9, 9, 7.5, 7.9, 8.5], float)
    c = np.array([9.5, 9.5, 7.6, 8.2, 9.0], float)
    lo, hi = np.full(5, np.nan), np.full(5, np.nan)
    lo[0], hi[0] = 8.0, 8.6
    assert ind.first_touch_after(np.array([0]), lo, hi, 1, h, l, c, 10) == []   # Schluss 7,6 < 8,0 vorher
    lo[2], hi[2] = 7.8, 8.6
    assert ind.first_touch_after(np.array([2]), lo, hi, 1, h, l, c, 10) == [(2, 3)]


def test_rsi_and_atr_ranges():
    rng = np.random.default_rng(0)
    c = 100 + rng.normal(0, 1, 500).cumsum()
    r = ind.rsi(c, 14)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100
    a = ind.atr(c + 1, c - 1, c, 14)
    assert np.all(a[20:] > 0)
    assert ind.rsi(np.arange(100, dtype=float), 14)[-1] == pytest.approx(100)
