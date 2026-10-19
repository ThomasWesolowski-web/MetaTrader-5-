"""Phase 4 – Beobachtung (Paper-Trading): kein Look-ahead, keine erfundenen Kurse, keine Orders."""

import json

import numpy as np
import pandas as pd
import pytest

import beobachtung_ausfuehren
from fake_market import FakeMT5
from tradingbot.backtest.costs import scenarios_for
from tradingbot.backtest.engine import simulate
from tradingbot.config import Config, PaperCandidate
from tradingbot.instruments import InstrumentSpec
from tradingbot.mt5_client import MT5Client
from tradingbot.paper import Observer, PaperStore
from tradingbot.server_time import parse_scheme
from tradingbot.storage import MarketStore, rates_to_frame
from tradingbot.strategies import Signals, create

START = "2026-03-02 00:00"
CANDS = [PaperCandidate("BTCUSD", "H1", "donchian_breakout", {"n": 20, "atr_mult": 2.0}),
         PaperCandidate("BTCUSD", "M30", "ema_trend", {"fast": 20, "slow": 50, "atr_mult": 2.0}),
         PaperCandidate("BTCUSD", "H1", "kombi_breakout_momentum", {"n": 20, "rr": 2.0})]


def write_specs(root, fake):
    instruments = {s: InstrumentSpec.from_mt5(fake._info(s)._asdict()).to_dict() for s in ("XAUUSD", "BTCUSD")}
    instruments["BTCUSD"]["margin_rate"] = 0.002
    MarketStore(root).write_json("specs.json", {
        "time_scheme": {"scheme": "ny_close"}, "instruments": instruments,
        "conversions": [{"symbol": "EURUSD", "base": "EUR", "quote": "USD"}],
        "account": {"currency": "EUR", "leverage": 500}})


@pytest.fixture
def setup(tmp_path):
    fake = FakeMT5(now_utc=START, days=60)
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    cfg.paper.candidates = list(CANDS)
    cfg.paper.warmup_bars = 600
    write_specs(cfg.data_root, fake)
    return fake, cfg


def run_steps(obs, fake, until, step_min=30):
    end = pd.Timestamp(until, tz="UTC")
    while fake.now_utc < end:
        fake.now_utc += pd.Timedelta(minutes=step_min)
        obs.cycle()


def make_observer(fake, cfg):
    obs = Observer(cfg, MT5Client(fake, retry_sleep=0), now_utc_fn=lambda: fake.now_utc, log=lambda *a: None)
    obs.start()
    return obs


def offline_trades(fake, cfg, cand, start_utc):
    """Nachträgliche Gesamtrechnung mit denselben Kerzen: Referenz für die Live-Beobachtung."""
    scheme = parse_scheme("ny_close")
    tf_s = {"H1": 3600, "M30": 1800}[cand.timeframe]
    start_srv = int(scheme.to_server(pd.DatetimeIndex([pd.Timestamp(start_utc)]))[0])
    arr = fake.bars["BTCUSD"][cand.timeframe]
    now_srv = fake.now_server()
    arr = arr[(arr["time"] >= start_srv - cfg.paper.warmup_bars * tf_s) & (arr["time"] + tf_s <= now_srv)]
    bars = rates_to_frame(arr, scheme)[["time", "time_server", "open", "high", "low", "close", "spread"]]
    sig = create(cand.strategy, **cand.params).entries(bars)
    keep = bars["time_server"].to_numpy()[sig.idx] >= start_srv
    sub = Signals(sig.idx[keep], sig.direction[keep], sig.stop[keep], sig.target[keep], sig.max_bars,
                  sig.exit_long, sig.exit_short)
    costs = scenarios_for("BTCUSD")["konservativ"]
    raw, _ = simulate(bars, sub, tf_s, costs.spread_price(bars["spread"].to_numpy(), 0.01), costs.slippage_frac, 2)
    return raw


