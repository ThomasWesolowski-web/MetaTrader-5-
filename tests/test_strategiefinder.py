"""Strategie-Finder (strategiefinder.py, tradingbot/strategiefinder): viele Regeln, ehrliche Auswahl, nur Rechnung."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import strategiefinder
import synthetic_store
from tradingbot import features
from tradingbot.backtest.account import swap_nights
from tradingbot.strategies import base
from tradingbot.strategiefinder import auswahl as aw
from tradingbot.strategiefinder import bericht
from tradingbot.strategiefinder.kandidaten import GRIDS, Kandidat, alle_kandidaten, freeze
from tradingbot.strategiefinder.rechnen import Trades, _naechte, kompakt, signale, trades_regel
from tradingbot.strategies.zusatz import entry_hours, filter_signals
from tradingbot.zeitmaschine.daten import DataSource
from tradingbot.zeitmaschine.kosten import scenarios

ROOT = Path(__file__).resolve().parent.parent
QUIET = lambda *a, **k: None  # noqa: E731


@pytest.fixture(scope="module")
def store_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("sf") / "data"
    synthetic_store.build(root, start="2022-01-01", end="2024-01-01", tfs=("M15", "H1"), seed=5)
    return root


@pytest.fixture(scope="module")
def src(store_root):
    return DataSource(store_root, log=QUIET)


# ---------------------------------------------------------------------- Sicherheit
def test_finder_never_touches_orders():
    files = list((ROOT / "tradingbot" / "strategiefinder").glob("*.py")) + [ROOT / "strategiefinder.py"]
    for f in files:
        txt = f.read_text(encoding="utf-8")
        assert not re.search(r"(from|import)\s+[\w.]*execution", txt), f    # Order-Modul wird nie geladen
        for word in ("order_send", "order_check", "send_manual_order", "positions_get"):
            assert word not in txt, (f, word)


# ---------------------------------------------------------------------- Kandidaten
def test_candidate_universe_is_large_unique_and_known():
    ks = alle_kandidaten()
    assert len(ks) > 4000
    assert len({k.key for k in ks}) == len(ks)
    for name in GRIDS:
        assert name in base.available(), name
    fam = {k.familie() for k in ks}
    assert {"Trend", "Breakout", "Breakout/ORB", "Mean Reversion", "Order Blocks/SMC"} <= fam
    assert any(k.strategie == "goldbot" for k in ks)
    assert not any(k.strategie == "goldbot" for k in alle_kandidaten(mit_goldbot=False))


def test_filter_by_hour_and_direction(src):
    bars = src.bars("XAUUSD", "M15")
    with features.shared(bars):
        sig = signale("ema_trend", {"fast": 20, "slow": 50, "atr_mult": 2.0}, bars)
    hrs = entry_hours(bars)
    f = filter_signals(sig, bars, 7, 12, 1)
    assert 0 < len(f) < len(sig)
    assert np.all((hrs[f.idx] >= 7) & (hrs[f.idx] < 12)) and np.all(f.direction == 1)
    assert filter_signals(sig, bars, 0, 24, 0) is sig


def test_demo_entry_reproduces_finder_trades(src):
    """Was der Finder rechnet, ist genau das, was der Demo-Bot mit finder_gefiltert handeln würde."""
    bars = src.bars("XAUUSD", "M15")
    spec, cost = src.spec("XAUUSD"), scenarios("XAUUSD")["konservativ"]
    k = Kandidat("donchian_breakout", freeze({"n": 20, "atr_mult": 1.5}), "M15", "london", -1)
    direct = trades_regel(k, bars, spec, cost)
    e = k.demo_eintrag()
    assert e["strategy"] == "finder_gefiltert" and e["params"]["von_utc"] == 7 and e["params"]["richtung"] == -1
    strat = base.create(e["strategy"], **e["params"])
    sig = strat.entries(bars)
    k_all = Kandidat("donchian_breakout", k.params, "M15", "alle", 0)
    via_demo = trades_regel(k_all, bars, spec, cost, sig=sig)
    assert len(direct) > 5
    assert np.array_equal(direct["entry_time"].to_numpy(), via_demo["entry_time"].to_numpy())
    assert np.allclose(direct["pnl"], via_demo["pnl"])


def test_vectorised_swap_nights_match_account():
    rng = np.random.default_rng(1)
    a = rng.integers(1_700_000_000, 1_710_000_000, 200)
    b = a + rng.integers(0, 12 * 86400, 200)
    fast = _naechte(a, b, False, 3)
    slow = [swap_nights(int(x), int(y), False, 3) for x, y in zip(a, b)]
    assert np.allclose(fast, slow)


# ---------------------------------------------------------------------- Walk-Forward ohne Blick in die Zukunft
def _mk(times, rs, hold_h=2.0) -> Trades:
    e = pd.DatetimeIndex(times).as_unit("ns").asi8
    x = (pd.DatetimeIndex(times) + pd.Timedelta(hours=hold_h)).as_unit("ns").asi8
    r = np.asarray(rs, float)
    return Trades(e.copy(), x.copy(), r, r * 10)


def test_walk_forward_only_uses_past():
    days = pd.date_range("2020-01-01", "2023-12-31", freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    stabil = _mk(days, rng.normal(0.15, 1.0, len(days)))
    # "zukunft": bis Mitte 2022 schlecht, danach traumhaft – darf erst gewählt werden, wenn das schon passiert ist
    cut = pd.Timestamp("2022-07-01", tz="UTC")
    rz = np.where(days < cut, rng.normal(-0.3, 1.0, len(days)), rng.normal(2.0, 0.5, len(days)))
    zukunft = _mk(days, rz)
    r = aw.Regeln(oos_monate=3, lern_monate=12)
    wahl, oos, keys = aw.walk_forward({"stabil": stabil, "zukunft": zukunft}, days[0], days[-1], r)
    for w in wahl:
        if pd.Timestamp(w.a, tz="UTC") <= cut:
            assert w.key != "zukunft", w
    later = [w for w in wahl if pd.Timestamp(w.a, tz="UTC") >= cut + pd.DateOffset(months=6)]
    assert later and all(w.key == "zukunft" for w in later)
    assert np.all(np.diff(oos.entry) > 0)                       # höchstens eine Position zur Zeit


def test_learning_window_ignores_trades_still_open():
    """Ein Trade, der vor dem Abschnitt beginnt, aber erst danach schließt, darf die Wahl nicht beeinflussen."""
    days = pd.date_range("2021-01-01", "2021-12-31", freq="D", tz="UTC")
    a = _mk(days, np.full(len(days), 0.1) + np.tile([0.05, -0.05], len(days) // 2 + 1)[:len(days)])
    b = _mk(days, np.tile([0.2, -0.2], len(days) // 2 + 1)[:len(days)], hold_h=24 * 400)   # schließt nie rechtzeitig
    key, t, n, _ = aw._lern_auswahl({"a": a, "b": b}, int(days[0].value), int(days[-1].value), aw.Regeln())
    assert key == "a"


def test_deflated_sharpe_punishes_many_trials():
    rng = np.random.default_rng(3)
    trials = [rng.normal(0, 1, 200) for _ in range(500)]
    srs = np.array([aw.sharpe(x) for x in trials])
    best = trials[int(np.argmax(srs))]
    assert aw.deflated_sharpe(best, srs) < 0.5                 # bester von 500 Zufällen: nicht überzeugend
    real = rng.normal(0.4, 1, 200)
    assert aw.deflated_sharpe(real, srs) > 0.9                 # echter Vorteil bleibt sichtbar


def test_traffic_light():
    days = pd.date_range("2020-01-01", "2023-12-31", freq="D", tz="UTC")
    rng = np.random.default_rng(1)
    gut = _mk(days, rng.normal(0.5, 1.0, len(days)))
    st = aw.bewerte_setup("H1", "alle", {"gut": gut}, days[0], days[-1], aw.Regeln(), hart={"gut": gut})
    aw.setup_dsr([st])
    st.dsr_oos = 0.99
    aw.ampel(st, aw.Regeln())
    assert st.ampel == "gruen", st.gruende
    schlecht = _mk(days, rng.normal(-0.2, 1.0, len(days)))
    st2 = aw.bewerte_setup("H1", "alle", {"s": schlecht}, days[0], days[-1], aw.Regeln(min_t=-99),
                           hart={"s": schlecht})
    aw.ampel(st2, aw.Regeln())
    assert st2.ampel == "rot"


# ---------------------------------------------------------------------- Ende zu Ende
def test_cli_end_to_end(store_root, capsys):
    cfgp = store_root.parent / "config.toml"
    cfgp.write_text('[data]\nroot = "data"\nreport_dir = "reports"\n\n[execution]\norders_enabled = false\n',
                    encoding="utf-8")
    rc = strategiefinder.main(["--zeitrahmen", "M15", "H1", "--strategie", "ema_trend", "donchian_breakout",
                               "rsi_mean_reversion", "--schnell", "--laeufe", "200", "--kerne", "1",
                               "--kein-browser", "--demo-eintragen", "--config", str(cfgp)], client=None)
    out = capsys.readouterr().out
    assert rc == 0 and "NICHTS gehandelt" in out and "Urteil" in out
    pages = list((store_root.parent / "reports" / "strategiefinder").glob("strategiefinder_XAUUSD_*.html"))
    assert pages
    page = pages[0].read_text(encoding="utf-8")
    assert "Alle Setups auf einen Blick" in page and "Rückblick-Sieger" in page
    cfg_txt = cfgp.read_text(encoding="utf-8")
    if "Demo-Kandidat (nur Demokonto)" in out or "In config.toml eingetragen" in out:
        assert "finder_gefiltert" in cfg_txt
    else:
        assert "Nichts in config.toml eingetragen" in out and "finder_gefiltert" not in cfg_txt


def test_demo_entry_written_before_execution_and_replaced(tmp_path):
    cfgp = tmp_path / "config.toml"
    cfgp.write_text('[paper]\ninterval_s = 60\n\n[[paper.candidates]]\nsymbol = "XAUUSD"\ntimeframe = "M5"\n'
                    'strategy = "opening_range_breakout"\nparams = { session = "new_york" }\n\n[execution]\n'
                    'orders_enabled = true\n', encoding="utf-8")
    k1 = Kandidat("ema_trend", freeze({"fast": 20, "slow": 50, "atr_mult": 2.0}), "H1", "london", 1)
    k2 = Kandidat("supertrend", freeze({"atr_n": 10, "mult": 3.0}), "M30", "alle", 0)
    strategiefinder.demo_eintragen(cfgp, k1.demo_eintrag())
    strategiefinder.demo_eintragen(cfgp, k2.demo_eintrag())
    txt = cfgp.read_text(encoding="utf-8")
    assert txt.count("finder_gefiltert") == 1 and "supertrend" in txt and "ema_trend" not in txt
    assert txt.index("supertrend") < txt.index("[execution]")
    import tomllib
    d = tomllib.loads(txt)
    c = d["paper"]["candidates"]
    assert len(c) == 2 and c[1]["params"]["basis"] == "supertrend" and c[1]["params"]["basis_params"]["mult"] == 3.0
    base.create(c[1]["strategy"], **c[1]["params"])            # ladbar wie im Demo-Bot


def test_toml_entry_roundtrip():
    import tomllib
    k = Kandidat("opening_range_breakout", freeze({"session": "utc", "range_min": 420, "rr": 1.5}), "M15", "alle", 0)
    d = tomllib.loads(bericht.toml_eintrag(k.demo_eintrag()))
    assert d["paper"]["candidates"][0]["params"]["basis_params"] == {"range_min": 420, "rr": 1.5, "session": "utc"}


def test_kompakt_sorted_and_typed(src):
    bars = src.bars("XAUUSD", "H1")
    k = Kandidat("ema_trend", freeze({"fast": 9, "slow": 21, "atr_mult": 2.0}), "H1")
    tr = kompakt(trades_regel(k, bars, src.spec("XAUUSD"), scenarios("XAUUSD")["konservativ"]))
    assert len(tr) > 10 and np.all(np.diff(tr.entry) >= 0) and np.all(tr.exit >= tr.entry)
