"""Gold-Bot: Erkennung (Trend, Order Block, Liquidität, Plan A/B), Orders über ein simuliertes MT5, Stop auf Einstieg,
eigene Grenzen, nur Demokonto, KI-Prüfung, Telegram-Briefkasten, Rückblick."""

from collections import namedtuple

import pandas as pd
import pytest

from tradingbot import meldungen
from tradingbot.execution import MAGIC, MAGIC_GOLDBOT, cancel_pending_order, send_pending_order
from tradingbot.goldbot import GoldBot, GoldConfig, GoldStore, analyse, load
from tradingbot.goldbot.ki import make_checker, prompt
from tradingbot.goldbot.rueckblick import simulate, summary
from tradingbot.goldbot.strategie import liquidity, order_blocks, swings, trend
from tradingbot.indicators import atr

T0 = 1_764_000_000                    # Serverzeit der ersten Kerze
NOW = pd.Timestamp("2026-11-25 10:00", tz="UTC")   # Mittwoch, innerhalb der Handelszeit


def build(points, start=3600.0, wick=0.3):
    rows, p, t = [], start, T0
    for target, nb in points:
        step = (target - p) / nb
        for _ in range(nb):
            o, c = p, p + step
            rows.append((t, o, max(o, c) + wick, min(o, c) - wick, c))
            p, t = c, t + 900
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


BASE = [(3602, 2), (3598, 2)] * 15
DOWN = BASE + [(3600, 2), (3560, 16), (3585, 10), (3530, 4), (3545, 6), (3532, 3), (3514, 1), (3528.5, 1)]


def down_df():
    return build(DOWN)


def plan_b_df():
    df = down_df()
    df.loc[len(df) - 1, ["high", "low", "close"]] = [3529.6, 3513.7, 3522.0]
    return df


def up_df():
    df = down_df()
    out = df.copy()
    for col_new, col_old in (("open", "open"), ("close", "close")):
        out[col_new] = 7200 - df[col_old]
    out["high"], out["low"] = 7200 - df["low"], 7200 - df["high"]
    return out


# ---------------------------------------------------------------------- Erkennung
def test_trend_swings_order_block_and_liquidity():
    df, cfg = down_df(), GoldConfig()
    h, l_, c = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()
    sh, sl = swings(h, l_, cfg.swing_k)
    d, text = trend(c, sh, sl)
    assert d == -1 and "abwärts" in text
    obs = order_blocks(df, atr(h, l_, c, 14), -1, cfg)
    fresh = [ob for ob in obs if not ob.touched and not ob.broken]
    assert fresh and fresh[0].lo == pytest.approx(3542.2) and fresh[0].hi == pytest.approx(3545.3)
    sells, buys = liquidity(df, sh, sl, 6.5, cfg)
    assert sells[0].price == pytest.approx(3513.7)          # letztes Tief seit dem letzten Hoch
    assert [round(b.price, 1) for b in buys] == [3545.3, 3585.3]


def test_plan_a_sell_limit_in_bear_order_block():
    an = analyse(down_df(), GoldConfig())
    a = next(s for s in an.setups if s.plan == "A")
    assert a.direction == -1 and a.entry == pytest.approx(3543.75)
    assert a.stop > 3545.3                                      # knapp über dem Order Block
    assert a.ziel1 < 3530 and a.ziel2 < a.ziel1 and a.rr1 >= 1
    assert not [s for s in an.setups if s.plan == "B"]         # Retest-Kerze zu klein -> Stop zu eng


def test_plan_b_retest_after_close_below_key_low():
    an = analyse(plan_b_df(), GoldConfig())
    b = next(s for s in an.setups if s.plan == "B")
    assert b.direction == -1 and b.entry is None and b.level == pytest.approx(3529.7)
    assert b.stop > 3529.6 and b.ziel1 < 3522 and b.rr1 == pytest.approx(1.5, abs=0.01)


def test_uptrend_is_mirrored():
    an = analyse(up_df(), GoldConfig())
    assert an.trend == 1
    a = next(s for s in an.setups if s.plan == "A")
    assert a.direction == 1 and a.entry == pytest.approx(7200 - 3543.75) and a.stop < a.entry < a.ziel1 < a.ziel2


