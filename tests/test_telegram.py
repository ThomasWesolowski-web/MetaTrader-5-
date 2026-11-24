"""Telegram-Signale: Texterkennung, Einstieg, Folgenachrichten, Grenzen, Demo/Live-Konto (simuliertes MT5)."""

from collections import namedtuple

import pandas as pd
import pytest

import telegram_signale
from tradingbot.execution import MAGIC, MAGIC_TELEGRAM, account_allowed
from tradingbot.telegram import SignalStore, SignalTrader, load, parse_followup, parse_signal
from tradingbot.telegram.config import TelegramConfig
from tradingbot.telegram.parser import BREAKEVEN, CANCEL, CLOSE, INFO, NEW_STOP, PARTIAL

FX_CULTURE = """FX Culture | Premium FX
SIGNAL ALERT


XAUUSD

SELL

ENTRY AREA 3622-3626

🎯Target: 3560

❌Stop loss: 3642

This is not financial advice. This is a personal trade idea shared for educational and informational purposes only"""

T0 = pd.Timestamp("2026-11-23 13:46", tz="UTC")


# ---------------------------------------------------------------------- Texterkennung
def test_parses_fx_culture_signal():
    s = parse_signal(FX_CULTURE)
    assert (s.symbol, s.direction, s.entry_lo, s.entry_hi, s.stop, s.targets) == ("XAUUSD", -1, 3622, 3626, 3642, [3560])


@pytest.mark.parametrize("text,sym,d,lo,hi,sl,tps", [
    ("GBPUSD BUY @ 1.2650 TP1 1.2700 TP2 1.2750 SL 1.2600", "GBPUSD", 1, 1.265, 1.265, 1.26, [1.27, 1.275]),
    ("GOLD BUY NOW 2650/2645\nTP 2660\nTP 2670\nSL 2640", "XAUUSD", 1, 2645, 2650, 2640, [2660, 2670]),
    ("EUR/USD SELL\nEntry zone: 1.0850 - 1.0860\nTake profit 1: 1.0800\nStop Loss: 1.0900", "EURUSD", -1, 1.085, 1.086,
     1.09, [1.08]),
])
def test_parses_other_formats(text, sym, d, lo, hi, sl, tps):
    s = parse_signal(text)
    assert (s.symbol, s.direction, s.entry_lo, s.entry_hi, s.stop, s.targets) == (sym, d, lo, hi, sl, tps)


@pytest.mark.parametrize("text", [
    "⚠️ Trading Note: Keep an eye on USD, Gold & Indices around 3:45 PM, as both PMI releases may trigger volatility",
    "Well well, taking partials here",
    "XAUUSD SELL ENTRY 3622 Target 3560 Stop loss 3600",        # Stop auf der falschen Seite
    "XAUUSD BUY or SELL? Target 3560 SL 3642",                   # widersprüchlich
    "XAUUSD SELL ENTRY 3622 Stop loss 3642",                     # kein Ziel
    "DEAL FOR EXISTING PREMIUM MEMBERS: 200% deposit bonus €500 → €1500",
])
def test_rejects_non_signals(text):
    assert parse_signal(text) is None


@pytest.mark.parametrize("text,acts", [
    ("Well well, taking partials here", [(PARTIAL, None)]),
    ("and also moving my SL to BE", [(BREAKEVEN, None)]),
    ("Taking partials and moving SL to breakeven", [(PARTIAL, None), (BREAKEVEN, None)]),
    ("Set your stop loss to entry now", [(BREAKEVEN, None)]),
    ("Move SL to 3610", [(NEW_STOP, 3610.0)]),
    ("Closing this one now", [(CLOSE, None)]),
    ("Close half here", [(PARTIAL, None)]),
    ("Cancel this signal, don't enter", [(CANCEL, None)]),
    ("TP hit ✅ closed in profit", [(INFO, None)]),
    ("Fast profits 🚀", []),
    ("And calling it a day", [(CLOSE, None)]),
    ("Closing out 80% +400✅", [(PARTIAL, 80.0)]),
    ("Let’s secure atleast 70% here", [(PARTIAL, 70.0)]),
    ("Removing risk here", [(BREAKEVEN, None)]),
    ("Removing risk on this trade and closing out  -60 pips.", [(BREAKEVEN, None), (CLOSE, None)]),
    ("Closing full +180 ✅✅", [(CLOSE, None)]),
    ("Send me your profits 👉@fxculture", []),
    ("🔥 BEST TRADER OF THE WEEK — MARK WILKES 🏆 He closed the week with +400 pips", []),
    ("If price closes above 3650 we could see more upside", []),
    ("It will be a busy week", []),
])
def test_followups(text, acts):
    assert parse_followup(text) == acts


