"""Orderausführung auf dem DEMOKONTO (Phase 5, Freigabe des Nutzers vom 21.10.2026).

Dies ist die EINZIGE Stelle im Programm, die Orders an MetaTrader 5 sendet (ein Test stellt das sicher).
Neben dem Bot (DemoExecutor) nutzen auch die Day-Trader-Skills (tradingbot/daytrader) und die Telegram-Signale
(tradingbot/telegram) und der Gold-Bot (tradingbot/goldbot) diese Stelle: send_manual_order / close_manual_position /
modify_position_sltp, der Gold-Bot zusätzlich send_pending_order / cancel_pending_order (Limit-Orders), jeweils mit
eigener Magic-Nummer und denselben Demo-Prüfungen.

Ausnahme (Freigabe des Nutzers vom 23.11.2026): NUR die Telegram-Signale dürfen zusätzlich auf GENAU EINEM
Echtgeldkonto handeln – dem, dessen Kontonummer der Nutzer selbst in telegram.toml (live_konto) eingetragen hat.
Dafür übergeben sie live_login; ein anderes Echtgeldkonto oder ein Wettbewerbskonto wird weiterhin abgelehnt.

Sicherheitsregeln (fest im Code, nicht über config.toml abschaltbar):
  * nur auf einem DEMOKONTO (account_info().trade_mode == 0) – geprüft beim Start UND vor jeder einzelnen Order;
    bei einem Echtgeld- oder Wettbewerbskonto wird keine Order gesendet
  * Positionsgröße aus dem Risiko je Trade (RiskRules.risk_per_trade vom aktuellen Kontostand) bis zum Stop,
    abgerundet auf das Volumenraster; unter der Mindestlotgröße wird NICHT gehandelt
  * Stop (und Ziel, falls die Strategie eines hat) werden mit der Order beim Broker hinterlegt
  * höchstens RiskRules.max_positions offene Positionen dieses Programms
  * Tagesverlust (realisiert, Brokertag) >= RiskRules.max_daily_loss -> keine neuen Orders bis zum nächsten Tag
  * Drawdown (Kontostand inkl. offener Positionen gegenüber dem Höchststand) >= RiskRules.max_drawdown -> Handel
    gestoppt; offene Positionen behalten ihren Stop; weiter erst nach ausdrücklicher Freigabe
    (python beobachtung_ausfuehren.py --handel-freigeben)
  * Einstiege nur für Signale, die in diesem Durchlauf rechtzeitig nach Kerzenschluss erkannt wurden
  * Ausstiege: Stop/Ziel beim Broker; Ausstiegssignal oder Zeitstopp der Strategie -> Schließen zum Marktpreis
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest.account import RiskRules
from .instruments import InstrumentSpec, round_volume_down

MAGIC = 20261021                  # kennzeichnet Positionen dieses Programms
MAGIC_DAYTRADER = 20261123        # Positionen der Day-Trader-Skills (vom Bot nicht angefasst)
MAGIC_TELEGRAM = 20261124         # Positionen aus Telegram-Signalen (vom Bot nicht angefasst)
MAGIC_GOLDBOT = 20261125          # Orders/Positionen des Gold-Bots (Order-Block-Strategie, nur Demo)
DEMO = 0                          # ACCOUNT_TRADE_MODE_DEMO
REAL = 2                          # ACCOUNT_TRADE_MODE_REAL
RELEASE_FILE = "handel_freigeben.anfrage"
OK_CODES = (10008, 10009)         # TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE
INVALID_FILL = 10030              # TRADE_RETCODE_INVALID_FILL


def _d(obj) -> dict:
    return {} if obj is None else (obj._asdict() if hasattr(obj, "_asdict") else dict(obj))


def _filling_mode(mt5, symbol: str) -> int:
    fm = int(getattr(mt5.symbol_info(symbol), "filling_mode", 0) or 0)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def account_allowed(acc: dict, live_login: int | None = None) -> str | None:
    """None = auf diesem Konto darf gehandelt werden. Demokonto immer; Echtgeld nur mit passender live_login."""
    mode = acc.get("trade_mode")
    if mode == DEMO:
        return None
    if live_login and mode == REAL and int(acc.get("login") or 0) == int(live_login):
        return None
    if live_login:
        return (f"eingeloggt ist Konto {acc.get('login')} (Modus {mode}), freigegeben sind nur Demokonten und das "
                f"Live-Konto {live_login} – es werden KEINE Orders gesendet")
    return "kein Demokonto eingeloggt – es werden KEINE Orders gesendet"


def _demo_send(mt5, req: dict, live_login: int | None = None) -> tuple[bool, object, str]:
    """order_check + order_send – nur, wenn in diesem Moment ein Demokonto (oder das freigegebene Live-Konto)
    eingeloggt ist."""
    why = account_allowed(_d(mt5.account_info()), live_login)          # vor JEDER Order erneut prüfen
    if why:
        return False, None, why
    chk = mt5.order_check(req)
    if chk is None or getattr(chk, "retcode", -1) != 0:
        return False, chk, f"Prüfung abgelehnt: {getattr(chk, 'retcode', None)} {getattr(chk, 'comment', '')} {mt5.last_error()}"
    res = mt5.order_send(req)
    if res is None or getattr(res, "retcode", -1) not in OK_CODES:
        return False, res, f"Order abgelehnt: {getattr(res, 'retcode', None)} {getattr(res, 'comment', '')} {mt5.last_error()}"
    return True, res, "ok"


def _manual_ready(mt5, live_login: int | None = None) -> str | None:
    why = account_allowed(_d(mt5.account_info()), live_login)
    if why:
        return why
    if _d(mt5.terminal_info()).get("trade_allowed") is False:
        return "Algo-Trading ist in MT5 ausgeschaltet (Schaltfläche 'Algo Trading')"
    return None


def send_manual_order(mt5, symbol: str, direction: int, volume: float, sl: float, tp: float | None, digits: int,
                      deviation_points: int = 100, comment: str = "DT", magic: int = MAGIC_DAYTRADER,
                      live_login: int | None = None) -> dict:
    """Marktorder einer Day-Trader-Freigabe (eine Order je Ja). Stop und Ziel werden mit der Order hinterlegt.
    Volumen, Stop und Ziel kommen aus dem geprüften Ticket; hier wird nur noch einmal gegen den aktuellen Kurs
    geprüft, dass Stop und Ziel auf der richtigen Seite liegen."""
    why = _manual_ready(mt5, live_login)
    if why:
        return {"ok": False, "meldung": why}
    if direction not in (1, -1) or not (volume > 0) or not (sl > 0):
        return {"ok": False, "meldung": "Ticket unvollständig (Richtung, Volumen oder Stop fehlt)"}
    tick = _d(mt5.symbol_info_tick(symbol))
    if not tick.get("bid"):
        return {"ok": False, "meldung": "kein aktueller Kurs"}
    price = float(tick["ask"] if direction == 1 else tick["bid"])
    if direction * (price - sl) <= 0:
        return {"ok": False, "meldung": f"Kurs {price} liegt bereits jenseits des Stops {sl}"}
    if tp and direction * (tp - price) <= 0:
        return {"ok": False, "meldung": f"Kurs {price} liegt bereits jenseits des Ziels {tp}"}
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
           "type": mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL, "price": price,
           "sl": round(float(sl), digits), "tp": round(float(tp), digits) if tp else 0.0,
           "deviation": int(deviation_points), "magic": int(magic), "comment": comment[:31],
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(mt5, symbol)}
    ok, res, msg = _demo_send(mt5, req, live_login)
    return {"ok": ok, "meldung": msg, "anfrage": req, "ticket": int(getattr(res, "order", 0) or 0) if ok else None,
            "kurs": float(getattr(res, "price", 0) or price) if ok else None}


def _own_position(mt5, ticket: int, magic: int):
    pos = next((p for p in (mt5.positions_get() or ()) if int(p.ticket) == int(ticket)), None)
    if pos is None:
        return None, f"Position {ticket} ist nicht offen"
    if getattr(pos, "magic", None) != magic:
        return None, f"Position {ticket} gehört nicht zu diesem Programmteil (Magic {magic}) – wird nicht angefasst"
    return pos, None


def close_manual_position(mt5, ticket: int, deviation_points: int = 100, comment: str = "DT exit",
                          magic: int = MAGIC_DAYTRADER, volume: float | None = None,
                          live_login: int | None = None) -> dict:
    """Schließt eine eigene Position (nur Demokonto, nur eigene Magic-Nummer) zum Marktpreis – ganz oder, mit
    volume, teilweise (Teilgewinn)."""
    why = _manual_ready(mt5, live_login)
    if why:
        return {"ok": False, "meldung": why}
    pos, why = _own_position(mt5, ticket, magic)
    if why:
        return {"ok": False, "meldung": why}
    vol = float(pos.volume) if volume is None else float(volume)
    if not (0 < vol <= float(pos.volume) + 1e-9):
        return {"ok": False, "meldung": f"Volumen {vol} passt nicht zur Position ({pos.volume} Lot)"}
    tick = _d(mt5.symbol_info_tick(pos.symbol))
    buy = int(pos.type) == mt5.ORDER_TYPE_BUY
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": vol,
           "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, "position": int(pos.ticket),
           "price": float(tick.get("bid") if buy else tick.get("ask")), "deviation": int(deviation_points),
           "magic": int(magic), "comment": comment[:31], "type_time": mt5.ORDER_TIME_GTC,
           "type_filling": _filling_mode(mt5, pos.symbol)}
    ok, res, msg = _demo_send(mt5, req, live_login)
    return {"ok": ok, "meldung": msg, "kurs": float(getattr(res, "price", 0) or req["price"]) if ok else None}


def modify_position_sltp(mt5, ticket: int, sl: float, tp: float | None, digits: int,
                         magic: int = MAGIC_DAYTRADER, live_login: int | None = None) -> dict:
    """Stop (und Ziel) einer eigenen offenen Position beim Broker ändern, z. B. Stop auf den Einstieg (nur Demokonto)."""
    why = _manual_ready(mt5, live_login)
    if why:
        return {"ok": False, "meldung": why}
    pos, why = _own_position(mt5, ticket, magic)
    if why:
        return {"ok": False, "meldung": why}
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": int(pos.ticket),
           "sl": round(float(sl), digits), "tp": round(float(tp), digits) if tp else 0.0, "magic": int(magic)}
    ok, res, msg = _demo_send(mt5, req, live_login)
    return {"ok": ok, "meldung": msg}


def send_pending_order(mt5, symbol: str, direction: int, volume: float, price: float, sl: float, tp: float | None,
                       digits: int, magic: int = MAGIC_GOLDBOT, comment: str = "GB") -> dict:
    """Limit-Order (Kauf-Limit unter dem Kurs / Verkaufs-Limit über dem Kurs) mit Stop und Ziel beim Broker – NUR auf
    einem Demokonto (kein live_login: Limit-Orders gibt es nur für Demo). Verfall steuert der Aufrufer selbst
    (cancel_pending_order)."""
    why = _manual_ready(mt5)
    if why:
        return {"ok": False, "meldung": why}
    if direction not in (1, -1) or not (volume > 0) or not (sl > 0) or not (price > 0):
        return {"ok": False, "meldung": "Order unvollständig (Richtung, Volumen, Kurs oder Stop fehlt)"}
    if direction * (price - sl) <= 0 or (tp and direction * (tp - price) <= 0):
        return {"ok": False, "meldung": f"Stop {sl} / Ziel {tp} liegen auf der falschen Seite des Limits {price}"}
    tick = _d(mt5.symbol_info_tick(symbol))
    if not tick.get("bid"):
        return {"ok": False, "meldung": "kein aktueller Kurs"}
    market = float(tick["ask"] if direction == 1 else tick["bid"])
    if direction * (market - price) <= 0:
        return {"ok": False, "meldung": f"Limit {price} liegt nicht {'unter' if direction == 1 else 'über'} dem "
                                        f"Kurs {market} – keine Limit-Order möglich"}
    req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": symbol, "volume": float(volume),
           "type": mt5.ORDER_TYPE_BUY_LIMIT if direction == 1 else mt5.ORDER_TYPE_SELL_LIMIT,
           "price": round(float(price), digits), "sl": round(float(sl), digits),
           "tp": round(float(tp), digits) if tp else 0.0, "magic": int(magic), "comment": comment[:31],
           "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(mt5, symbol)}
    ok, res, msg = _demo_send(mt5, req)
    if not ok and getattr(res, "retcode", None) == INVALID_FILL and req["type_filling"] != mt5.ORDER_FILLING_RETURN:
        req["type_filling"] = mt5.ORDER_FILLING_RETURN          # manche Broker wollen für Limit-Orders RETURN
        ok, res, msg = _demo_send(mt5, req)
    return {"ok": ok, "meldung": msg, "anfrage": req, "ticket": int(getattr(res, "order", 0) or 0) if ok else None}


def cancel_pending_order(mt5, ticket: int, magic: int = MAGIC_GOLDBOT) -> dict:
    """Eigene, noch nicht ausgeführte Limit-Order löschen (nur Demokonto, nur eigene Magic-Nummer)."""
    why = _manual_ready(mt5)
    if why:
        return {"ok": False, "meldung": why}
    order = next((o for o in (mt5.orders_get() or ()) if int(o.ticket) == int(ticket)), None)
    if order is None:
        return {"ok": False, "meldung": f"Order {ticket} ist nicht (mehr) offen"}
    if getattr(order, "magic", None) != magic:
        return {"ok": False, "meldung": f"Order {ticket} gehört nicht zu diesem Programmteil – wird nicht angefasst"}
    req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket), "magic": int(magic)}
    why = account_allowed(_d(mt5.account_info()))
    if why:
        return {"ok": False, "meldung": why}
    res = mt5.order_send(req)                                   # Löschen braucht keine order_check-Prüfung
    ok = res is not None and getattr(res, "retcode", -1) in OK_CODES
    return {"ok": ok, "meldung": "ok" if ok else f"Löschen abgelehnt: {getattr(res, 'retcode', None)} "
                                                 f"{getattr(res, 'comment', '')} {mt5.last_error()}"}


class DemoExecutor:
    def __init__(self, mt5, store, state: dict, rules: RiskRules, deviation_points: int = 100, log=print):
        self.mt5, self.store, self.rules, self.log = mt5, store, rules, log
        self.deviation = int(deviation_points)
        self.st = state.setdefault("execution", {"peak_equity": None, "halted": False, "halt_reason": None,
                                                 "day": None, "day_start_balance": None, "daily_locked": False,
                                                 "positions": {}, "closed": []})
        self.active = False
        self.reason = "nicht gestartet"

    # ------------------------------------------------------------------ Konto und Grenzen
    def _account(self) -> dict:
        return _d(self.mt5.account_info())

    def start(self, now) -> bool:
        acc = self._account()
        term = _d(self.mt5.terminal_info())
        if acc.get("trade_mode") != DEMO:
            self.active, self.reason = False, "kein Demokonto eingeloggt – es werden KEINE Orders gesendet"
        elif term.get("trade_allowed") is False:
            self.active, self.reason = False, "Algo-Trading ist in MT5 ausgeschaltet (Schaltfläche 'Algo Trading')"
        else:
            self.active, self.reason = True, "Orders auf dem Demokonto aktiv"
        self.store.event("handel_start", now, aktiv=self.active, grund=self.reason, konto_modus=acc.get("trade_mode"),
                         risiko_je_trade_pct=100 * self.rules.risk_per_trade,
                         tagesverlust_pct=100 * self.rules.max_daily_loss, drawdown_pct=100 * self.rules.max_drawdown,
                         max_positionen=self.rules.max_positions, gestoppt=self.st["halted"])
        return self.active

    def account_checks(self, now, now_srv: int) -> None:
        """Einmal je Durchlauf: Freigabe-Anfrage, Tagesverlust, Drawdown, Algo-Trading-Schalter."""
        rel = self.store.dir / RELEASE_FILE
        if rel.exists():
            rel.unlink(missing_ok=True)
            acc = self._account()
            self.st.update(halted=False, halt_reason=None, peak_equity=acc.get("equity"))
            self.store.event("handel_freigegeben", now, kontostand=acc.get("equity"),
                             hinweis="Drawdown wird ab dem aktuellen Kontostand neu gemessen")
        if not self.active and self.reason.startswith("Algo-Trading"):
            if _d(self.mt5.terminal_info()).get("trade_allowed"):
                self.active, self.reason = True, "Orders auf dem Demokonto aktiv"
                self.store.event("handel_start", now, aktiv=True, grund="Algo-Trading wurde eingeschaltet")
        acc = self._account()
        if not acc:
            return
        day = int(now_srv // 86400)
        if self.st["day"] != day:
            self.st.update(day=day, day_start_balance=acc["balance"], daily_locked=False)
        start_bal = self.st["day_start_balance"] or acc["balance"]
        if not self.st["daily_locked"] and start_bal - acc["balance"] >= self.rules.max_daily_loss * start_bal:
            self.st["daily_locked"] = True
            self.store.event("tageslimit_erreicht", now, verlust_eur=round(start_bal - acc["balance"], 2),
                             grenze_pct=100 * self.rules.max_daily_loss,
                             hinweis="keine neuen Orders bis zum nächsten Handelstag")
        eq = float(acc["equity"])
        peak = max(self.st["peak_equity"] or eq, eq)
        self.st["peak_equity"] = peak
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd >= self.rules.max_drawdown and not self.st["halted"]:
            self.st.update(halted=True, halt_reason=f"Drawdown {100 * dd:.1f} % >= {100 * self.rules.max_drawdown:g} %")
            self.store.event("handel_gestoppt", now, grund=self.st["halt_reason"], hoechststand=round(peak, 2),
                             kontostand=round(eq, 2),
                             hinweis="offene Positionen behalten ihren Stop; weiter nur nach Freigabe "
                                     "(python beobachtung_ausfuehren.py --handel-freigeben)")

    def status(self) -> dict:
        acc = self._account()
        peak = self.st["peak_equity"]
        eq = acc.get("equity")
        return {"aktiv": self.active, "grund": self.reason, "gestoppt": self.st["halted"],
                "stopp_grund": self.st["halt_reason"], "tageslimit_erreicht": self.st["daily_locked"],
                "kontostand": eq, "hoechststand": peak,
                "drawdown_pct": None if not (peak and eq) else round(100 * (peak - eq) / peak, 2),
                "offene_positionen": len(self.st["positions"]), "geschlossene_positionen": len(self.st["closed"])}

    # ------------------------------------------------------------------ Hilfen
    def _mine(self, symbol: str | None = None) -> dict:
        pos = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        return {int(p.ticket): p for p in (pos or ()) if getattr(p, "magic", None) == MAGIC}

    def _filling(self, symbol: str) -> int:
        return _filling_mode(self.mt5, symbol)

    def _send(self, req: dict) -> tuple[bool, object, str]:
        ok, res, msg = _demo_send(self.mt5, req)
        if not ok and msg.startswith("kein Demokonto"):
            self.active, self.reason = False, msg
        return ok, res, msg

    # ------------------------------------------------------------------ Einstieg
    def open(self, cand, signal_key: str, direction: int, stop: float, target: float, entry_bar_server: int,
             spec: InstrumentSpec, costs, conv, account_ccy: str, now) -> dict | None:
        def skip(grund, **kw):
            self.store.event("order_nicht_gesendet", now, kandidat=cand.key, signal=signal_key, grund=grund, **kw)
            return None
        if not self.active:
            return skip(self.reason)
        if self.st["halted"]:
            return skip(f"Handel gestoppt: {self.st['halt_reason']}")
        if self.st["daily_locked"]:
            return skip("Tagesverlustgrenze erreicht")
        if len(self._mine()) >= self.rules.max_positions:
            return skip(f"bereits {self.rules.max_positions} Positionen offen")
        tick = _d(self.mt5.symbol_info_tick(spec.symbol))
        if not tick.get("bid"):
            return skip("kein aktueller Kurs")
        price = float(tick["ask"] if direction == 1 else tick["bid"])
        if direction * (price - stop) <= 0:
            return skip("Kurs liegt bereits jenseits des Stops", kurs=price, stop=stop)
        has_tp = target is not None and np.isfinite(target)
        if has_tp and direction * (target - price) <= 0:
            return skip("Kurs liegt bereits jenseits des Ziels", kurs=price, ziel=target)
        dist = abs(price - stop)
        min_dist = spec.stops_level * spec.point
        if dist <= min_dist or (has_tp and abs(target - price) <= min_dist):
            return skip("Stop/Ziel zu nah am Kurs (Mindestabstand des Brokers)", abstand=dist, mindestabstand=min_dist)
        try:
            rate = float(conv.rate(spec.currency_profit, account_ccy)[0])
        except KeyError:
            rate = float("nan")
        if not np.isfinite(rate) or rate <= 0:
            return skip(f"kein Umrechnungskurs {spec.currency_profit}->{account_ccy}")
        acc = self._account()
        risk_eur = float(acc["balance"]) * self.rules.risk_per_trade
        comm_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * price * spec.contract_size) * rate
        loss_per_lot = dist * spec.contract_size * rate + comm_lot
        vol = round_volume_down(spec, risk_eur / loss_per_lot)
        if vol <= 0:
            return skip(f"Mindestlot: {spec.volume_min:g} Lot würde {loss_per_lot * spec.volume_min:.2f} € riskieren "
                        f"(erlaubt {risk_eur:.2f} €)")
        otype = self.mt5.ORDER_TYPE_BUY if direction == 1 else self.mt5.ORDER_TYPE_SELL
        margin = self.mt5.order_calc_margin(otype, spec.symbol, vol, price)
        if margin is None or margin > float(acc.get("margin_free", 0)):
            return skip("nicht genug freie Margin", margin=margin, frei=acc.get("margin_free"))
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": spec.symbol, "volume": vol, "type": otype,
               "price": price, "sl": round(float(stop), spec.digits), "tp": round(float(target), spec.digits) if has_tp else 0.0,
               "deviation": self.deviation, "magic": MAGIC, "comment": f"TB {cand.timeframe} {cand.strategy}"[:31],
               "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(spec.symbol)}
        before = set(self._mine(spec.symbol))
        ok, res, msg = self._send(req)
        if not ok:
            return skip(msg)
        ticket = int(getattr(res, "order", 0) or getattr(res, "deal", 0))
        new = set(self._mine(spec.symbol)) - before - {int(k) for k in self.st["positions"]}
        if ticket not in new and len(new) == 1:
            ticket = new.pop()             # Positionsnummer kann von der Ordernummer abweichen (Netting-Konten)
        rec = {"ticket": ticket, "kandidat": cand.key, "symbol": spec.symbol, "signal": signal_key,
               "direction": direction, "volumen": vol, "kurs": float(getattr(res, "price", 0) or price),
               "stop": req["sl"], "ziel": req["tp"] or None, "risiko_eur": round(vol * loss_per_lot, 2),
               "entry_bar_server": int(entry_bar_server), "eroeffnet_utc": pd.Timestamp(now).isoformat()}
        self.st["positions"][str(ticket)] = rec
        self.store.event("order_eroeffnet", now, **rec)
        self.log(f"  ORDER (Demo) {cand.key}: {'Kauf' if direction == 1 else 'Verkauf'} {vol} Lot @ {rec['kurs']}, "
                 f"Stop {rec['stop']}" + (f", Ziel {rec['ziel']}" if rec["ziel"] else ""))
        return rec

    # ------------------------------------------------------------------ Ausstieg
    def _close(self, pos, rec: dict, reason: str, now) -> bool:
        tick = _d(self.mt5.symbol_info_tick(pos.symbol))
        buy = int(pos.type) == self.mt5.ORDER_TYPE_BUY
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": float(pos.volume),
               "type": self.mt5.ORDER_TYPE_SELL if buy else self.mt5.ORDER_TYPE_BUY, "position": int(pos.ticket),
               "price": float(tick.get("bid") if buy else tick.get("ask")), "deviation": self.deviation,
               "magic": MAGIC, "comment": f"TB exit {reason}"[:31], "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self._filling(pos.symbol)}
        ok, res, msg = self._send(req)
        self.store.event("order_geschlossen" if ok else "schliessen_fehlgeschlagen", now, kandidat=rec["kandidat"],
                         ticket=rec["ticket"], grund=reason, meldung=msg)
        if ok:
            self.log(f"  ORDER (Demo) {rec['kandidat']}: Position {rec['ticket']} geschlossen ({reason})")
        return ok

    def manage(self, cand, bars: pd.DataFrame, sig, now) -> None:
        """Nach jeder neuen abgeschlossenen Kerze: Ausstiegssignal/Zeitstopp prüfen, beim Broker geschlossene
        Positionen (Stop/Ziel/manuell) erfassen."""
        mine = self._mine(cand.symbol)
        srv = bars["time_server"].to_numpy(dtype="int64")
        n = len(srv)
        for t, rec in list(self.st["positions"].items()):
            if rec["kandidat"] != cand.key:
                continue
            pos = mine.get(int(t))
            if pos is None:
                deals = [d for d in (self.mt5.history_deals_get(position=int(t)) or ()) if getattr(d, "entry", 0) != 0]
                result = round(sum(float(getattr(d, "profit", 0)) + float(getattr(d, "commission", 0))
                                   + float(getattr(d, "swap", 0)) for d in deals), 2) if deals else None
                rec.update(geschlossen_utc=pd.Timestamp(now).isoformat(), ergebnis_eur=result)
                self.st["closed"].append(rec)
                del self.st["positions"][t]
                self.store.event("position_beendet", now, kandidat=cand.key, ticket=rec["ticket"], ergebnis_eur=result,
                                 hinweis="beim Broker geschlossen (Stop, Ziel, Ausstieg oder von Hand)")
                continue
            e = int(np.searchsorted(srv, rec["entry_bar_server"]))      # Einstiegskerze im Fenster
            if e >= n:
                continue                                                # Einstiegskerze noch nicht abgeschlossen
            arr = sig.exit_long if rec["direction"] == 1 else sig.exit_short
            reason = None
            if arr is not None and np.asarray(arr[e:], dtype=bool).any():
                reason = "Ausstiegssignal"
            elif sig.max_bars and n - e >= sig.max_bars:
                reason = "Zeitstopp"
            if reason:
                self._close(pos, rec, reason, now)