def test_no_trade_without_trend_or_after_structure_break():
    df = down_df()
    flat = build(BASE * 3)
    assert analyse(flat, GoldConfig()).setups == []
    # Schluss über dem letzten Hoch (3545.3) bricht den Abwärtstrend -> kein Verkauf
    broken = build(DOWN + [(3550, 4)])
    an = analyse(broken, GoldConfig())
    assert an.trend == 0 and an.setups == []
    assert analyse(df.head(40), GoldConfig()).setups == []


def test_analysis_uses_only_given_bars():
    df = plan_b_df()
    full = analyse(build(DOWN + [(3500, 6)]).head(len(df) - 1), GoldConfig())
    part = analyse(df.head(len(df) - 1), GoldConfig())
    assert [s.key for s in full.setups] == [s.key for s in part.setups]


# ---------------------------------------------------------------------- simuliertes MT5
Acc = namedtuple("Acc", "login trade_mode balance equity margin_free currency")
Term = namedtuple("Term", "trade_allowed")
Info = namedtuple("Info", "name digits point volume_min volume_max volume_step trade_tick_size trade_tick_value "
                          "trade_stops_level filling_mode")
Tick = namedtuple("Tick", "bid ask")
Pos = namedtuple("Pos", "ticket identifier symbol type volume price_open sl tp magic comment profit")
Order = namedtuple("Order", "ticket symbol type volume_current price_open sl tp magic comment")
Res = namedtuple("Res", "retcode order price comment")
Deal = namedtuple("Deal", "entry profit commission swap")


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 0, 1, 2, 3
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 5, 6, 8
    ORDER_TIME_GTC, TIMEFRAME_M15 = 0, 15
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, df, trade_mode=0, login=2100405946, balance=10000.0):
        self.df, self.trade_mode, self.login, self.balance = df, trade_mode, login, balance
        self.bid, self.algo = float(df.close.iloc[-1]), True
        self.orders, self.positions, self.deals, self.sent, self.next = {}, {}, {}, [], 900

    def account_info(self):
        return Acc(self.login, self.trade_mode, self.balance, self.balance, self.balance, "USD")

    def terminal_info(self):
        return Term(self.algo)

    def symbol_select(self, s, on=True):
        return True

    def symbol_info(self, s):
        return Info("XAUUSD", 2, 0.01, 0.01, 100.0, 0.01, 0.01, 1.0, 0, 1) if s == "XAUUSD" else None

    def symbol_info_tick(self, s):
        return Tick(self.bid, round(self.bid + 0.2, 2))

    def copy_rates_from_pos(self, s, tf, start, n):
        assert start == 1                                   # nie die laufende Kerze
        return self.df.tail(n).to_records(index=False)

    def order_calc_profit(self, otype, s, vol, price, close):
        return (close - price) * 100 * vol * (1 if otype == 0 else -1)

    def order_calc_margin(self, otype, s, vol, price):
        return price * 100 * vol / 500

    def orders_get(self):
        return tuple(self.orders.values())

    def positions_get(self, symbol=None):
        return tuple(self.positions.values())

    def history_deals_get(self, *a, position=None, **kw):
        return tuple(self.deals.get(position, ()))

    def last_error(self):
        return (1, "ok")

    def order_check(self, req):
        return Res(0, 0, 0, "ok")

    def order_send(self, req):
        self.sent.append(req)
        self.next += 1
        if req["action"] == self.TRADE_ACTION_PENDING:
            self.orders[self.next] = Order(self.next, req["symbol"], req["type"], req["volume"], req["price"],
                                           req["sl"], req["tp"], req["magic"], req["comment"])
            return Res(10008, self.next, req["price"], "placed")
        if req["action"] == self.TRADE_ACTION_REMOVE:
            self.orders.pop(req["order"], None)
            return Res(10009, req["order"], 0, "done")
        if req["action"] == self.TRADE_ACTION_SLTP:
            p = self.positions[req["position"]]
            self.positions[req["position"]] = p._replace(sl=req["sl"], tp=req["tp"])
            return Res(10009, 0, 0, "done")
        if req.get("position"):
            self.positions.pop(req["position"], None)
            return Res(10009, self.next, req["price"], "done")
        self.positions[self.next] = Pos(self.next, self.next, req["symbol"], req["type"], req["volume"], req["price"],
                                        req["sl"], req["tp"], req["magic"], req["comment"], 0.0)
        return Res(10009, self.next, req["price"], "done")

    # Hilfen für die Tests
    def fill_all(self):
        for t, o in list(self.orders.items()):
            self.positions[t] = Pos(t, t, o.symbol, 1 if o.type == 3 else 0, o.volume_current, o.price_open, o.sl,
                                    o.tp, o.magic, o.comment, 0.0)
            del self.orders[t]

    def close(self, ticket, profit):
        self.positions.pop(ticket)
        self.deals[ticket] = [Deal(0, 0, 0, 0), Deal(1, profit, 0, 0)]


