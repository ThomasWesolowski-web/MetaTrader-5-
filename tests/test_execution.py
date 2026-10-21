"""Phase 5 – Orders nur auf dem Demokonto, mit Risiko-, Tages- und Drawdown-Grenzen (simuliertes MT5)."""

from collections import namedtuple

import numpy as np
import pandas as pd
import pytest

import beobachtung_ausfuehren
from fake_market import FakeMT5
from test_paper import CANDS, make_observer, run_steps, write_specs
from tradingbot.config import Config, load_config
from tradingbot.execution import MAGIC, DemoExecutor
from tradingbot.backtest.account import RiskRules
from tradingbot.paper import PaperStore

Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp magic comment")
Res = namedtuple("Res", "retcode order deal price comment")
Deal = namedtuple("Deal", "position_id entry profit commission swap")
Acc = namedtuple("Acc", "login server company currency trade_mode leverage balance equity margin_free margin_mode")
Term = namedtuple("Term", "build maxbars connected trade_allowed")


class TradingFake(FakeMT5):
    """FakeMT5 mit Orderfunktionen. Füllt Marktorders sofort zum aktuellen Kurs."""
    TRADE_ACTION_DEAL, ORDER_TIME_GTC = 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, *a, trade_mode=0, **kw):
        super().__init__(*a, **kw)
        self.trade_mode, self.balance, self.equity, self.algo = trade_mode, 1000.0, 1000.0, True
        self.positions, self.sent, self.next_ticket, self.deals = {}, [], 1000, {}

    def account_info(self):
        return Acc(1, "Fake-Demo", "Fake", "EUR", self.trade_mode, 500, self.balance, self.equity, self.equity, 2)

    def terminal_info(self):
        return Term(6272, 100000, True, self.algo)

    def positions_get(self, symbol=None):
        return tuple(p for p in self.positions.values() if symbol is None or p.symbol == symbol)

    def history_deals_get(self, position=None, **kw):
        return tuple(self.deals.get(position, ()))

    def order_check(self, req):
        return Res(0, 0, 0, req["price"], "ok")

    def order_send(self, req):
        self.sent.append(dict(req))
        if "position" in req:
            p = self.positions.pop(req["position"])
            sign = 1 if p.type == self.ORDER_TYPE_BUY else -1
            self.deals[p.ticket] = [Deal(p.ticket, 1, round(sign * (req["price"] - p.price_open) * p.volume / 1.08, 2), 0.0, 0.0)]
            return Res(10009, 0, 1, req["price"], "done")
        self.next_ticket += 1
        self.positions[self.next_ticket] = Pos(self.next_ticket, req["symbol"], req["type"], req["volume"], req["price"],
                                               req["sl"], req["tp"], req["magic"], req["comment"])
        return Res(10009, self.next_ticket, self.next_ticket, req["price"], "done")


@pytest.fixture
def trading(tmp_path):
    fake = TradingFake(now_utc="2026-03-02 00:00", days=60)
    cfg = Config()
    cfg.data_root = tmp_path / "data"
    cfg.paper.candidates = list(CANDS)
    cfg.paper.warmup_bars = 600
    cfg.execution.orders_enabled = True
    write_specs(cfg.data_root, fake)
    return fake, cfg


def events(cfg, kind=None):
    ev = PaperStore(cfg.data_root).events()
    return [e for e in ev if kind is None or e["art"] == kind]


def test_orders_follow_live_signals_with_risk_based_size(trading):
    fake, cfg = trading
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-10 00:00")
    opened = events(cfg, "order_eroeffnet")
    skipped = events(cfg, "order_nicht_gesendet")
    signals = [e for e in events(cfg, "signal")]
    assert signals and len(opened) + len(skipped) >= 1
    assert len(opened) + len(skipped) <= len(signals)                 # nur für Signale, nie ohne Signal
    sent_open = [r for r in fake.sent if "position" not in r]
    assert len(sent_open) == len(opened) > 0
    for r, e in zip(sent_open, opened):
        assert r["magic"] == MAGIC and r["sl"] > 0 and r["volume"] >= 0.01
        assert e["risiko_eur"] <= 1000 * RiskRules().risk_per_trade + 1e-6     # nie mehr als 1,5 % riskiert
        sig = next(s for s in signals if s["key"] == e["signal"] and s["kandidat"] == e["kandidat"])
        assert r["sl"] == pytest.approx(sig["stop"], abs=0.01)               # Stop der Strategie beim Broker
        assert sig["verzoegerung_s"] <= cfg.paper.max_delay_s


