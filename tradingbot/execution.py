"""Orderausführung auf dem DEMOKONTO (Phase 5, Freigabe des Nutzers vom 21.10.2026).

Dies ist die EINZIGE Stelle im Programm, die Orders an MetaTrader 5 sendet (ein Test stellt das sicher).

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
DEMO = 0                          # ACCOUNT_TRADE_MODE_DEMO
RELEASE_FILE = "handel_freigeben.anfrage"
OK_CODES = (10008, 10009)         # TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE


def _d(obj) -> dict:
    return {} if obj is None else (obj._asdict() if hasattr(obj, "_asdict") else dict(obj))


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
        fm = int(getattr(self.mt5.symbol_info(symbol), "filling_mode", 0) or 0)
        if fm & 1:
            return self.mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, req: dict) -> tuple[bool, object, str]:
        if self._account().get("trade_mode") != DEMO:                # vor JEDER Order erneut prüfen
            self.active, self.reason = False, "kein Demokonto eingeloggt – es werden KEINE Orders gesendet"
            return False, None, self.reason
        chk = self.mt5.order_check(req)
        if chk is None or getattr(chk, "retcode", -1) != 0:
            return False, chk, f"Prüfung abgelehnt: {getattr(chk, 'retcode', None)} {getattr(chk, 'comment', '')} {self.mt5.last_error()}"
        res = self.mt5.order_send(req)
        if res is None or getattr(res, "retcode", -1) not in OK_CODES:
            return False, res, f"Order abgelehnt: {getattr(res, 'retcode', None)} {getattr(res, 'comment', '')} {self.mt5.last_error()}"
        return True, res, "ok"

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