@pytest.fixture(scope="module")
def observed(tmp_path_factory):
    """Eine Beobachtung über 12 Tage (Durchlauf alle 30 min) – gemeinsam für mehrere Tests."""
    fake = FakeMT5(now_utc=START, days=60)
    cfg = Config()
    cfg.data_root = tmp_path_factory.mktemp("paper") / "data"
    cfg.paper.candidates = list(CANDS)
    cfg.paper.warmup_bars = 600
    write_specs(cfg.data_root, fake)
    obs = make_observer(fake, cfg)
    seen_last_bar = []
    end = pd.Timestamp("2026-03-14 00:00", tz="UTC")
    while fake.now_utc < end:
        fake.now_utc += pd.Timedelta(minutes=30)
        obs.cycle()
        for key, snap in obs.snap.items():
            if snap["letzte_kerze_utc"]:
                tf = pd.Timedelta(hours=1) if "_H1_" in key else pd.Timedelta(minutes=30)
                seen_last_bar.append(pd.Timestamp(snap["letzte_kerze_utc"]) + tf <= fake.now_utc)
    return fake, cfg, seen_last_bar


def test_live_observation_equals_offline_computation_without_lookahead(observed):
    fake, cfg, seen_last_bar = observed
    assert seen_last_bar and all(seen_last_bar)                 # nie die laufende Kerze verwendet
    doc = PaperStore(cfg.data_root).read("trades.json")
    total = 0
    for cand in CANDS:
        snap = doc["kandidaten"][cand.key]
        ref = offline_trades(fake, cfg, cand, snap["beobachtung_seit_utc"])
        closed_ref = ref[ref["exit_reason"] != "end"]
        closed = [t for t in snap["trades"] if t["status"] == "geschlossen"]
        assert len(closed) == len(closed_ref)
        for t, r in zip(closed, closed_ref.itertuples()):
            assert t["einstieg_utc"] == str(r.entry_time) and t["ausstieg_utc"] == str(r.exit_time)
            assert t["einstieg"] == pytest.approx(r.entry) and t["ausstieg"] == pytest.approx(r.exit)
            assert pd.Timestamp(t["einstieg_utc"]) >= pd.Timestamp(t["signal_kerze_utc"]) + pd.Timedelta(minutes=30)
        for s in snap["signale"]:
            assert pd.Timestamp(s["erkannt_utc"]) >= pd.Timestamp(s["kerzenschluss_utc"])   # erst nach Kerzenschluss
            assert pd.Timestamp(s["signal_kerze_utc"]) >= pd.Timestamp(snap["beobachtung_seit_utc"])
        total += len(closed)
    assert total > 0                                         # der Test hat tatsächlich Trades geprüft


def test_signal_is_causal_with_data_up_to_signal_bar(observed):
    fake, cfg, _ = observed
    snap = PaperStore(cfg.data_root).read("trades.json")["kandidaten"][CANDS[0].key]
    assert snap["signale"]
    scheme = parse_scheme("ny_close")
    for s in snap["signale"]:
        # nur mit Kerzen bis einschließlich der Signalkerze rechnen -> dasselbe Signal muss entstehen
        end_srv = int(scheme.to_server(pd.DatetimeIndex([pd.Timestamp(s["signal_kerze_utc"])]))[0])
        start_srv = int(scheme.to_server(pd.DatetimeIndex([pd.Timestamp(snap["beobachtung_seit_utc"])]))[0])
        arr = fake.bars["BTCUSD"]["H1"]
        arr = arr[(arr["time"] >= start_srv - 600 * 3600) & (arr["time"] <= end_srv)]
        bars = rates_to_frame(arr, scheme)
        sig = create(CANDS[0].strategy, **CANDS[0].params).entries(bars)
        assert len(bars) - 1 in set(sig.idx.tolist())