def test_no_orders_on_real_account(trading):
    fake, cfg = trading
    fake.trade_mode = 2                                               # Echtgeldkonto
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-08 00:00")
    assert fake.sent == []
    start = events(cfg, "handel_start")[-1]
    assert start["aktiv"] is False and "kein Demokonto" in start["grund"]


def test_switch_to_real_account_blocks_next_order(trading):
    fake, cfg = trading
    obs = make_observer(fake, cfg)
    fake.trade_mode = 2                                               # nach dem Start auf Echtgeld gewechselt
    run_steps(obs, fake, "2026-03-08 00:00")
    assert [r for r in fake.sent if "position" not in r] == []


def test_orders_disabled_in_config_sends_nothing(trading):
    fake, cfg = trading
    cfg.execution.orders_enabled = False
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-08 00:00")
    assert fake.sent == [] and obs.executor is None
    assert Config().execution.orders_enabled is False               # Standard ohne Einstellung: aus


def test_algo_trading_switched_off_blocks_orders(trading):
    fake, cfg = trading
    fake.algo = False
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-06 00:00")
    assert fake.sent == [] and "Algo-Trading" in obs.executor.reason


def test_drawdown_stop_and_release(trading):
    fake, cfg = trading
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-03 00:00")
    fake.equity = 790.0                                               # 21 % unter dem Höchststand 1000
    run_steps(obs, fake, "2026-03-03 01:00")
    assert events(cfg, "handel_gestoppt") and obs.executor.st["halted"]
    n_before = len([r for r in fake.sent if "position" not in r])
    run_steps(obs, fake, "2026-03-07 00:00")
    assert len([r for r in fake.sent if "position" not in r]) == n_before      # keine neuen Orders
    assert beobachtung_ausfuehren.main(["--config", str(_cfg_file(cfg)), "--handel-freigeben"]) == 0
    run_steps(obs, fake, "2026-03-07 01:00")
    assert not obs.executor.st["halted"] and events(cfg, "handel_freigegeben")
    assert obs.executor.st["peak_equity"] == 790.0                  # Drawdown ab jetzt neu gemessen


def test_daily_loss_limit(trading):
    fake, cfg = trading
    obs = make_observer(fake, cfg)
    run_steps(obs, fake, "2026-03-03 00:30")
    fake.balance = 1000 - 46.0                                        # 4,6 % Verlust am selben Tag
    fake.equity = fake.balance
    run_steps(obs, fake, "2026-03-03 01:00")
    assert obs.executor.st["daily_locked"] and events(cfg, "tageslimit_erreicht")
    run_steps(obs, fake, "2026-03-04 02:00")                          # neuer Brokertag
    assert not obs.executor.st["daily_locked"]


def _cfg_file(cfg):
    f = cfg.data_root.parent / "config.toml"
    f.write_text('[data]\nroot = "data"\n', encoding="utf-8")
    return f


def _executor(fake, tmp_path):
    store = PaperStore(tmp_path)
    ex = DemoExecutor(fake, store, {}, RiskRules(), 100, log=lambda *a: None)
    ex.start(pd.Timestamp("2026-03-02", tz="UTC"))
    return ex


class _Cand:
    key, symbol, timeframe, strategy = "BTCUSD_H1_test", "BTCUSD", "H1", "test"


def test_position_size_min_lot_and_max_positions(tmp_path):
    from tradingbot.backtest.costs import scenarios_for
    from tradingbot.instruments import CurrencyConverter, InstrumentSpec
    fake = TradingFake(now_utc="2026-03-02 00:00", days=10)
    ex = _executor(fake, tmp_path)
    spec = InstrumentSpec.from_mt5(fake._info("BTCUSD")._asdict())
    conv = CurrencyConverter()
    conv.add_live("EURUSD", "EUR", "USD", 1.25)
    costs = scenarios_for("BTCUSD")["konservativ"]
    ask = fake.symbol_info_tick("BTCUSD").ask
    now = pd.Timestamp("2026-03-02", tz="UTC")
    # Stop 500 USD: 1 Lot verliert 400 € + Kommission -> 15 € erlauben 0,03 Lot (abgerundet)
    rec = ex.open(_Cand, "k1", 1, ask - 500, float("nan"), 0, spec, costs, conv, "EUR", now)
    comm = 0.0004 * ask / 1.25
    assert rec["volumen"] == pytest.approx(np.floor(15 / (400 + comm) * 100) / 100)
    assert fake.sent[-1]["tp"] == 0.0 and fake.sent[-1]["sl"] == round(ask - 500, 2)
    # Stop so groß, dass schon 0,01 Lot mehr als 15 € riskiert -> keine Order
    assert ex.open(_Cand, "k2", 1, ask - 5000, float("nan"), 0, spec, costs, conv, "EUR", now) is None
    assert "Mindestlot" in events_of(tmp_path)[-1]["grund"]
    ex.open(_Cand, "k3", -1, fake.symbol_info_tick("BTCUSD").bid + 300, float("nan"), 0, spec, costs, conv, "EUR", now)
    assert ex.open(_Cand, "k4", 1, ask - 300, float("nan"), 0, spec, costs, conv, "EUR", now) is None
    assert "2 Positionen" in events_of(tmp_path)[-1]["grund"]
    assert len(fake.positions) == 2