# ---------------------------------------------------------------------- simuliertes MT5
Acc = namedtuple("Acc", "login trade_mode balance equity margin_free currency")
Term = namedtuple("Term", "trade_allowed")
Info = namedtuple("Info", "name digits point volume_min volume_max volume_step trade_tick_size trade_tick_value "
                          "trade_stops_level filling_mode")
Tick = namedtuple("Tick", "bid ask")
Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp magic comment")
Res = namedtuple("Res", "retcode order price comment")
Deal = namedtuple("Deal", "entry profit commission swap")
Cash = namedtuple("Cash", "ticket type profit")


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, ORDER_TIME_GTC = 1, 6, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, trade_mode=0, login=111, balance=1000.0):
        self.trade_mode, self.login, self.balance, self.equity = trade_mode, login, balance, balance
        self.bid, self.algo = 3625.0, True
        self.positions, self.sent, self.deals, self.next = {}, [], {}, 500

    def account_info(self):
        return Acc(self.login, self.trade_mode, self.balance, self.equity, self.equity, "USD")

    def terminal_info(self):
        return Term(self.algo)

    def symbol_select(self, s, on=True):
        return s == "XAUUSD"

    def symbol_info(self, s):
        return Info("XAUUSD", 2, 0.01, 0.01, 100.0, 0.01, 0.01, 1.0, 0, 1) if s == "XAUUSD" else None

    def symbol_info_tick(self, s):
        return Tick(self.bid, round(self.bid + 0.3, 2))

    def order_calc_profit(self, otype, s, vol, price, close):
        return (close - price) * 100 * vol * (1 if otype == 0 else -1)

    def order_calc_margin(self, otype, s, vol, price):
        return price * 100 * vol / 500

    def positions_get(self, symbol=None):
        return tuple(self.positions.values())

    def history_deals_get(self, *a, position=None, **kw):
        if a:                                                   # Zeitraum -> Kontobuchungen
            return tuple(getattr(self, "cash", ()))
        return tuple(self.deals.get(position, ()))

    def last_error(self):
        return (1, "ok")

    def order_check(self, req):
        return Res(0, 0, req.get("price", 0), "ok")

    def order_send(self, req):
        self.sent.append(dict(req))
        if req["action"] == self.TRADE_ACTION_SLTP:
            p = self.positions[req["position"]]
            self.positions[p.ticket] = p._replace(sl=req["sl"], tp=req["tp"])
            return Res(10009, 0, 0, "done")
        if "position" in req:
            p = self.positions[req["position"]]
            rest = round(p.volume - req["volume"], 2)
            if rest > 0:
                self.positions[p.ticket] = p._replace(volume=rest)
            else:
                del self.positions[p.ticket]
            return Res(10009, 0, req["price"], "done")
        self.next += 1
        self.positions[self.next] = Pos(self.next, req["symbol"], req["type"], req["volume"], req["price"], req["sl"],
                                        req["tp"], req["magic"], req["comment"])
        return Res(10009, self.next, req["price"], "done")

    def hit_stop(self, ticket, profit):
        del self.positions[ticket]
        self.deals[ticket] = [Deal(1, profit, 0.0, 0.0)]


def make(tmp_path, mt5=None, **kw):
    cfg = TelegramConfig(data_root=str(tmp_path), **kw)
    mt5 = mt5 or FakeMT5()
    tr = SignalTrader(mt5, cfg, SignalStore(tmp_path), log=lambda *_: None)
    tr.tick(T0)
    return tr, mt5


def test_signal_opens_sell_with_stop_and_target(tmp_path):
    tr, mt5 = make(tmp_path)
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "offen"
    (req,) = mt5.sent
    assert req["type"] == mt5.ORDER_TYPE_SELL and req["sl"] == 3642 and req["tp"] == 3560
    assert req["magic"] == MAGIC_TELEGRAM and req["comment"] == "TG 1"
    # 25 % von 1000 = 250 $ Risiko, 17 $ bis zum Stop je 0,01 Lot -> 0,14 Lot
    assert req["volume"] == pytest.approx(0.14)
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "schon verarbeitet"
    assert len(mt5.sent) == 1


