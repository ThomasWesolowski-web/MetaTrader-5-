import json

import pytest

import backtest_ausfuehren
import synthetic_store
from tradingbot.backtest import crosscheck
from tradingbot.storage import MarketStore


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("bt")
    synthetic_store.build(root / "data", tfs=("M15", "H1", "D1"))
    (root / "config.toml").write_text('[data]\nroot = "data"\nreport_dir = "reports"\n'
                                      'conversion_timeframes = ["M15", "H1", "D1"]\n', encoding="utf-8")
    return root


def test_full_run_writes_report_and_json(project):
    rc = backtest_ausfuehren.main(["--config", str(project / "config.toml"), "--zeitrahmen", "H1",
                                   "--strategien", "ema_trend", "mss", "momentum"])
    assert rc == 0
    data = json.loads((project / "reports" / "phase3_ergebnisse.json").read_text(encoding="utf-8"))
    assert set(data["rankings"]) == {"XAUUSD", "BTCUSD"}
    job = next(j for j in data["jobs"] if j["symbol"] == "BTCUSD")
    r = job["results"][0]
    for per in ("train", "validation", "test"):
        assert per in r["evaluation"]["konservativ"] and per in r["evaluation"]["basis"]
    assert r["walk_forward"]["folds"] and set(r["risk_variants"]) == {"0.25%", "0.5%", "1%"}
    assert job["feasibility"]["max_stop_distance_at_min_lot"] > 0
    # Zufallspfad ohne Kante: keine Strategie darf als geeignet gelten
    assert all(rk["verdict"] == "aktuell keine Strategie für den Demohandel geeignet"
               for rk in data["rankings"].values())
    md = next((project / "reports").glob("phase3_bericht_*.md")).read_text(encoding="utf-8")
    assert "Rangliste BTCUSD" in md and "Mindestlot-Prüfung" in md


def test_conservative_costs_never_better_than_basis(project):
    data = json.loads((project / "reports" / "phase3_ergebnisse.json").read_text(encoding="utf-8"))
    for j in data["jobs"]:
        for r in j["results"]:
            for per in ("validation", "test"):
                b = r["evaluation"]["basis"][per]
                k = r["evaluation"]["konservativ"][per]
                if b["trades"] and k["trades"] and b["trades"] == k["trades"]:
                    assert k["net_profit_eur"] <= b["net_profit_eur"] + 1e-9


def test_crosscheck_against_terminal(make_fake, client_for, tmp_path):
    fake = make_fake()
    st = MarketStore(tmp_path)
    st.write_json("specs.json", {
        "account": {"currency": "EUR", "leverage": 500},
        "instruments": {s: {**synthetic_store.SPECS[s]} for s in ("XAUUSD", "BTCUSD")},
        "conversions": [{"symbol": "EURUSD", "base": "EUR", "quote": "USD"}]})
    out = crosscheck.run(client_for(fake), st)
    for sym, rows in out.items():
        assert rows
        for x in rows:
            assert abs(x["profit_dev_pct"]) < 0.5        # Cent-Rundung von MT5 bei kleinen Beträgen
            assert abs(x["margin_dev_pct"]) < 0.5


def test_periods_start_after_long_data_hole(tmp_path):
    """Regressionstest: Vereinzelte frühe Kerzen vor einer langen Lücke dürfen den Trainingszeitraum nicht
    in die Lücke legen (Fall Gold: Intraday ab 2007 gemeldet, lückenlos erst ab ca. 2017)."""
    import pandas as pd
    from tradingbot.backtest.runner import run_job
    from tradingbot.config import Config
    st = synthetic_store.build(tmp_path / "data", start="2019-01-01", end="2024-01-01", tfs=("H1", "D1"))
    early = st.load("XAUUSD", "H1").iloc[:200].copy()
    shift = pd.Timedelta(days=365 * 4)
    early["time"] = early["time"] - shift
    early["time_server"] = early["time_server"] - int(shift.total_seconds())
    st.upsert("XAUUSD", "H1", early)
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    cfg.conversion_timeframes = ["H1", "D1"]
    res = run_job(cfg, "XAUUSD", "H1", ["ema_trend"], log=lambda *a: None)
    assert res["periods"]["start"] >= "2019-01-01"
    assert min(res["bars_per_year"]) >= "2019"


def test_periods_start_where_data_becomes_dense(tmp_path):
    """Regressionstest: Dünne frühe Historie (nur ein Teil der Handelsstunden vorhanden) darf nicht im
    Trainingszeitraum liegen (Fall Gold-Intraday bei Vantage)."""
    import numpy as np
    from tradingbot.backtest.runner import run_job
    from tradingbot.config import Config
    from tradingbot.server_time import parse_scheme
    st = synthetic_store.build(tmp_path / "data", start="2018-01-01", end="2024-01-01", tfs=("H1", "D1"))
    h1 = st.load("XAUUSD", "H1")
    early = (h1["time"] < "2020-01-01").to_numpy()
    keep = ~early | (np.arange(len(h1)) % 3 == 0)
    for f in (tmp_path / "data" / "market" / "XAUUSD" / "H1").glob("*.parquet"):
        f.unlink()
    st.upsert("XAUUSD", "H1", h1[keep])
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    cfg.conversion_timeframes = ["H1", "D1"]
    res = run_job(cfg, "XAUUSD", "H1", ["ema_trend"], log=lambda *a: None)
    assert res["periods"]["start"] >= "2020-01-01"


def test_failing_strategy_does_not_abort_run(project, monkeypatch):
    """Ein Fehler in einer Strategie darf den Lauf nicht abbrechen: Bericht, JSON und Protokoll entstehen,
    der Fehler wird ausgewiesen."""
    from tradingbot.strategies.library import Momentum

    # Testlauf vorher bestehen (er prüft den Programmstand, der sich durch den Monkeypatch nicht ändert)
    assert backtest_ausfuehren.main(["--config", str(project / "config.toml"), "--zeitrahmen", "H1",
                                     "--strategien", "ema_trend", "momentum", "--testlauf"]) == 0

    def boom(self, bars):
        raise ValueError("Testfehler")
    monkeypatch.setattr(Momentum, "entries", boom)
    rc = backtest_ausfuehren.main(["--config", str(project / "config.toml"), "--zeitrahmen", "H1",
                                   "--strategien", "ema_trend", "momentum", "--neu"])
    assert rc == 1
    data = json.loads((project / "reports" / "phase3_ergebnisse.json").read_text(encoding="utf-8"))
    assert {e["strategy"] for e in data["errors"]} == {"momentum"}
    assert all(r["strategy"] == "ema_trend" for rk in data["rankings"].values() for r in rk["ranking"])
    assert list((project / "reports").glob("phase3_log_*.txt"))