def test_cost_breakdown_and_reference_account(observed):
    fake, cfg, _ = observed
    doc = PaperStore(cfg.data_root).read("trades.json")
    closed = [t for c in doc["kandidaten"].values() for t in c["trades"] if t["status"] == "geschlossen"]
    assert closed
    for t in closed:
        assert t["volumen_mindestlot"] == 0.01
        # Netto = Brutto (Spread und Slippage im Kurs enthalten) − Kommission + Swap
        assert t["netto_eur"] == pytest.approx(t["brutto_eur"] - t["kommission_eur"] + t["swap_eur"], abs=0.02)
        assert t["spread_eur"] > 0 and t["slippage_eur"] >= 0 and t["kommission_eur"] > 0
        assert t["referenzkonto"]["ausgefuehrt"] in (True, False)
        if not t["innerhalb_risikogrenze"]:
            assert t["referenzkonto"]["ausgefuehrt"] is False and "Mindestlot" in t["referenzkonto"]["grund"]
    acct = [c["referenzkonto"] for c in doc["kandidaten"].values() if c["referenzkonto"]]
    assert acct and all("max_drawdown_eur" in a and "max_drawdown_pct" in a for a in acct)
    ml = [c["mindestlot"] for c in doc["kandidaten"].values() if c["mindestlot"] and c["mindestlot"]["trades"]]
    assert ml and all(m["max_drawdown_eur"] >= 0 for m in ml)


def test_signals_while_program_was_off_are_not_traded(setup):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-05 00:00")
    fake.now_utc += pd.Timedelta(days=3)                       # PC aus / Programm gestoppt
    obs.cycle()
    run_steps(obs, fake, "2026-03-10 00:00")
    st = PaperStore(cfg.data_root).state()
    late = [r for c in st["candidates"].values() for r in c["rejected"].values() if "verspätet" in r["grund"]]
    assert late                                                 # in der Pause entstandene Signale ...
    doc = PaperStore(cfg.data_root).read("trades.json")
    traded = {t["signal"] for c in doc["kandidaten"].values() for t in c["trades"]}
    assert not traded & {r["key"] for r in late}                # ... werden nicht gehandelt
    events = [e["art"] for e in PaperStore(cfg.data_root).events()]
    assert "signal_verspaetet" in events


def test_stale_ticks_are_reported_and_no_prices_invented(setup, monkeypatch):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-04 00:00")
    real = fake.symbol_info_tick
    frozen = real("BTCUSD")
    monkeypatch.setattr(fake, "symbol_info_tick", lambda s: frozen if s == "BTCUSD" else real(s))
    fake.now_utc += pd.Timedelta(minutes=30)
    st = obs.cycle()
    assert st["maerkte"]["BTCUSD"]["zustand"] == "keine aktuellen Kurse"
    events = PaperStore(cfg.data_root).events()
    assert any(e["art"] == "keine_aktuellen_kurse" and e["symbol"] == "BTCUSD" for e in events)
    monkeypatch.setattr(fake, "symbol_info_tick", real)
    fake.now_utc += pd.Timedelta(minutes=10)
    assert obs.cycle()["maerkte"]["BTCUSD"]["zustand"] == "aktuell"


def test_data_gap_is_logged_and_no_entry_across_gap(setup):
    fake, cfg = setup
    arr = fake.bars["BTCUSD"]["H1"]
    scheme = parse_scheme("ny_close")
    gap_from = int(scheme.to_server(pd.DatetimeIndex([pd.Timestamp("2026-03-06 10:00", tz="UTC")]))[0])
    fake.bars["BTCUSD"]["H1"] = arr[(arr["time"] < gap_from) | (arr["time"] >= gap_from + 5 * 3600)]
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-09 00:00")
    events = PaperStore(cfg.data_root).events()
    gaps = [e for e in events if e["art"] == "datenluecke" and "_H1_" in e["kandidat"]]
    assert gaps and gaps[0]["fehlende_kerzen"] == 5
    doc = PaperStore(cfg.data_root).read("trades.json")
    for key in (CANDS[0].key, CANDS[2].key):
        for t in doc["kandidaten"][key]["trades"]:                   # kein Einstieg innerhalb der Lücke
            e = pd.Timestamp(t["einstieg_utc"])
            assert not (pd.Timestamp("2026-03-06 10:00", tz="UTC") <= e < pd.Timestamp("2026-03-06 15:00", tz="UTC"))