def test_fixed_lot(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.02)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    assert mt5.sent[0]["volume"] == pytest.approx(0.02)


def test_waits_for_entry_zone_then_expires_or_enters(tmp_path):
    tr, mt5 = make(tmp_path)
    mt5.bid = 3600.0                                    # schon weit Richtung Ziel gelaufen
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "wartend"
    tr.tick(T0 + pd.Timedelta(minutes=30))
    assert not mt5.sent
    mt5.bid = 3621.0                                    # zurück an die Zone (innerhalb der Toleranz)
    tr.tick(T0 + pd.Timedelta(minutes=40))
    assert len(mt5.sent) == 1
    # zweites Signal kommt nie in die Zone -> verfällt
    mt5.bid = 3590.0
    tr.on_message(2, FX_CULTURE, T0 + pd.Timedelta(minutes=41), now=T0 + pd.Timedelta(minutes=41))
    tr.tick(T0 + pd.Timedelta(minutes=41 + 121))
    assert tr.st["signale"]["2"]["status"] == "abgelaufen"


def test_beyond_stop_or_old_message_is_skipped(tmp_path):
    tr, mt5 = make(tmp_path)
    mt5.bid = 3650.0
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "verworfen"
    mt5.bid = 3625.0
    assert tr.on_message(2, FX_CULTURE, T0, now=T0 + pd.Timedelta(minutes=10)) == "verworfen"   # nach Neustart
    assert not mt5.sent


def test_partials_then_breakeven_then_close(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.02)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    ticket = tr.st["signale"]["1"]["ticket"]
    entry = mt5.positions[ticket].price_open
    mt5.bid = 3600.0
    tr.on_message(2, "Well well, taking partials here", T0, now=T0 + pd.Timedelta(minutes=7))
    assert mt5.positions[ticket].volume == pytest.approx(0.01)
    tr.on_message(3, "and also moving my SL to BE", T0, now=T0 + pd.Timedelta(minutes=7))
    assert mt5.positions[ticket].sl == pytest.approx(entry)
    assert tr.st["signale"]["1"]["risiko"] == 0.0
    tr.on_message(4, "Closing the rest now", T0, now=T0 + pd.Timedelta(minutes=20))
    assert ticket not in mt5.positions


def test_partial_with_minimum_lot_closes_whole_position(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.01)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    ticket = tr.st["signale"]["1"]["ticket"]
    mt5.bid = 3600.0
    res = tr.on_message(2, "taking partials", T0, now=T0)
    assert "nicht teilbar" in res and ticket not in mt5.positions


BUY_TRADE = """BUY TRADE XAU/USD
4054.0-4050
📍Stop Loss: 4033
Target: open 
This is not financial advice. This is a personal trade idea shared for educational and informational purposes only."""


def test_parses_buy_trade_without_target():
    s = parse_signal(BUY_TRADE)
    assert (s.symbol, s.direction, s.entry_lo, s.entry_hi, s.stop, s.targets) == ("XAUUSD", 1, 4050, 4054, 4033, [])
    s = parse_signal("SELL TRADE XAU/USD\n📍Stop Loss: 3890\nTarget: open")
    assert (s.direction, s.entry_lo, s.stop, s.targets) == (-1, None, 3890, [])


def test_open_target_trade_runs_without_tp_until_followups(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.01)
    mt5.bid = 4052.0
    assert tr.on_message(1, BUY_TRADE, T0, now=T0) == "offen"
    req = mt5.sent[0]
    assert req["type"] == mt5.ORDER_TYPE_BUY and req["sl"] == 4033 and req["tp"] == 0.0
    ticket = tr.st["signale"]["1"]["ticket"]
    mt5.bid = 4070.0
    tr.on_message(2, "Removing risk here", T0, now=T0)
    assert mt5.positions[ticket].sl == pytest.approx(mt5.positions[ticket].price_open)
    tr.on_message(3, "And calling it a day", T0, now=T0)
    assert ticket not in mt5.positions