def make(tmp_path, df, mt5=None, ai=None, **kw):
    cfg = GoldConfig(data_root=str(tmp_path), **kw)
    mt5 = mt5 or FakeMT5(df)
    return GoldBot(mt5, cfg, GoldStore(tmp_path), log=lambda *_: None, ai=ai), mt5


def test_plan_a_places_two_sell_limits_then_breakeven_after_target1(tmp_path):
    bot, mt5 = make(tmp_path, down_df())
    bot.tick(NOW)
    lim = [r for r in mt5.sent if r["action"] == mt5.TRADE_ACTION_PENDING]
    assert len(lim) == 2 and all(r["type"] == mt5.ORDER_TYPE_SELL_LIMIT and r["magic"] == MAGIC_GOLDBOT for r in lim)
    assert all(r["volume"] == 0.01 and r["price"] == 3543.75 for r in lim)
    tr = bot.st["aktiv"]
    assert [r["tp"] for r in lim] == [tr["ziel1"], tr["ziel2"]] and tr["status"] == "wartend"
    mt5.fill_all()
    bot.tick(NOW)
    assert bot.st["aktiv"]["status"] == "offen"
    t1, t2 = (leg["position"] for leg in bot.st["aktiv"]["legs"])
    mt5.close(t1, 29.0)                                       # Teil 1 am Ziel 1
    mt5.bid = 3530.0
    bot.tick(NOW)
    assert mt5.positions[t2].sl == 3543.75                     # Stop von Teil 2 auf Einstieg
    assert bot.st["aktiv"]["be"]
    mt5.close(t2, 0.0)
    bot.tick(NOW)
    assert bot.st["aktiv"] is None and bot.st["trades"][-1]["ergebnis"] == 29.0
    assert bot.st["konto"]["kum"] == 29.0
    texts = meldungen.collect(tmp_path)
    assert any("ORDER Plan A" in t for t in texts) and any("Stop von Teil 2 auf Einstieg" in t for t in texts)


def test_single_part_moves_stop_when_price_reaches_target1(tmp_path):
    bot, mt5 = make(tmp_path, down_df(), parts=1)
    bot.tick(NOW)
    lim = [r for r in mt5.sent if r["action"] == mt5.TRADE_ACTION_PENDING]
    assert len(lim) == 1 and lim[0]["tp"] == bot.st["aktiv"]["ziel2"]
    mt5.fill_all()
    bot.tick(NOW)
    (t,) = (leg["position"] for leg in bot.st["aktiv"]["legs"])
    mt5.bid = bot.st["aktiv"]["ziel1"] - 1
    bot.tick(NOW)
    assert mt5.positions[t].sl == 3543.75


def test_pending_cancelled_when_target1_reached_or_expired(tmp_path):
    bot, mt5 = make(tmp_path, down_df())
    bot.tick(NOW)
    mt5.bid = bot.st["aktiv"]["ziel1"] - 2                     # Kurs läuft ohne uns weiter
    bot.tick(NOW)
    assert not mt5.orders and bot.st["aktiv"] is None
    assert bot.st["trades"][-1]["status"] == "geloescht"
    # gleiches Setup wird nicht noch einmal gehandelt
    n = len(mt5.sent)
    bot.st["letzte_kerze"] = None
    mt5.bid = 3528.5
    bot.tick(NOW)
    assert len(mt5.sent) == n

    bot2, mt52 = make(tmp_path / "b", down_df(), pending_valid_bars=2)
    bot2.tick(NOW)
    extra = build(DOWN + [(3527, 2)]).tail(2)
    mt52.df = pd.concat([mt52.df, extra.assign(time=extra.time)], ignore_index=True)
    bot2.tick(NOW)
    assert not mt52.orders and bot2.st["trades"][-1]["status"] == "geloescht"