def test_restart_continues_without_duplicates(setup):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-06 00:00")
    obs.close()
    obs2 = make_observer(fake, cfg)                              # Neustart, Stand aus data/paper
    run_steps(obs2, fake, "2026-03-10 00:00")
    events = PaperStore(cfg.data_root).events()
    starts = [e["kandidat"] for e in events if e["art"] == "beobachtung_start"]
    assert sorted(starts) == sorted(c.key for c in CANDS)
    sig = [(e["kandidat"], e["key"]) for e in events if e["art"] == "signal"]
    assert len(sig) == len(set(sig))
    outs = [e["trade"] for e in events if e["art"] == "ausstieg"]
    assert len(outs) == len(set(outs))
    assert not [e for e in events if e["art"] == "abweichung"]


def test_changed_parameters_are_not_mixed_into_running_observation(setup):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-03 00:00")
    cfg.paper.candidates[0] = PaperCandidate("BTCUSD", "H1", "donchian_breakout", {"n": 55, "atr_mult": 2.5})
    fake.now_utc += pd.Timedelta(hours=1)
    obs.cycle()
    errs = [e for e in PaperStore(cfg.data_root).events() if e["art"] == "fehler"]
    assert errs and "nicht vermischen" in errs[-1]["meldung"]


def test_contract_check_documents_stop_limits(setup):
    fake, cfg = setup
    make_observer(fake, cfg)
    doc = PaperStore(cfg.data_root).read("kontraktpruefung.json")
    btc = doc["symbols"]["BTCUSD"]
    eurusd = fake._eurusd()
    price = btc["price"]
    # 2,50 € je 0,01 Lot: (2,50/0,01 − Kommission je Lot) / (Kontraktgröße × USD→EUR)
    comm_lot = 0.0004 * price / eurusd
    expected = (2.5 / 0.01 - comm_lot) / (1.0 / eurusd)
    assert btc["max_stop_distance"] == pytest.approx(expected, rel=1e-4)
    assert btc["risk_eur"] == 2.5 and btc["reference_capital_eur"] == 1000.0
    assert set(btc["by_timeframe"]) == {"M1", "M5", "M15", "M30", "H1"}
    assert btc["spec_differences"] == []
    assert "XAUUSD" in doc["symbols"]


def test_cli_once_and_status_never_send_orders(setup, capsys):
    fake, cfg = setup
    cfg_file = cfg.data_root.parent / "config.toml"
    cfg_file.write_text('[data]\nroot = "data"\n[paper]\nwarmup_bars = 600\n', encoding="utf-8")
    fake.now_utc += pd.Timedelta(minutes=5)
    rc = beobachtung_ausfuehren.main(["--config", str(cfg_file), "--einmal"], client=MT5Client(fake, retry_sleep=0),
                                     now_utc_fn=lambda: fake.now_utc)
    assert rc == 0
    assert not [c for c in fake.calls if "order" in str(c[0])]
    assert beobachtung_ausfuehren.main(["--config", str(cfg_file), "--status"]) == 0
    out = capsys.readouterr().out
    assert "Letzter Durchlauf" in out and "BTCUSD_H1_kombi_breakout_momentum" in out


def test_reference_account_mapping_when_trades_are_executable(setup):
    """Nur im Test: höheres Risiko, damit Trades ausführbar sind und die Zuordnung geprüft wird.
    (Im Programm ist die Risikoregel fest 0,25 % – siehe test_risk_rules_are_fixed.)"""
    from tradingbot.backtest.account import RiskRules
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    obs.rules = RiskRules(risk_per_trade=0.05)
    run_steps(obs, fake, "2026-03-09 00:00")
    doc = PaperStore(cfg.data_root).read("trades.json")
    ex = [t for c in doc["kandidaten"].values() for t in c["trades"]
          if t["status"] == "geschlossen" and t["referenzkonto"]["ausgefuehrt"]]
    assert ex
    for t in ex:   # Ergebnis im Referenzkonto = Ergebnis je 0,01 Lot × Volumen/0,01 (bis auf Rundung)
        assert t["referenzkonto"]["netto_eur"] == pytest.approx(t["netto_eur"] * t["referenzkonto"]["volumen"] / 0.01,
                                                                abs=0.05 * t["referenzkonto"]["volumen"] / 0.01)


