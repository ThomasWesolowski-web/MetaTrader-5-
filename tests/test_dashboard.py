"""Phase 4 – Dashboard: Daten werden nur gelesen, fehlende Kennzahlen nicht geschätzt, Bereiche klar getrennt."""

import json
from pathlib import Path

import pandas as pd
import pytest

import synthetic_store
from fake_market import FakeMT5
from test_paper import CANDS, make_observer, run_steps, write_specs
from tradingbot import dashboard_data as dd
from tradingbot.config import Config
from tradingbot.storage import MarketStore

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "docs" / "phase3_zusammenfassung.json"


@pytest.fixture(scope="module")
def summary():
    return dd.load_phase3(SUMMARY)


def test_phase3_source_prefers_full_results(tmp_path):
    path, kind = dd.phase3_source(tmp_path, ROOT)
    assert path == SUMMARY and "kompakt" in kind
    (tmp_path / "phase3_ergebnisse.json").write_text("{}", encoding="utf-8")
    path, kind = dd.phase3_source(tmp_path, ROOT)
    assert path.name == "phase3_ergebnisse.json" and "vollständig" in kind


def test_ranking_and_details_from_phase3(summary):
    comp = dd.completeness(summary)
    assert comp["einheiten_erwartet"] == 120 and comp["einheiten_fertig"] == 120 and not comp["fehler"]
    rk = dd.ranking_table(summary, "BTCUSD")
    assert len(rk) == 60 and rk["Rang"].tolist() == list(range(1, 61))
    assert not rk["qualifiziert"].any()
    # Drawdown in € ist in Lauf 3 nicht gespeichert -> None, nicht geschätzt
    assert rk["Test DD €"].isna().all() and rk["Test DD %"].notna().all()
    job, res = dd.find_result(summary, "BTCUSD", "H1", "ema_trend")
    assert res["params"] == {"fast": 50, "slow": 200, "atr_mult": 3.0}
    mt = dd.metrics_table(res)
    assert set(mt["Kostenszenario"]) == {"basis", "konservativ"} and len(mt) == 6
    t = mt[(mt["Kostenszenario"] == "konservativ") & (mt["Zeitraum"] == "Test")].iloc[0]
    assert t["Trades"] == res["evaluation"]["konservativ"]["test"]["trades"]
    assert t["Netto €"] == res["evaluation"]["konservativ"]["test"]["net_profit_eur"]
    assert not dd.folds_table(res).empty and not dd.risk_variants_table(res).empty
    assert not dd.yearly_table(res, "konservativ").empty


def test_cost_table_matches_phase3_report(summary):
    df = dd.cost_scenarios_by_tf(summary)
    assert len(df) == 10
    row = df[(df["Markt"] == "XAUUSD") & (df["TF"] == "M1")].iloc[0]
    assert row["Netto € konservativ"] == pytest.approx(-4785.68, abs=0.05)     # Wert aus dem Phase-3-Bericht
    assert row["Netto € basis"] == pytest.approx(-3829.25, abs=0.05)


def test_quality_and_freshness(tmp_path):
    report = {"created_utc": "2026-10-18T14:29:00+00:00", "quality": [
        {"symbol": "XAUUSD", "tf": "H1", "rows": 100, "first_utc": "2018-01-01", "last_utc": "2026-10-16",
         "duplicates": 0, "invalid_ohlc": 1, "nonpositive_prices": 0, "nan_rows": 0, "negative_spread": 0,
         "gaps": {"data_gap": 3, "weekend": 400}, "data_gap_open_hours": 7.5, "coverage_pct": 99.9,
         "anomalies": {"spike": 2}}]}
    (tmp_path / "phase2_datenbericht_20261018_142900.json").write_text(json.dumps(report), encoding="utf-8")
    p = dd.latest_quality_report(tmp_path)
    q = dd.quality_table(dd._read_json(p))
    assert q.iloc[0]["unerklärte Lücken"] == 3 and q.iloc[0]["ungültig"] == 1 and q.iloc[0]["Ausreißer"] == 2
    st = MarketStore(tmp_path / "data")
    st.write_meta("BTCUSD", {"timeframes": {"H1": {"first_utc": "2018-04-01T00:00:00+00:00",
                                                   "last_utc": "2026-10-18T10:00:00+00:00", "rows": 5,
                                                   "last_update_utc": "2026-10-18T11:01:00+00:00"}}})
    fr = dd.freshness_table(tmp_path / "data", pd.Timestamp("2026-10-18 14:00", tz="UTC"))
    assert fr.iloc[0]["Alter (h)"] == 3.0                       # Kerze 10:00–11:00, jetzt 14:00


def test_offline_contract_check_from_stored_data(tmp_path):
    synthetic_store.build(tmp_path / "data", start="2023-06-01", end="2024-01-01", tfs=("M15", "H1"))
    doc = dd.offline_contract_check(tmp_path / "data", tfs=("M15", "H1"))
    btc = doc["symbols"]["BTCUSD"]
    assert btc["risk_eur"] == 2.5 and btc["max_stop_distance"] > 0
    assert btc["by_timeframe"]["H1"]["atr14"] > 0
    specs, tfs = dd.contract_rows(doc)
    assert set(specs["Symbol"]) == {"XAUUSD", "BTCUSD"} and len(tfs) == 4


@pytest.fixture(scope="module")
def paper_root(tmp_path_factory):
    fake = FakeMT5(now_utc="2026-03-02 00:00", days=60)
    cfg = Config()
    root = tmp_path_factory.mktemp("dash")
    cfg.data_root = root / "data"
    cfg.report_dir = root / "reports"
    cfg.paper.candidates = list(CANDS)
    cfg.paper.warmup_bars = 600
    write_specs(cfg.data_root, fake)
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-08 00:00")
    return root


def test_paper_tables(paper_root):
    p = dd.load_paper(paper_root / "data")
    cands = dd.paper_candidates_table(p["trades"])
    assert set(cands["Kandidat"]) == {c.key for c in CANDS}
    trades = dd.paper_trades_table(p["trades"])
    assert not trades.empty and set(trades["Status"]) <= {"offen", "geschlossen"}
    assert not dd.paper_signals_table(p["trades"]).empty
    assert not dd.events_table(p["events"]).empty
    eq = dd.paper_equity(p["trades"])
    assert eq.empty or eq["Zeit"].is_monotonic_increasing or len(eq["Kandidat"].unique()) > 1


def _apptest(root: Path, monkeypatch):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    cfg_file = root / "config.toml"
    cfg_file.write_text('[data]\nroot = "data"\nreport_dir = "reports"\n', encoding="utf-8")
    monkeypatch.setenv("TRADINGBOT_CONFIG", str(cfg_file))
    at = st_testing.AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60)
    at.run()
    return at


def test_dashboard_renders_with_separated_sections(paper_root, monkeypatch):
    at = _apptest(paper_root, monkeypatch)
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert labels == ["Übersicht", "Backtest (Phase 3)", "Live-Beobachtung (Paper-Trading)", "Daten",
                      "Kontrakt & Mindestlot"]
    texts = " ".join(str(x.value) for x in list(at.info) + list(at.warning))
    assert "Historische Simulation (Phase 3)" in texts and "Live-Beobachtung (Paper-Trading)" in texts
    assert "keine Orders" in " ".join(str(c.value) for c in at.caption)
    assert len(at.dataframe) >= 5


def test_dashboard_without_any_data(tmp_path, monkeypatch):
    at = _apptest(tmp_path, monkeypatch)
    assert not at.exception