def test_plan_b_market_orders(tmp_path):
    bot, mt5 = make(tmp_path, plan_b_df(), plan_a=False)
    bot.tick(NOW)
    deals = [r for r in mt5.sent if r["action"] == mt5.TRADE_ACTION_DEAL]
    assert len(deals) == 2 and all(r["type"] == mt5.ORDER_TYPE_SELL for r in deals)
    assert bot.st["aktiv"]["status"] == "offen" and len(mt5.positions) == 2


def test_only_demo_account_and_configured_login(tmp_path):
    bot, mt5 = make(tmp_path, down_df(), mt5=FakeMT5(down_df(), trade_mode=2, login=2746301))
    bot.tick(NOW)
    assert mt5.sent == [] and bot.st["aktiv"] is None
    assert bot.st["gehandelt"] == []                           # Setup bleibt für ein Demokonto erhalten
    bot2, mt52 = make(tmp_path / "x", down_df(), demo_login=123)
    bot2.tick(NOW)
    assert mt52.sent == []
    r = send_pending_order(FakeMT5(down_df(), trade_mode=2), "XAUUSD", -1, 0.01, 3550, 3560, 3500, 2)
    assert not r["ok"] and "Demokonto" in r["meldung"]


def test_pending_order_checks_side_and_owner(tmp_path):
    mt5 = FakeMT5(down_df())
    r = send_pending_order(mt5, "XAUUSD", -1, 0.01, 3520.0, 3530.0, 3500.0, 2)   # Verkaufs-Limit unter dem Kurs
    assert not r["ok"] and "nicht über" in r["meldung"]
    r = send_pending_order(mt5, "XAUUSD", -1, 0.01, 3540.0, 3530.0, 3500.0, 2)   # Stop auf der falschen Seite
    assert not r["ok"]
    mt5.orders[1] = Order(1, "XAUUSD", 3, 0.01, 3540, 3550, 3500, MAGIC, "TB")
    assert not cancel_pending_order(mt5, 1)["ok"] and 1 in mt5.orders            # fremde Order bleibt


def test_risk_window_trades_per_day_and_spread(tmp_path):
    bot, mt5 = make(tmp_path, down_df(), mt5=FakeMT5(down_df(), balance=100.0), max_risk_pct=3.0)
    bot.tick(NOW)                                              # 2 x 0,01 Lot x 3,5 USD = 7 USD > 3 USD
    assert mt5.sent == [] and "riskieren" in bot.store.events()[-1]["grund"]
    bot2, mt52 = make(tmp_path / "n", down_df())
    bot2.tick(pd.Timestamp("2026-11-25 22:00", tz="UTC"))     # außerhalb der Handelszeit
    assert mt52.sent == [] and bot2.st["gehandelt"] == []
    bot3, mt53 = make(tmp_path / "s", down_df(), max_spread_points=10)
    bot3.tick(NOW)
    assert mt53.sent == []
    bot4, mt54 = make(tmp_path / "f", down_df())
    bot4.tick(pd.Timestamp("2026-11-27 19:00", tz="UTC"))     # Freitagabend
    assert mt54.sent == []


def test_own_limits_ignore_other_bots(tmp_path):
    df = down_df()
    mt5 = FakeMT5(df)
    mt5.positions[5] = Pos(5, 5, "XAUUSD", 0, 1.0, 3600, 3500, 0, MAGIC, "TB", -900.0)   # Verlust des Demo-Bots
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "state.json").write_text('{"execution": {"halted": true, "halt_reason": "DD"}}')
    bot, mt5 = make(tmp_path, df, mt5=mt5)
    bot.tick(NOW)
    assert any(r["action"] == mt5.TRADE_ACTION_PENDING for r in mt5.sent)   # trotzdem gehandelt
    k = bot.st["konto"]
    assert not k["tag_gesperrt"] and not k["gestoppt"]


