"""Speicheroptimierungen (Blöcke, Indikator-Speicher, Prüfpunkte) dürfen Ergebnisse nicht verändern."""

import json

import numpy as np
import pandas as pd
import pytest

import synthetic_store
from tradingbot import features
from tradingbot.backtest import resources, runner
from tradingbot.backtest.runner import (Checkpoints, _canonical, _same_signals, _signals_blocked, compute_signals,
                                        run_all, run_job, run_testlauf, safe_workers, trade_strategies)
from tradingbot.config import Config
from tradingbot.storage import MarketStore
from tradingbot.strategies import base


@pytest.fixture(scope="module")
def bars():
    rng = np.random.default_rng(3)
    n = 24_000
    t = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    c = 2000 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    w = np.abs(rng.normal(0, 0.0005, n)) * c
    return pd.DataFrame({"time": t, "time_server": t.as_unit("ns").asi8 // 10**9, "open": o, "close": c,
                         "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "spread": 20})


@pytest.mark.parametrize("name", trade_strategies())
def test_blocked_signals_identical_to_full(bars, name):
    grid = base._REGISTRY[name].param_grid
    full = _signals_blocked(name, grid, bars, None)
    blocked = _signals_blocked(name, grid, bars, 6_000)
    assert sum(len(s) for s in full) > 0
    for p, a, b in zip(grid, full, blocked):
        assert _same_signals(a, b), (name, p)


def test_feature_cache_is_reused_and_read_only(bars):
    with features.shared(bars) as f:
        a1 = features.get(bars).atr(14)
        a2 = features.get(bars).atr(14)
        assert a1 is a2 and f.hits >= 1
        with pytest.raises(ValueError):
            a1[0] = 1.0                       # gemeinsame Arrays sind schreibgeschützt
    assert features.get(bars) is not f        # außerhalb von shared(): eigener, frischer Speicher


def test_block_size_shrinks_on_memory_error(bars, monkeypatch):
    real = runner._signals_blocked
    calls = []

    def fake(name, grid, b, block, warmup=runner.WARMUP):
        calls.append(block)
        if block is None or block > 20_000:
            raise MemoryError("simuliert")
        return real(name, grid, b, block, warmup)
    monkeypatch.setattr(runner, "_signals_blocked", fake)
    state = {"block": 100_000}
    out = compute_signals("ema_trend", base._REGISTRY["ema_trend"].param_grid, bars, state, log=lambda *a: None)
    assert calls == [100_000, 50_000, 25_000, 12_500] and state["block"] == 12_500 and len(out) == 4


def test_minimum_block_gives_up_with_error(bars, monkeypatch):
    def always(*a, **k):
        raise MemoryError("simuliert")
    monkeypatch.setattr(runner, "_signals_blocked", always)
    with pytest.raises(MemoryError):
        compute_signals("ema_trend", [{}], bars, {"block": runner.MIN_BLOCK}, log=lambda *a: None)


@pytest.fixture
def store_cfg(tmp_path):
    synthetic_store.build(tmp_path / "data", start="2018-01-01", end="2024-01-01", tfs=("M15", "H1", "D1"))
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    cfg.report_dir = tmp_path / "reports"
    cfg.conversion_timeframes = ["M15", "H1", "D1"]
    return cfg


def _results(job):
    return {r["strategy"]: _canonical(r) for r in job["results"]}


def test_backtest_results_identical_with_and_without_blocks(store_cfg):
    names = ["ema_trend", "liquidity_sweep", "kombi_sweep_mss"]
    a = run_job(store_cfg, "BTCUSD", "M15", names, log=lambda *x: None, block_bars=None)
    b = run_job(store_cfg, "BTCUSD", "M15", names, log=lambda *x: None, block_bars=20_000)
    assert a["bars"] > 100_000
    assert _results(a) == _results(b)


def test_resume_after_interruption_gives_identical_results(store_cfg, monkeypatch, tmp_path):
    names = ["ema_trend", "momentum", "mss"]
    ref = run_all(store_cfg, ["XAUUSD"], ["H1"], names, log=lambda *x: None, checkpoint_dir=None)
    ck = tmp_path / "ck"
    real = runner._evaluate_strategy
    count = {"n": 0}

    def flaky(*a, **k):
        count["n"] += 1
        if count["n"] == 3:
            raise KeyboardInterrupt("Abbruch simuliert")
        return real(*a, **k)
    monkeypatch.setattr(runner, "_evaluate_strategy", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_all(store_cfg, ["XAUUSD"], ["H1"], names, log=lambda *x: None, checkpoint_dir=ck)
    assert len([f for f in ck.glob("XAUUSD_H1_*.json") if "_job" not in f.name]) == 2
    monkeypatch.setattr(runner, "_evaluate_strategy", real)
    computed = []
    monkeypatch.setattr(runner, "_evaluate_strategy", lambda name, *a, **k: computed.append(name) or real(name, *a, **k))
    res = run_all(store_cfg, ["XAUUSD"], ["H1"], names, log=lambda *x: None, checkpoint_dir=ck)
    assert computed == ["mss"]                                     # nur die fehlende Einheit
    assert _results(res["jobs"][0]) == _results(ref["jobs"][0])    # identisch zum ununterbrochenen Lauf
    assert res["units_completed"] == res["units_expected"] == 3


def test_checkpoint_invalidated_by_new_data_and_corruption(store_cfg, monkeypatch, tmp_path):
    ck = tmp_path / "ck"
    run_all(store_cfg, ["BTCUSD"], ["H1"], ["ema_trend"], log=lambda *x: None, checkpoint_dir=ck)
    real = runner._evaluate_strategy
    computed = []
    monkeypatch.setattr(runner, "_evaluate_strategy", lambda name, *a, **k: computed.append(name) or real(name, *a, **k))
    run_all(store_cfg, ["BTCUSD"], ["H1"], ["ema_trend"], log=lambda *x: None, checkpoint_dir=ck)
    assert computed == []                                          # unverändert -> übernommen
    (ck / "BTCUSD_H1_ema_trend.json").write_text("{kaputt", encoding="utf-8")
    run_all(store_cfg, ["BTCUSD"], ["H1"], ["ema_trend"], log=lambda *x: None, checkpoint_dir=ck)
    assert computed == ["ema_trend"]                               # beschädigt -> neu berechnet
    st = MarketStore(store_cfg.data_root)
    last = st.load("BTCUSD", "H1").iloc[[-1]].copy()
    last["time"] += pd.Timedelta(hours=1)
    last["time_server"] += 3600
    st.upsert("BTCUSD", "H1", last)                                # neue Daten
    run_all(store_cfg, ["BTCUSD"], ["H1"], ["ema_trend"], log=lambda *x: None, checkpoint_dir=ck)
    assert computed == ["ema_trend", "ema_trend"]


def test_parallel_only_with_enough_free_memory(monkeypatch):
    monkeypatch.setattr(resources, "system_mb", lambda: (3000.0, 8192.0))
    assert safe_workers(2, log=lambda *a: None) == 1
    monkeypatch.setattr(resources, "system_mb", lambda: (12000.0, 16384.0))
    assert safe_workers(2, log=lambda *a: None) == 2
    assert safe_workers(1, log=lambda *a: None) == 1


def test_testlauf_passes_and_is_bound_to_code(store_cfg, monkeypatch, tmp_path):
    ck = tmp_path / "ck"
    t = run_testlauf(store_cfg, ["XAUUSD", "BTCUSD"], ["ema_trend", "fvg_retrace"], log=lambda *a: None,
                     checkpoint_dir=ck)
    assert t["ok"] and t["reproducible"] and not t["signal_mismatches"] and not t["errors"]
    assert runner.testlauf_ok(ck)
    assert (ck / "XAUUSD_H1_ema_trend.json").exists()              # H1-Einheiten zählen für den Hauptlauf
    monkeypatch.setattr(runner, "code_fingerprint", lambda: "anderer-code")
    assert not runner.testlauf_ok(ck)


def test_storage_loads_only_requested_columns_and_years(store_cfg):
    st = MarketStore(store_cfg.data_root)
    df = st.load("XAUUSD", "H1", start="2022-03-01", columns=["close"])
    assert list(df.columns) == ["time", "time_server", "close"]
    assert df["time"].min() >= pd.Timestamp("2022-03-01", tz="UTC")


def test_resources_report_numbers():
    snap = resources.snapshot()
    assert snap["process_mb"] and snap["process_mb"] > 0
    assert snap["available_mb"] and snap["total_mb"] >= snap["available_mb"]
    assert "RAM" in resources.status()