def events_of(path):
    return PaperStore(path).events()


def test_exit_signal_closes_position_and_broker_close_is_recorded(tmp_path):
    from tradingbot.strategies import Signals
    fake = TradingFake(now_utc="2026-03-02 00:00", days=10)
    ex = _executor(fake, tmp_path)
    fake.positions[5] = Pos(5, "BTCUSD", fake.ORDER_TYPE_BUY, 0.01, 80000.0, 79000.0, 0.0, MAGIC, "x")
    ex.st["positions"]["5"] = {"ticket": 5, "kandidat": _Cand.key, "direction": 1, "entry_bar_server": 7200}
    bars = pd.DataFrame({"time_server": [0, 3600, 7200, 10800, 14400]})
    exit_long = np.array([False, False, False, False, True])
    sig = Signals(np.array([], int), np.array([], int), np.array([]), np.array([]), 0, exit_long, None)
    now = pd.Timestamp("2026-03-02", tz="UTC")
    ex.manage(_Cand, bars, sig, now)
    assert 5 not in fake.positions and fake.sent[-1]["position"] == 5
    ex.manage(_Cand, bars, sig, now)                                    # nächster Durchlauf: als beendet erfasst
    assert ex.st["positions"] == {} and ex.st["closed"][0]["ticket"] == 5
    assert any(e["art"] == "position_beendet" for e in events_of(tmp_path))


def test_time_stop_closes_position(tmp_path):
    from tradingbot.strategies import Signals
    fake = TradingFake(now_utc="2026-03-02 00:00", days=10)
    ex = _executor(fake, tmp_path)
    fake.positions[6] = Pos(6, "BTCUSD", fake.ORDER_TYPE_SELL, 0.01, 80000.0, 81000.0, 0.0, MAGIC, "x")
    ex.st["positions"]["6"] = {"ticket": 6, "kandidat": _Cand.key, "direction": -1, "entry_bar_server": 3600}
    bars = pd.DataFrame({"time_server": [0, 3600, 7200]})
    sig = Signals(np.array([], int), np.array([], int), np.array([]), np.array([]), 3, None, np.zeros(3, bool))
    ex.manage(_Cand, bars, sig, pd.Timestamp("2026-03-02", tz="UTC"))
    assert 6 in fake.positions                                          # 2 von 3 Kerzen
    bars = pd.DataFrame({"time_server": [0, 3600, 7200, 10800]})
    sig = Signals(np.array([], int), np.array([], int), np.array([]), np.array([]), 3, None, np.zeros(4, bool))
    ex.manage(_Cand, bars, sig, pd.Timestamp("2026-03-02", tz="UTC"))
    assert 6 not in fake.positions


def test_shipped_config_enables_demo_orders_for_approved_candidates():
    from pathlib import Path
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.toml")
    assert cfg.execution.orders_enabled is True
    keys = {c.key for c in cfg.paper.candidates}
    assert {"BTCUSD_M15_sma_box_trend", "BTCUSD_H1_kombi_breakout_momentum"} <= keys


def test_position_ticket_differs_from_order_ticket(tmp_path):
    from tradingbot.backtest.costs import scenarios_for
    from tradingbot.instruments import CurrencyConverter, InstrumentSpec

    class Netting(TradingFake):
        def order_send(self, req):
            r = super().order_send(req)
            if "position" not in req:                      # Position bekommt eine andere Nummer als die Order
                p = self.positions.pop(r.order)
                self.positions[r.order + 500] = p._replace(ticket=r.order + 500)
            return r
    fake = Netting(now_utc="2026-03-02 00:00", days=10)
    ex = _executor(fake, tmp_path)
    spec = InstrumentSpec.from_mt5(fake._info("BTCUSD")._asdict())
    conv = CurrencyConverter()
    conv.add_live("EURUSD", "EUR", "USD", 1.25)
    ask = fake.symbol_info_tick("BTCUSD").ask
    rec = ex.open(_Cand, "k1", 1, ask - 500, float("nan"), 0, spec, scenarios_for("BTCUSD")["konservativ"], conv,
                  "EUR", pd.Timestamp("2026-03-02", tz="UTC"))
    assert rec["ticket"] in fake.positions                 # verfolgt wird die tatsächliche Position
