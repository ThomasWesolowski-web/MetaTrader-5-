"""Day-Trader-Skills für MT5: Scanner, Plan, Zeitmaschine, Risikoprüfung, Freigabe und Orders (simuliertes MT5)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import daytrader
from test_execution import Pos, TradingFake
from test_paper import write_specs
from tradingbot.config import Config
from tradingbot.daytrader import plan as planmod
from tradingbot.daytrader import scan as scanmod
from tradingbot.daytrader import ticket as tk
from tradingbot.daytrader import timemachine as tm
from tradingbot.daytrader.config import DayTraderConfig, Session, load_daytrader_config
from tradingbot.daytrader.data import Context
from tradingbot.execution import MAGIC, MAGIC_DAYTRADER
from tradingbot.mt5_client import MT5Client
from tradingbot.paper import PaperStore

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-03-03 16:00"          # Dienstag, 11:00 New York: Gold-Range (08:20–08:35) fertig, Einstiegsfenster offen


@pytest.fixture(scope="module")
def market():
    return TradingFake(now_utc=NOW, days=80)


@pytest.fixture
def ctx(market, tmp_path):
    market.positions, market.sent, market.trade_mode, market.algo = {}, [], 0, True
    market.now_utc = pd.Timestamp(NOW, tz="UTC")
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    write_specs(cfg.data_root, market)
    return Context(cfg, DayTraderConfig(), MT5Client(market, retry_sleep=0), pd.Timestamp(NOW, tz="UTC"))


def later(ctx, minutes):
    ctx.now_utc = ctx.now_utc + pd.Timedelta(minutes=minutes)


def ready_ticket(ctx, **plan_kw):
    kw = dict(direction=1, stop_rule="atr")
    kw.update(plan_kw)
    planmod.make_plan(ctx, "XAUUSD", **kw)
    return tk.build_ticket(ctx)


# ---------------------------------------------------------------------- Einstellungen
def test_repo_config_loads_and_validates(tmp_path):
    cfg = load_daytrader_config(ROOT / "daytrader.toml")
    assert cfg.symbols == ["XAUUSD", "BTCUSD"] and cfg.timeframe == "M5"
    assert cfg.risk.risk_pct == 1.0 and cfg.risk.max_positions == 1
    g = cfg.session("XAUUSD")
    assert (g.tz, g.hhmm(g.start), g.range_min, g.hhmm(g.flat_by)) == ("America/New_York", "08:20", 15, "16:30")
    bad = tmp_path / "d.toml"
    bad.write_text('[sitzungen.GER40]\nzeitzone = "Europe/Berlin"\nstart = "09:00"\neinstieg_bis = "08:00"\n'
                   'glattstellen = "17:25"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="start"):
        load_daytrader_config(bad)
    bad.write_text('[risiko]\nrisiko_je_trade_pct = 20\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_daytrader_config(bad)
    with pytest.raises(ValueError, match="sitzungen"):
        DayTraderConfig().session("GER40")


# ---------------------------------------------------------------------- Scanner
def _day(rows, tf=5, start="2026-03-02 08:20"):
    t = pd.date_range(pd.Timestamp(start, tz="America/New_York"), periods=len(rows), freq=f"{tf}min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["time"] = t.tz_convert("UTC")
    df["mins"] = t.hour * 60 + t.minute
    return df


def test_orb_state_waits_triggers_and_expires():
    s = Session("x", "America/New_York", 8 * 60 + 20, 15, 9 * 60, 16 * 60)
    rng = [(100, 101, 99, 100)] * 3                                    # Range 99–101
    inside = [(100, 100.5, 99.5, 100.2)] * 3
    up = [(100.5, 101.6, 100.4, 101.4)]
    assert orb(s, rng, 8 * 60 + 30)["zustand"] == "wartet"            # Range noch nicht fertig
    st = orb(s, rng + inside + up, 8 * 60 + 60)
    assert st["zustand"] == "ausgelöst" and st["richtung"] == 1 and st["kurs"] == 101.4
    assert st["range_hoch"] == 101 and st["range_tief"] == 99 and st["range_mitte"] == 100
    assert orb(s, rng + inside * 3, 9 * 60 + 5)["zustand"] == "abgelaufen"
    assert orb(s, rng[:2], 8 * 60 + 40)["zustand"] == "keine_daten"   # Kerze der Range fehlt


def orb(s, rows, now_min):
    return scanmod.orb_state(_day(rows), s, 5, now_min)


def test_scan_reports_all_symbols_and_writes_file(ctx):
    res = scanmod.scan(ctx)
    assert [s["symbol"] for s in res["symbole"]] == ["XAUUSD", "BTCUSD"]
    for s in res["symbole"]:
        assert s["orb"]["zustand"] in ("wartet", "ausgelöst", "abgelaufen", "keine_daten")
        assert "rsi" in s and "vortag" in s and s["atr14"] > 0
        assert pd.Timestamp(s["letzte_kerze_utc"]) + pd.Timedelta(minutes=5) <= ctx.now()   # nur fertige Kerzen
    assert json.loads((ctx.dir / "scan.json").read_text(encoding="utf-8"))["zeitrahmen"] == "M5"
    assert "Opening Range" in scanmod.format_scan(res)


# ---------------------------------------------------------------------- Plan
def test_plan_atr_long_uses_ask_and_r_target(ctx):
    p = planmod.make_plan(ctx, "XAUUSD", direction=1, stop_rule="atr", atr_mult=1.5, target_r=2.0)
    tick = ctx.client.tick("XAUUSD")
    assert p["einstieg"] == round(tick["ask"], 2) and p["einstieg_quelle"].startswith("MT5")
    assert p["stop"] < p["einstieg"] < p["ziel"]
    assert p["ziel"] == pytest.approx(p["einstieg"] + 2 * (p["einstieg"] - p["stop"]), abs=0.02)
    assert p["sitzung"]["glattstellen"] == "16:30" and p["tag"] == "2026-03-03"
    assert (ctx.dir / "plan.json").exists()


def test_plan_orb_follows_signal_and_needs_direction_otherwise(ctx):
    sig = scanmod.scan(ctx)["symbole"][0]["orb"]
    if sig["zustand"] == "ausgelöst":
        p = planmod.make_plan(ctx, "XAUUSD")
        assert p["richtung"] == sig["richtung"] and p["stop"] == sig["range_mitte"]
    with pytest.raises(ValueError, match="Richtung"):
        planmod.make_plan(ctx, "XAUUSD", setup="rsi")
    with pytest.raises(ValueError, match="Stop-Regel"):
        planmod.make_plan(ctx, "XAUUSD", direction=1, stop_rule="irgendwas")


# ---------------------------------------------------------------------- Zeitmaschine
def test_timemachine_is_reproducible_and_consistent(ctx):
    planmod.make_plan(ctx, "XAUUSD", direction=1, stop_rule="atr")
    a = tm.run(ctx, replays=2000, seed=19)
    b = tm.run(ctx, replays=2000, seed=19)
    mc = a["zeitmaschine"]
    assert mc == b["zeitmaschine"]
    assert mc["ziel_zuerst"] + mc["stop_zuerst"] + mc["offen_bei_glattstellen"] == pytest.approx(1, abs=1e-3)
    assert mc["mittel_r_netto"] == pytest.approx(mc["mittel_r_brutto"] - mc["kosten_r"], abs=2e-3)
    h = a["geschichte"]["gleiche_uhrzeit"]
    assert h["ziel"] + h["stop"] + h["offen"] == h["ausgeloest"] == a["geschichte"]["vollstaendige_tage"] >= 10
    assert "gleiche_regel" in a["geschichte"]
    assert (ctx.dir / "zeitmaschine.json").exists() and "Urteil" in tm.format_run(a)


def test_timemachine_walk_counts_stop_before_target_on_same_bar():
    o = h = l = c = np.array([[100.0, 100.0]])
    h = np.array([[106.0, 100.0]])
    l = np.array([[94.0, 100.0]])
    res, _ = tm._walk(o, h, l, c, 1, 100.0, 95.0, 105.0, 0.0)
    assert res[0] == -1
    res, _ = tm._walk(o, h, np.array([[99.0, 99.0]]), c, 1, 100.0, 95.0, 105.0, 0.0)
    assert res[0] == 1


def test_verdict_uses_break_even_of_target_r():
    assert tm.verdict(0.5, 0.5, 1.0) == "Nahe am Münzwurf"
    assert tm.verdict(0.40, 0.60, 1.5) == "Nahe an der Gewinnschwelle"
    assert tm.verdict(0.60, 0.40, 1.0) == "Neigt zum Ziel"
    assert tm.verdict(0.10, 0.90, 1.0) == "Deutlich zum Stop"


# ---------------------------------------------------------------------- Ticket und Risikoprüfung
def test_ticket_pass_sizes_from_risk_and_rounds_down(ctx):
    t = ready_ticket(ctx)
    assert t["urteil"] == "PASS", t["gruende"]
    o = t["order"]
    assert o["volumen"] >= 0.01 and t["risiko"] <= t["risiko_erlaubt"] == 10.0
    assert o["volumen"] == pytest.approx(np.floor(10.0 / t["verlust_je_lot"] / 0.01 + 1e-9) * 0.01)
    assert t["fingerabdruck"] == tk.fingerprint(o)
    assert "PASS" in tk.format_ticket(t)


@pytest.mark.parametrize("setup, reason", [
    ("echtgeld", "Demokonto"), ("spaet", "Einstiegsfensters"), ("alt", "Minuten alt"),
    ("bot_stopp", "Handelsstopp"), ("trades", "Day-Trades"), ("position", "Position"),
])
def test_ticket_blocks(ctx, market, setup, reason):
    planmod.make_plan(ctx, "XAUUSD", direction=1, stop_rule="atr")
    if setup == "echtgeld":
        market.trade_mode = 2
    elif setup == "spaet":
        ctx.now_utc = pd.Timestamp("2026-03-03 16:45", tz="UTC")    # 11:45 New York > einstieg_bis 11:30
    elif setup == "alt":
        later(ctx, 31)
    elif setup == "bot_stopp":
        PaperStore(ctx.cfg.data_root).save_state({"execution": {"halted": True, "halt_reason": "Drawdown 21 %"}})
    elif setup == "trades":
        for _ in range(3):
            ctx.log("order_gesendet", tag="2026-03-03", risiko=1.0)
    elif setup == "position":
        market.positions[7] = Pos(7, "BTCUSD", 0, 0.01, 1.0, 0.5, 2.0, MAGIC_DAYTRADER, "DT")
    t = tk.build_ticket(ctx)
    assert t["urteil"] == "BLOCK" and any(reason in g for g in t["gruende"]), t["gruende"]


def test_ticket_without_mt5_is_preview_only(ctx):
    planmod.make_plan(ctx, "XAUUSD", direction=1, stop_rule="atr")
    ctx.client = None
    t = tk.build_ticket(ctx, balance=1000.0)
    assert t["urteil"] == "BLOCK" and any("nicht verbunden" in g for g in t["gruende"])


def test_daily_risk_limit_counts_todays_trades(ctx):
    for _ in range(2):
        ctx.log("order_gesendet", tag="2026-03-03", risiko=12.0)        # 24 € von 30 € (3 %) verbraucht
    t = ready_ticket(ctx)
    assert t["urteil"] == ("BLOCK" if t["risiko"] > 6 else "PASS")


# ---------------------------------------------------------------------- Freigabe
@pytest.mark.parametrize("reply, ok", [
    ("ja", True), ("Ja.", True), ("yes", True), ("ja, XAUUSD kaufen", True), ("Ja, 0.01 Lot", True),
    ("ja zu allen", False), ("ja für alle", False), ("ja?", False), ("sure", False), ("ok", False), ("nein", False),
    ("ja, aber später", False), ("ja BTCUSD", False), ("ja 0.5 Lot", False), ("jein", False),
])
def test_reply_must_be_explicit_yes_for_this_ticket(reply, ok):
    t = {"order": {"symbol": "XAUUSD", "volumen": 0.01, "stop": 2531.21, "ziel": 2545.81}, "kurs_jetzt": 2537.05}
    assert tk.check_reply(reply, t)[0] is ok


def test_block_ticket_cannot_be_approved(ctx, market):
    market.trade_mode = 2
    ready_ticket(ctx)
    assert tk.approve(ctx, "ja")["ok"] is False


# ---------------------------------------------------------------------- Senden und Schließen
def test_send_needs_yes_and_sends_exactly_one_demo_order(ctx, market):
    t = ready_ticket(ctx)
    r = tk.send(ctx)
    assert r["ok"] is False and "kein Ja" in r["meldung"] and market.sent == []
    assert tk.approve(ctx, "ja")["ok"]
    r = tk.send(ctx)
    assert r["ok"], r
    req = market.sent[0]
    assert req["magic"] == MAGIC_DAYTRADER and req["volume"] == t["order"]["volumen"]
    assert req["sl"] == t["order"]["stop"] and req["tp"] == t["order"]["ziel"] and req["type"] == market.ORDER_TYPE_BUY
    again = tk.send(ctx)
    assert again["ok"] is False and len(market.sent) == 1                    # ein Ja, eine Order
    assert any(e["art"] == "order_gesendet" for e in ctx.events())


def test_send_refuses_old_yes_changed_ticket_and_real_account(ctx, market):
    ready_ticket(ctx)
    tk.approve(ctx, "ja")
    later(ctx, 11)
    assert "älter" in tk.send(ctx)["meldung"]
    later(ctx, -11)
    t = json.loads((ctx.dir / "ticket.json").read_text(encoding="utf-8"))
    t["order"]["volumen"] = 1.0                                              # nach der Freigabe verändert
    ctx.write("ticket.json", t)
    assert "verändert" in tk.send(ctx)["meldung"]
    ready_ticket(ctx)
    tk.approve(ctx, "ja")
    market.trade_mode = 2                                                    # Konto nach der Freigabe gewechselt
    r = tk.send(ctx)
    assert r["ok"] is False and "Demokonto" in r["meldung"] and market.sent == []


def test_close_only_touches_day_trader_positions(ctx, market):
    market.positions[1] = Pos(1, "XAUUSD", 0, 0.01, 2500.0, 2490.0, 2520.0, MAGIC, "TB bot")
    market.positions[2] = Pos(2, "XAUUSD", 0, 0.01, 2500.0, 2490.0, 2520.0, MAGIC_DAYTRADER, "DT")
    assert tk.close(ctx, 1)["ok"] is False and 1 in market.positions
    assert tk.close(ctx, 2)["ok"] is True and 2 not in market.positions
    st = tk.status(ctx)
    assert st["bot_positionen"] == 1 and st["day_trader_positionen"] == []


def test_cli_runs_full_flow(market, tmp_path, capsys):
    market.positions, market.sent, market.trade_mode = {}, [], 0
    (tmp_path / "config.toml").write_text('[data]\nroot = "data"\n', encoding="utf-8")
    write_specs(tmp_path / "data", market)
    base = ["--jetzt", "2026-03-03 17:00", "--config", str(tmp_path / "config.toml"),
            "--daytrader-config", str(ROOT / "daytrader.toml")]
    cl = MT5Client(market, retry_sleep=0)
    for cmd in (["scannen"], ["planen", "XAUUSD", "--richtung", "long", "--stop", "atr"],
                ["zeitmaschine", "--laeufe", "500"], ["ticket"], ["freigeben", "--antwort", "ja"], ["senden"],
                ["status"]):
        assert daytrader.run(base + cmd, client=cl) == 0, cmd
    assert len(market.sent) == 1 and "Order gesendet (Demo)" in capsys.readouterr().out


# ---------------------------------------------------------------------- Claude-Skills
def test_skills_reference_existing_commands_and_ask_before_orders():
    skills = sorted((ROOT / ".claude" / "skills").glob("mt5-*/SKILL.md"))
    assert len(skills) >= 5
    cmds = set(daytrader.build_parser()._subparsers._group_actions[0].choices)
    for f in skills:
        text = f.read_text(encoding="utf-8")
        assert text.startswith("---\nname: " + f.parent.name + "\n")
        for c in __import__("re").findall(r"python daytrader\.py (\w+)", text):
            assert c in cmds, (f, c)
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    ask = " ".join(settings["permissions"]["ask"])
    assert "daytrader.py senden" in ask and "daytrader.py schliessen" in ask
    assert not any("senden" in a or "schliessen" in a for a in settings["permissions"].get("allow", []))