def test_risk_rules_are_fixed():
    from tradingbot.backtest.account import RiskRules
    from tradingbot.config import load_config
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "config.toml").write_text("[paper]\nrisk_per_trade = 0.01\n", encoding="utf-8")
    with pytest.raises(ValueError):                      # Risiko ist über die Konfiguration nicht änderbar
        load_config(d / "config.toml")
    obs = Observer(Config(), client=None)
    assert obs.rules == RiskRules() and obs.rules.risk_per_trade == 0.0025 and obs.rules.initial == 1000.0


def test_missing_conversion_rate_gives_no_euro_values(setup):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    obs._eur_rates = lambda now_srv: None                  # EURUSD nicht verfügbar
    run_steps(obs, fake, "2026-03-08 00:00")
    doc = PaperStore(cfg.data_root).read("trades.json")
    trades = [t for c in doc["kandidaten"].values() for t in c["trades"]]
    assert trades and all(t.get("euro_nicht_verfuegbar") and "netto_eur" not in t for t in trades)
    errs = [e for e in PaperStore(cfg.data_root).events() if e.get("bereich") == "umrechnung"]
    keys = [e["kandidat"] for e in errs]
    with_trades = {k for k, c in doc["kandidaten"].items() if c["trades"]}
    assert set(keys) == with_trades and len(keys) == len(set(keys))   # je betroffenem Kandidaten einmal gemeldet


# ---------------------------------------------------------------------- unbeaufsichtigter Betrieb (START/STOP.bat)
def _cfg_file(cfg):
    f = cfg.data_root.parent / "config.toml"
    f.write_text('[data]\nroot = "data"\n[paper]\nwarmup_bars = 600\n', encoding="utf-8")
    return f


def test_second_instance_is_refused(setup, capsys):
    fake, cfg = setup
    lock = beobachtung_ausfuehren.single_instance(cfg.data_root / "paper" / "beobachtung.lock")
    assert lock is not None
    rc = beobachtung_ausfuehren.main(["--config", str(_cfg_file(cfg)), "--einmal"],
                                     client=MT5Client(fake, retry_sleep=0), now_utc_fn=lambda: fake.now_utc)
    assert rc == 3 and "läuft bereits" in capsys.readouterr().out
    lock.close()
    assert beobachtung_ausfuehren.single_instance(cfg.data_root / "paper" / "beobachtung.lock") is not None


def test_stop_request_ends_loop_cleanly(setup):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        fake.now_utc += pd.Timedelta(seconds=s)
        if len(sleeps) == 150:                       # nach gut zwei Durchläufen: STOP.bat
            (cfg.data_root / "paper" / "stop.anfrage").write_text("")
    obs.run(60, sleep=fake_sleep)
    assert obs.cycles == 3 and not (cfg.data_root / "paper" / "stop.anfrage").exists()


def test_cycle_error_does_not_end_observation(setup, monkeypatch):
    fake, cfg = setup
    obs = make_observer(fake, cfg)
    real, calls = obs.cycle, []

    def flaky():
        calls.append(1)
        if len(calls) == 2:
            raise ConnectionError("Terminal kurz weg")
        return real()
    monkeypatch.setattr(obs, "cycle", flaky)
    obs.run(60, max_cycles=4, sleep=lambda s: None)
    assert len(calls) == 4
    assert any(e.get("bereich") == "durchlauf" for e in PaperStore(cfg.data_root).events())


def test_waits_for_mt5_when_terminal_not_ready(setup, capsys):
    fake, cfg = setup
    real_init, tries = fake.initialize, []

    def init(**kw):
        tries.append(1)
        return len(tries) >= 3 and real_init(**kw)       # erst beim dritten Versuch bereit
    fake.initialize = init
    rc = beobachtung_ausfuehren.main(["--config", str(_cfg_file(cfg)), "--intervall", "60"],
                                     client=MT5Client(fake, retry_sleep=0), now_utc_fn=lambda: fake.now_utc,
                                     sleep=lambda s: (cfg.data_root / "paper" / "stop.anfrage").write_text("")
                                     if len(tries) >= 3 else None)
    out = capsys.readouterr().out
    assert rc == 0 and len(tries) == 3 and out.count("MT5 noch nicht bereit") == 2