def test_breakeven_refused_when_price_not_in_profit_and_stop_never_widened(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.02)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    ticket = tr.st["signale"]["1"]["ticket"]
    mt5.bid = 3630.0                                   # im Minus: Stop auf Einstieg wäre sofort ausgelöst
    assert "unverändert" in tr.on_message(2, "SL to BE", T0, now=T0)
    assert "unverändert" in tr.on_message(3, "move SL to 3660", T0, now=T0)   # weiter weg: nie
    assert mt5.positions[ticket].sl == 3642


def test_followup_mode_be_only_ignores_partials(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.02, followups="nur_be_und_schliessen")
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    mt5.bid = 3600.0
    assert "ignoriert" in tr.on_message(2, "taking partials", T0, now=T0)


def test_cancel_before_entry(tmp_path):
    tr, mt5 = make(tmp_path)
    mt5.bid = 3600.0
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    tr.on_message(2, "Cancel this one, don't enter", T0, now=T0)
    assert tr.st["signale"]["1"]["status"] == "verworfen"
    mt5.bid = 3624.0
    tr.tick(T0 + pd.Timedelta(minutes=5))
    assert not mt5.sent


def test_closed_at_broker_is_recorded(tmp_path):
    tr, mt5 = make(tmp_path)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    t = tr.st["signale"]["1"]["ticket"]
    mt5.hit_stop(t, -85.0)
    tr.tick(T0 + pd.Timedelta(hours=1))
    rec = tr.st["signale"]["1"]
    assert rec["status"] == "beendet" and rec["ergebnis"] == -85.0


def test_limits_positions_and_daily_loss_and_drawdown(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.01, max_positions=1)
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    assert tr.on_message(2, FX_CULTURE, T0, now=T0) == "verworfen"            # schon 1 Position offen
    mt5.positions.clear()
    mt5.balance = mt5.equity = 740.0                                          # -26 % heute
    tr.tick(T0 + pd.Timedelta(minutes=1))
    assert tr.on_message(3, FX_CULTURE, T0, now=T0 + pd.Timedelta(minutes=1)) == "verworfen"
    assert tr.st["konto"]["gesperrt"]
    mt5.balance = mt5.equity = 590.0                                          # -41 % vom Höchststand
    tr.tick(T0 + pd.Timedelta(days=1))
    assert tr.st["konto"]["gestoppt"]
    nxt = T0 + pd.Timedelta(days=1, minutes=1)
    assert tr.on_message(4, FX_CULTURE, nxt, now=nxt) == "verworfen"


def test_fixed_minimum_lot_on_small_account(tmp_path):
    tr, mt5 = make(tmp_path, FakeMT5(balance=90.73), lot=0.01)                # 17 $ = 19 % <= 25 %
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "offen"
    assert tr.on_message(2, FX_CULTURE, T0, now=T0) == "verworfen"            # Tagesrisiko 2 x 19 % > 25 %


def test_fixed_lot_too_risky_for_small_account(tmp_path):
    tr, mt5 = make(tmp_path, FakeMT5(balance=100.0), lot=0.02)                # 0,02 Lot = 34 $ > 25 $
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "verworfen"
    assert "riskieren" in tr.st["signale"]["1"]["grund"]


# ---------------------------------------------------------------------- Konto: Demo / Live
def test_account_allowed():
    assert account_allowed({"trade_mode": 0, "login": 1}) is None
    assert account_allowed({"trade_mode": 2, "login": 7}) is not None
    assert account_allowed({"trade_mode": 2, "login": 7}, live_login=7) is None
    assert account_allowed({"trade_mode": 2, "login": 8}, live_login=7) is not None
    assert account_allowed({"trade_mode": 1, "login": 7}, live_login=7) is not None   # Wettbewerb nie


def test_live_account_only_when_configured(tmp_path):
    live = FakeMT5(trade_mode=2, login=4711)
    tr, _ = make(tmp_path, live, lot=0.02)                                     # konto = demo
    assert tr.on_message(1, FX_CULTURE, T0, now=T0) == "wartend" and not live.sent
    tr2, _ = make(tmp_path / "b", live, lot=0.02, account="live", live_account=9999)   # falsche Nummer
    assert tr2.on_message(1, FX_CULTURE, T0, now=T0) == "wartend" and not live.sent
    tr3, _ = make(tmp_path / "c", live, lot=0.02, account="live", live_account=4711)
    assert tr3.on_message(1, FX_CULTURE, T0, now=T0) == "offen"
    assert live.sent[0]["volume"] == pytest.approx(0.02)


