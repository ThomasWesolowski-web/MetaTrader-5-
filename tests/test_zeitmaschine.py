"""Zeitmaschine 2 (zeitmaschine.py, tradingbot/zeitmaschine): echte Regeln, Kosten, Block-Bootstrap, nur lesend."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import synthetic_store
import zeitmaschine
from tradingbot.config import Config
from tradingbot.goldbot.config import GoldConfig
from tradingbot.zeitmaschine import auswertung as aw
from tradingbot.zeitmaschine import bericht
from tradingbot.zeitmaschine.daten import DataSource, resample
from tradingbot.zeitmaschine.handel import TRADE_COLS, goldbot_trades, orb_trades
from tradingbot.zeitmaschine.kosten import ORDER, scenarios
from tradingbot.zeitmaschine.lauf import run

ROOT = Path(__file__).resolve().parent.parent
NOW = pd.Timestamp("2024-01-01", tz="UTC")


@pytest.fixture(scope="module")
def store_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("zm") / "data"
    synthetic_store.build(root, start="2023-07-01", end="2024-01-01", tfs=("M5", "M15"), seed=3)
    return root


@pytest.fixture(scope="module")
def src(store_root):
    return DataSource(store_root, log=lambda *a, **k: None)


def _trades(pnls, start="2024-01-01", every_days=1.0, risk_usd=10.0):
    t0 = pd.Timestamp(start, tz="UTC")
    times = [t0 + pd.Timedelta(days=every_days * i) for i in range(len(pnls))]
    df = pd.DataFrame({"entry_time": times, "exit_time": [t + pd.Timedelta(hours=1) for t in times],
                       "direction": 1, "setup": "x", "entry": 100.0, "stop": 99.0, "risk": 1.0,
                       "risk_usd": risk_usd, "volume": 0.01, "gross": pnls, "commission": 0.0, "swap": 0.0,
                       "pnl": np.asarray(pnls, float), "r": np.asarray(pnls, float) / risk_usd,
                       "exit_reason": "stop"})
    return df[TRADE_COLS]


# ---------------------------------------------------------------------- Sicherheit
def test_zeitmaschine_never_touches_orders():
    """Kein Import von execution, kein order_send – die Zeitmaschine kann gar nicht handeln."""
    files = list((ROOT / "tradingbot" / "zeitmaschine").glob("*.py")) + [ROOT / "zeitmaschine.py"]
    for f in files:
        txt = f.read_text(encoding="utf-8")
        assert not re.search(r"\bexecution\b", txt.replace("Ausführung", "")), f
        for word in ("order_send", "order_check", "send_manual_order", "positions_get"):
            assert word not in txt, (f, word)


# ---------------------------------------------------------------------- Daten
def test_resample_m5_to_m15_matches_ohlc(src):
    m5 = src.bars("XAUUSD", "M5")
    m15 = resample(m5.iloc[:300], "M15")
    first = m5.iloc[:3]
    row = m15.iloc[0]
    assert row["open"] == first["open"].iloc[0] and row["close"] == first["close"].iloc[2]
    assert row["high"] == first["high"].max() and row["low"] == first["low"].min()
    assert (pd.DatetimeIndex(m15["time"]).minute % 15 == 0).all()


def test_missing_symbol_gives_clear_message(src):
    with pytest.raises(RuntimeError, match="Keine Kerzen"):
        src.bars("GER40", "M5")


# ---------------------------------------------------------------------- Kosten und Trades
def test_three_cost_scenarios_get_more_expensive():
    s = scenarios("XAUUSD")
    assert list(s) == list(ORDER)
    assert s["basis"].spread_factor < s["konservativ"].spread_factor < s["hart"].spread_factor
    assert s["basis"].slippage_frac < s["konservativ"].slippage_frac < s["hart"].slippage_frac


def test_orb_trades_costs_reduce_result(src):
    bars = src.bars("XAUUSD", "M5")
    spec = src.spec("XAUUSD")
    res = {n: orb_trades(bars, spec, c, "M5", lot=0.01) for n, c in scenarios("XAUUSD").items()}
    assert len(res["basis"]) > 50
    assert list(res["basis"].columns) == TRADE_COLS
    assert res["basis"]["pnl"].sum() > res["konservativ"]["pnl"].sum() > res["hart"]["pnl"].sum()
    tr = res["konservativ"]
    assert (tr["exit_time"] >= tr["entry_time"]).all()
    assert (tr["exit_time"].iloc[:-1].to_numpy() <= tr["entry_time"].iloc[1:].to_numpy()).all()  # eine zur Zeit
    assert np.allclose(tr["risk_usd"], tr["risk"] * 100 * 0.01)
    cap = float(tr["risk_usd"].median())
    capped = orb_trades(bars, spec, scenarios("XAUUSD")["konservativ"], "M5", lot=0.01, max_risk_usd=cap)
    assert 0 < len(capped) < len(tr) and (capped["risk_usd"] <= cap + 1e-9).all()


def test_goldbot_trades_follow_bot_rules(src):
    bars = src.bars("XAUUSD", "M15")
    bars = bars.iloc[-(600 + 2500):].reset_index(drop=True)
    spec = src.spec("XAUUSD")
    g = GoldConfig()
    g.ob_displacement_atr = 1.0                    # Zufallsdaten: etwas lockerer, damit Setups entstehen
    tr = goldbot_trades(bars, spec, scenarios("XAUUSD")["konservativ"], g, start=600, lot=0.01,
                        max_risk_usd=50.0)
    assert len(tr) >= 3
    assert set(tr["setup"]) <= {"Plan A", "Plan B"}
    assert np.allclose(tr["volume"], 0.02)                     # 2 Teile à 0,01
    assert (tr["risk_usd"] <= 50.0 * 1.6).all()                # Grenze auf Signal-Kurs; Fill kann leicht abweichen
    assert (tr["exit_time"].iloc[:-1].to_numpy() <= tr["entry_time"].iloc[1:].to_numpy()).all()
    b = tr[tr["setup"] == "Plan B"]                              # Marktorder: Einstieg im Handelsfenster
    assert ((b["entry_time"].dt.hour >= g.entry_from_utc) & (b["entry_time"].dt.hour <= g.entry_to_utc)).all()
    assert (tr["entry_time"].dt.weekday < 5).all()


# ---------------------------------------------------------------------- Kennzahlen und Monte Carlo
def test_kennzahlen_basic_numbers():
    k = aw.kennzahlen(_trades([10, -5, -5, 20, -5]), 100.0, dd_stopp_pct=10)
    assert k["trades"] == 5 and k["gewinnrate"] == 0.4
    assert k["mittel_gewinn"] == 15 and k["mittel_verlust"] == 5 and k["crv"] == 3.0
    assert k["summe_usd"] == 15 and k["endkonto"] == 115
    assert k["max_drawdown_usd"] == 10 and k["laengste_verlustserie"] == 2
    assert k["profit_faktor"] == 2.0 and k["dd_stopp_am"] is not None


def test_block_bootstrap_keeps_losing_streaks_together():
    """Erst 12 Wochen nur Verluste, dann 12 Wochen nur Gewinne: Blöcke erhalten die Pechsträhne, einzeln gezogene
    Trades zerreißen sie – der Rückgang wird mit Blöcken größer."""
    pn = [-6.0] * 36 + [6.5] * 36
    tr = _trades(pn, every_days=7 / 3)
    blk = aw.monte_carlo(tr, 300, laeufe=2000, wochen_horizont=24, block_wochen=8, seed=1)
    iid = aw.monte_carlo(tr, 300, laeufe=2000, wochen_horizont=24, block_wochen=8, seed=1, einzeln=True)
    assert blk["drawdown_usd"]["50"] > 2 * iid["drawdown_usd"]["50"]
    assert blk == aw.monte_carlo(tr, 300, laeufe=2000, wochen_horizont=24, block_wochen=8, seed=1)


def test_ruin_is_absorbing():
    tr = _trades([-20.0] * 30, every_days=2)
    mc = aw.monte_carlo(tr, 100, laeufe=200, wochen_horizont=20, ruin_konto=10)
    assert mc["p_ruin"] == 1.0 and mc["endkonto"]["95"] <= 10
    assert mc["im_plus"] == 0.0


def test_urteil_flags_cost_sensitivity_and_few_trades():
    good = {"trades": 20, "summe_usd": 50}
    bad = {"trades": 20, "summe_usd": -10}
    u = aw.urteil({"basis": good, "konservativ": bad, "hart": bad}, [], {"laeufe": 0}, 357)
    assert any("zu wenige" in x for x in u) and any("nur bei sehr niedrigen Kosten" in x for x in u)


# ---------------------------------------------------------------------- Ganzer Lauf und Befehl
def test_run_goldbot_end_to_end(src):
    g = GoldConfig()
    o = run(src, "goldbot", "XAUUSD", tage=20, konto=357, laeufe=200, gcfg=g, log=lambda *a, **k: None, now=NOW)
    assert set(o["kennzahlen"]) == set(ORDER)
    assert o["einstellungen"]["max_risiko_pct"] == g.max_risk_pct
    assert o["urteil"] and "Vergangenheit" in o["urteil"][-1]
    assert "ZEITMASCHINE" in bericht.text(o)
    assert bericht.html_page(o).startswith("<!DOCTYPE html>")


def test_cli_writes_report_without_mt5(store_root, capsys):
    cfgp = store_root.parent / "config.toml"
    cfgp.write_text('[data]\nroot = "data"\nreport_dir = "reports"\n', encoding="utf-8")
    rc = zeitmaschine.main(["orb", "--symbol", "XAUUSD", "--laeufe", "300", "--kein-browser", "--tage", "120",
                            "--config", str(cfgp), "--goldbot-config", str(store_root.parent / "fehlt.toml")],
                           client=None, now=NOW)
    out = capsys.readouterr().out
    assert rc == 0 and "NICHTS gehandelt" in out and "RUIN" in out
    pages = list((store_root.parent / "reports" / "zeitmaschine").glob("orb_XAUUSD_*.html"))
    assert pages and "Was hätte auch passieren können" in pages[0].read_text(encoding="utf-8")


def test_cli_skips_unknown_symbol_but_continues(store_root, capsys):
    cfgp = store_root.parent / "config.toml"
    rc = zeitmaschine.main(["orb", "--symbol", "GER40", "--laeufe", "100", "--kein-browser",
                            "--config", str(cfgp), "--goldbot-config", str(store_root.parent / "fehlt.toml")],
                           client=None, now=NOW)
    assert rc == 1 and "übersprungen" in capsys.readouterr().out