def test_own_drawdown_stops_until_release(tmp_path):
    bot, mt5 = make(tmp_path, down_df(), max_drawdown_pct=1.0, max_daily_loss_pct=10.0)
    bot.st["konto"].update(referenz=10000.0, kum=-150.0, hoechst=0.0)
    bot.tick(NOW)
    assert bot.st["konto"]["gestoppt"] and mt5.sent == []
    (bot.store.dir / "handel_freigeben.anfrage").write_text("x")
    bot.st["letzte_kerze"] = None
    bot.tick(NOW)
    assert not bot.st["konto"]["gestoppt"] and mt5.sent


def test_ai_no_blocks_order_and_yes_allows(tmp_path):
    calls = []

    def no(s, df, an):
        calls.append(s.key)
        return False, "Order Block zu nah an der Gegenbewegung"
    bot, mt5 = make(tmp_path, down_df(), ai=no)
    bot.tick(NOW)
    assert mt5.sent == [] and len(calls) == 1
    bot.st["letzte_kerze"] = None
    bot.tick(NOW)
    assert len(calls) == 1                                     # nicht jede Kerze erneut fragen
    bot2, mt52 = make(tmp_path / "y", down_df(), ai=lambda s, df, an: (True, "sauber"))
    bot2.tick(NOW)
    assert mt52.sent and bot2.st["aktiv"]["ki"] == "sauber"


def test_ai_without_key_rejects_or_trades_by_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    an = analyse(down_df(), GoldConfig())
    s = an.setups[0]
    cfg = GoldConfig(ai_enabled=True, base_dir=tmp_path)
    ok, text = make_checker(cfg, log=lambda *_: None)(s, down_df(), an)
    assert not ok and "kein API-Schlüssel" in text
    cfg.ai_on_error = "handeln"
    assert make_checker(cfg, log=lambda *_: None)(s, down_df(), an)[0]
    assert make_checker(GoldConfig(), log=lambda *_: None) is None
    p = prompt(s, down_df(), an)
    assert "Plan A" in p and "Bear-Order-Block" in p and p.count("\n") > 80
    (tmp_path / "ki_zugang.toml").write_text('api_key = "sk-ant-test"\n')
    assert cfg.api_key() == "sk-ant-test"


def test_config_caps_and_shipped_file():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    cfg = load(root / "goldbot.toml")
    assert cfg.lot == 0.01 and cfg.parts == 2 and cfg.symbol == "XAUUSD"
    assert not cfg.ai_enabled and cfg.ai_model == "claude-opus-5-5"
    assert cfg.ziel1_fallback_rr == 1.5


def test_config_rejects_unknown_and_caps(tmp_path):
    p = tmp_path / "g.toml"
    p.write_text("[handel]\nlot = 5\nteile = 9\nrisiko_je_trade_pct = 50\n")
    c = load(p)
    assert c.lot == 0.10 and c.parts == 2 and c.max_risk_pct == 5.0
    p.write_text("[handel]\nlive_konto = 1\n")
    with pytest.raises(ValueError):
        load(p)


def test_outbox_and_telegram_terminal_path(tmp_path):
    for i in range(55):
        meldungen.write(tmp_path, "goldbot", f"m{i}")
    got = meldungen.collect(tmp_path, limit=100)
    assert len(got) == meldungen.KEEP and got[-1] == "m54" and meldungen.collect(tmp_path) == []
    from tradingbot.telegram import load as tload
    p = tmp_path / "t.toml"
    p.write_text("[telegram]\nterminal_pfad = 'C:\\MT5 Live\\terminal64.exe'\n")
    assert tload(p).terminal_path == "C:\\MT5 Live\\terminal64.exe"


def test_rueckblick_runs_and_counts():
    df = build(DOWN + [(3505, 6), (3540, 8), (3500, 6), (3520, 6), (3490, 6)])
    trades = simulate(df, GoldConfig(), start=60)
    lines = summary(trades)
    assert lines and (trades == [] or "Trades:" in lines[0])
    assert summary([]) == ["Keine abgeschlossenen Trades im Zeitraum."]