def test_bot_positions_are_not_touched(tmp_path):
    tr, mt5 = make(tmp_path, lot=0.02)
    mt5.positions[1] = Pos(1, "XAUUSD", 1, 0.5, 3624.0, 3642.0, 3560.0, MAGIC, "TB")
    tr.on_message(1, FX_CULTURE, T0, now=T0)
    mt5.bid = 3600.0
    tr.on_message(2, "closing everything now", T0, now=T0)
    assert 1 in mt5.positions and mt5.positions[1].volume == 0.5


# ---------------------------------------------------------------------- Einstellungen
def test_config_caps_and_live_requirements(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text('[handel]\nrisiko_je_trade_pct = 50\nmax_positionen = 9\nlot = 0.02\n', encoding="utf-8")
    c = load(p)
    assert c.risk_pct == 25.0 and c.max_positions == 2 and c.live_login is None
    p.write_text('[telegram]\nkonto = "live"\nlive_konto = 0\n[handel]\nlot = 0.02\n', encoding="utf-8")
    with pytest.raises(ValueError, match="live_konto"):
        load(p)
    p.write_text('[telegram]\nkonto = "live"\nlive_konto = 4711\n[handel]\nlot = 1.0\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Lotgröße"):
        load(p)
    p.write_text('[telegram]\nkonto = "live"\nlive_konto = 4711\n[handel]\nlot = 0.02\n', encoding="utf-8")
    assert load(p).live_login == 4711


def test_shipped_config_loads_and_does_not_trade_live_without_account_number():
    from pathlib import Path
    root = Path(telegram_signale.__file__).resolve().parent
    with pytest.raises(ValueError, match="live_konto"):
        load(root / "telegram.toml")          # erst mit eingetragener Kontonummer startklar


def test_pruefen_cli(capsys):
    telegram_signale.main(["--pruefen", FX_CULTURE])
    assert "VERKAUF XAUUSD" in capsys.readouterr().out


def test_deposit_raises_daily_base(tmp_path):
    tr, mt5 = make(tmp_path, FakeMT5(balance=57.97), lot=0.01)
    mt5.balance = mt5.equity = 307.0                                          # Einzahlung am selben Tag
    tr.tick(T0 + pd.Timedelta(minutes=1))
    nxt = T0 + pd.Timedelta(minutes=2)
    assert tr.on_message(1, FX_CULTURE, nxt, now=nxt) == "offen"             # 17 $ < 25 % von 307 $


def test_deposit_shifts_drawdown_peak_and_lifts_stop(tmp_path):
    tr, mt5 = make(tmp_path, FakeMT5(balance=100.91), lot=0.01)
    mt5.balance = mt5.equity = 57.97                                          # -42 % -> Stopp
    tr.tick(T0 + pd.Timedelta(hours=1))
    assert tr.st["konto"]["gestoppt"]
    mt5.cash = [Cash(9001, 2, 250.0)]                                         # Einzahlung 250 $
    mt5.balance = mt5.equity = 307.97
    tr.tick(T0 + pd.Timedelta(hours=2))
    k = tr.st["konto"]
    assert not k["gestoppt"] and k["hoechststand"] == pytest.approx(350.91)
    tr.tick(T0 + pd.Timedelta(hours=3))                                       # nicht doppelt anrechnen
    assert tr.st["konto"]["hoechststand"] == pytest.approx(350.91)
    nxt = T0 + pd.Timedelta(hours=3, minutes=1)
    assert tr.on_message(1, FX_CULTURE, nxt, now=nxt) == "offen"


def test_withdrawal_does_not_trigger_stop(tmp_path):
    tr, mt5 = make(tmp_path, FakeMT5(balance=300.0), lot=0.01)
    mt5.cash = [Cash(9002, 2, -200.0)]
    mt5.balance = mt5.equity = 100.0
    tr.tick(T0 + pd.Timedelta(hours=1))
    assert not tr.st["konto"]["gestoppt"] and tr.st["konto"]["hoechststand"] == pytest.approx(100.0)
