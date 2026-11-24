"""Telegram-Signale automatisch handeln (Freigabe 23.11.2026, ohne Ja je Order): auf dem Demokonto oder – nur wenn
in telegram.toml konto = "live" und die eigene Live-Kontonummer eingetragen ist – auf genau diesem Live-Konto mit
fester Lotgröße.

Ablauf je Signal:
  1. Nachricht erkannt (parser.parse_signal) -> Status "wartend"
  2. Sobald der Kurs in der Einstiegszone liegt (oder höchstens toleranz_r x Risiko daneben) und Chance/Risiko
     mindestens min_crv beträgt: Marktorder mit Stop und Ziel beim Broker -> "offen"
     Kommt der Kurs nicht binnen einstieg_warten_min in die Zone -> "abgelaufen"; liegt er jenseits des Stops ->
     "verworfen".
  3. Folgenachrichten des Kanals (Teilgewinn, Stop auf Einstieg, neuer Stop, schließen, verwerfen) wirken auf das
     Signal, auf das geantwortet wurde, sonst auf das jüngste Signal (gleiches Symbol, falls genannt).
  4. Beim Broker geschlossene Positionen (Stop, Ziel, von Hand) -> "beendet" mit Ergebnis.

Grenzen (fest im Code begrenzt, siehe config.py): Demokonto oder das eingetragene Live-Konto (execution.py prüft vor
JEDER Order), Live nur mit fester Lotgröße bis 0,05, Risiko je Signal
vom Kontostand, Tagesverlust (realisiert + offenes Risiko), Drawdown-Stopp mit ausdrücklicher Freigabe, höchstens
max_positionen offene Positionen und max_signale_pro_tag Einstiege, Handelsstopp des Bots gilt auch hier.
Orders gehen ausschließlich über tradingbot/execution.py (eigene Magic-Nummer MAGIC_TELEGRAM).
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from ..execution import (MAGIC_TELEGRAM, account_allowed, close_manual_position, modify_position_sltp,
                         send_manual_order)
from ..paper.store import PaperStore
from . import parser as P
from .config import TelegramConfig

RELEASE_FILE = "handel_freigeben.anfrage"
STOP_FILE = "stop.anfrage"
KEEP_IDS = 2000


class SignalStore(PaperStore):
    """Wie die Ablage der Beobachtung, aber unter <data>/telegram/."""

    def __init__(self, data_root: str | Path):
        self.dir = Path(data_root) / "telegram"

    def state(self) -> dict:
        st = self.read("state.json")
        if not isinstance(st, dict):
            st = {}
        st.setdefault("verarbeitet", [])
        st.setdefault("signale", {})
        st.setdefault("konto", {"tag": None, "tag_start": None, "gesperrt": False, "hoechststand": None,
                                "gestoppt": False, "grund": None})
        return st


def _d(obj) -> dict:
    return {} if obj is None else (obj._asdict() if hasattr(obj, "_asdict") else dict(obj))


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class SignalTrader:
    def __init__(self, mt5, cfg: TelegramConfig, store: SignalStore | None = None, log=print):
        self.mt5, self.cfg, self.log = mt5, cfg, log
        self.store = store or SignalStore(cfg.data_root)
        self.st = self.store.state()

    # ------------------------------------------------------------------ Hilfen
    def save(self) -> None:
        self.st["verarbeitet"] = self.st["verarbeitet"][-KEEP_IDS:]
        self.store.save_state(self.st)

    def event(self, kind: str, now, **kw) -> None:
        self.store.event(kind, now, **kw)

    def _say(self, text: str) -> None:
        self.log(text)

    def _positions(self) -> dict:
        return {int(p.ticket): p for p in (self.mt5.positions_get() or ()) if getattr(p, "magic", None) == MAGIC_TELEGRAM}

    def _sym(self, name: str) -> dict:
        try:
            self.mt5.symbol_select(name, True)
        except Exception:                                             # noqa: BLE001 – Fake ohne symbol_select
            pass
        return _d(self.mt5.symbol_info(name))

    @staticmethod
    def _floor_volume(info: dict, vol: float) -> float:
        step = float(info.get("volume_step") or 0.01)
        vmin, vmax = float(info.get("volume_min") or step), float(info.get("volume_max") or 1e9)
        v = math.floor(vol / step + 1e-9) * step
        v = min(round(v, 8), vmax)
        return v if v >= vmin - 1e-12 else 0.0

    def _loss_per_lot(self, info: dict, direction: int, price: float, stop: float) -> float:
        otype = self.mt5.ORDER_TYPE_BUY if direction == 1 else self.mt5.ORDER_TYPE_SELL
        try:
            v = self.mt5.order_calc_profit(otype, info["name"], 1.0, price, stop)
            if v is not None and v < 0:
                return float(-v)
        except Exception:                                             # noqa: BLE001
            pass
        ts, tv = float(info.get("trade_tick_size") or 0), float(info.get("trade_tick_value") or 0)
        return abs(price - stop) / ts * tv if ts > 0 and tv > 0 else float("nan")

    def _bot_halted(self) -> str | None:
        ex = PaperStore(self.cfg.data_root).state().get("execution") or {}
        return ex.get("halt_reason") if ex.get("halted") else None

    # ------------------------------------------------------------------ Konto
    def account_checks(self, now) -> None:
        k = self.st["konto"]
        rel = self.store.dir / RELEASE_FILE
        acc = _d(self.mt5.account_info())
        if rel.exists():
            rel.unlink(missing_ok=True)
            k.update(gestoppt=False, grund=None, hoechststand=acc.get("equity"))
            self.event("handel_freigegeben", now, kontostand=acc.get("equity"))
            self._say("Handel wieder freigegeben (Drawdown ab jetzt neu gemessen)")
        if not acc:
            return
        day = str(_ts(now).date())
        flow = self._cash_flows(now)
        if k["tag"] != day:
            k.update(tag=day, tag_start=float(acc["balance"]), gesperrt=False)
        elif flow and k["tag_start"]:
            k["tag_start"] = max(float(k["tag_start"]) + flow, 0.0)
            if k["gesperrt"] and k["tag_start"] - float(acc["balance"]) < self.cfg.max_daily_loss_pct / 100 * k["tag_start"]:
                k["gesperrt"] = False
        if float(acc["balance"]) > (k["tag_start"] or 0):           # Einzahlung oder Gewinn: Tagesbasis anheben
            k["tag_start"] = float(acc["balance"])
        start = k["tag_start"] or float(acc["balance"])
        if not k["gesperrt"] and start - float(acc["balance"]) >= self.cfg.max_daily_loss_pct / 100 * start:
            k["gesperrt"] = True
            self.event("tageslimit_erreicht", now, verlust=round(start - float(acc["balance"]), 2))
            self._say(f"Tagesverlustgrenze {self.cfg.max_daily_loss_pct:g} % erreicht – heute keine neuen Signale")
        eq = float(acc.get("equity") or acc["balance"])
        if flow and k["hoechststand"]:
            # Ein-/Auszahlung: Höchststand um denselben Betrag verschieben, der Drawdown misst nur Handelsergebnisse
            k["hoechststand"] = max(float(k["hoechststand"]) + flow, 0.0)
            self.event("einzahlung" if flow > 0 else "auszahlung", now, betrag=round(flow, 2),
                       hoechststand_neu=round(k["hoechststand"], 2))
            self._say(f"{'Einzahlung' if flow > 0 else 'Auszahlung'} {flow:+.2f} erkannt – Höchststand für den "
                      f"Drawdown jetzt {k['hoechststand']:.2f}")
            if k["gestoppt"] and k["hoechststand"] > 0 and \
                    (k["hoechststand"] - eq) / k["hoechststand"] < self.cfg.max_drawdown_pct / 100:
                k.update(gestoppt=False, grund=None)
                self.event("handel_freigegeben", now, grund="Einzahlung", kontostand=eq)
                self._say("Handel wieder frei: nach der Einzahlung liegt der Drawdown unter der Grenze")
        peak = max(k["hoechststand"] or eq, eq)
        k["hoechststand"] = peak
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd >= self.cfg.max_drawdown_pct / 100 and not k["gestoppt"]:
            k.update(gestoppt=True, grund=f"Drawdown {100 * dd:.1f} % >= {self.cfg.max_drawdown_pct:g} %")
            self.event("handel_gestoppt", now, grund=k["grund"], hoechststand=round(peak, 2), kontostand=round(eq, 2))
            self._say(f"HANDEL GESTOPPT: {k['grund']}. Weiter nur mit: python telegram_signale.py --handel-freigeben")

    def _cash_flows(self, now) -> float:
        """Summe neuer Ein-/Auszahlungen (MT5-Deals vom Typ BALANCE = 2) seit der letzten Prüfung. Beim allerersten
        Aufruf werden vorhandene Buchungen nur gemerkt, nicht angerechnet."""
        k = self.st["konto"]
        now = _ts(now)
        first = not k.get("kasse_seit")
        since = _ts(k["kasse_seit"]) if not first else now - pd.Timedelta(days=30)
        try:
            deals = self.mt5.history_deals_get(since.tz_localize(None).to_pydatetime(),
                                               (now + pd.Timedelta(days=1)).tz_localize(None).to_pydatetime()) or ()
        except Exception:                                             # noqa: BLE001 – ohne Historie keine Anpassung
            return 0.0
        seen = set(k.get("kasse_tickets") or [])
        total = 0.0
        for dl in deals:
            if int(getattr(dl, "type", -1)) != 2:
                continue
            t = int(getattr(dl, "ticket", 0))
            if t in seen:
                continue
            seen.add(t)
            if not first:
                total += float(getattr(dl, "profit", 0) or 0)
        k["kasse_tickets"] = sorted(seen)[-500:]
        k["kasse_seit"] = (now - pd.Timedelta(days=2)).isoformat()   # Überlappung, doppelte über die Ticketnummer
        return total

    # ------------------------------------------------------------------ Nachrichten
    def on_message(self, msg_id: int, text: str, date_utc, reply_to: int | None = None, now=None) -> str:
        now = _ts(now if now is not None else pd.Timestamp.now(tz="UTC"))
        if msg_id in self.st["verarbeitet"]:
            return "schon verarbeitet"
        self.st["verarbeitet"].append(msg_id)
        try:
            sig = P.parse_signal(text or "")
            if sig:
                return self._new_signal(msg_id, sig, _ts(date_utc), now)
            acts = P.parse_followup(text or "")
            if acts:
                return self._followup(msg_id, text, acts, reply_to, now)
            return "keine Handelsnachricht"
        finally:
            self.save()

    def _new_signal(self, msg_id: int, sig: P.Signal, date: pd.Timestamp, now: pd.Timestamp) -> str:
        rec = {"nachricht": msg_id, "gesendet_utc": date.isoformat(), "empfangen_utc": now.isoformat(),
               "signal": sig.to_dict(), "symbol": self.cfg.broker_symbol(sig.symbol), "status": "wartend",
               "grund": None, "ticket": None, "folgen": []}
        self.st["signale"][str(msg_id)] = rec
        side = "KAUF" if sig.direction == 1 else "VERKAUF"
        zone = f"{sig.entry_lo}-{sig.entry_hi}" if sig.entry_lo is not None else "Markt"
        self._say(f"SIGNAL {msg_id}: {side} {sig.symbol} Zone {zone}, Stop {sig.stop}, Ziel(e) {sig.targets}")
        self.event("signal", now, nachricht=msg_id, **sig.to_dict())
        age = (now - date).total_seconds()
        if age > self.cfg.max_signal_age_s:
            return self._finish(rec, "verworfen", f"Nachricht ist {age:.0f} s alt (> {self.cfg.max_signal_age_s} s)", now)
        if self.cfg.allowed and sig.symbol not in self.cfg.allowed:
            return self._finish(rec, "verworfen", f"{sig.symbol} steht nicht in [symbole] erlaubt", now)
        self._try_entry(rec, now)
        return rec["status"]

    def _finish(self, rec: dict, status: str, grund: str, now) -> str:
        rec.update(status=status, grund=grund, ende_utc=_ts(now).isoformat())
        self.event(f"signal_{status}", now, nachricht=rec["nachricht"], grund=grund)
        self._say(f"  Signal {rec['nachricht']} {status}: {grund}")
        return status

    # ------------------------------------------------------------------ Einstieg
    def _blocked(self, now) -> str | None:
        k = self.st["konto"]
        if not self.cfg.orders_enabled:
            return "Orders ausgeschaltet (telegram.toml: orders = false) – nur Protokoll"
        acc = _d(self.mt5.account_info())
        why = account_allowed(acc, self.cfg.live_login)
        if why:
            return why
        if _d(self.mt5.terminal_info()).get("trade_allowed") is False:
            return "Algo-Trading ist in MT5 ausgeschaltet (Schaltfläche 'Algo Trading')"
        if k["gestoppt"]:
            return f"Handel gestoppt: {k['grund']}"
        if k["gesperrt"]:
            return "Tagesverlustgrenze erreicht"
        bot = self._bot_halted()
        if bot:
            return f"Handelsstopp des Bots aktiv: {bot}"
        return None

    def _try_entry(self, rec: dict, now) -> None:
        now = _ts(now)
        s = rec["signal"]
        d, stop = int(s["richtung"]), float(s["stop"])
        lo, hi = s["zone"]
        waited = (now - _ts(rec["empfangen_utc"])).total_seconds() / 60
        if waited > self.cfg.entry_wait_min:
            self._finish(rec, "abgelaufen", f"Kurs kam nicht binnen {self.cfg.entry_wait_min} Min. in die Einstiegszone", now)
            return
        why = self._blocked(now)
        if why:
            # ohne Demokonto/Algo-Trading weiter warten (kann sich ändern), sonst verwerfen
            if why.startswith(("kein Demokonto", "eingeloggt ist Konto", "Algo-Trading")):
                if rec.get("wartet") != why:
                    rec["wartet"] = why
                    self._say(f"  Signal {rec['nachricht']} wartet: {why}")
                    self.event("signal_wartet", now, nachricht=rec["nachricht"], grund=why)
                return
            self._finish(rec, "verworfen", why, now)
            return
        info = self._sym(rec["symbol"])
        if not info:
            self._finish(rec, "verworfen", f"Symbol {rec['symbol']} gibt es beim Broker nicht "
                                           f"(telegram.toml [symbole] namen/suffix)", now)
            return
        tick = _d(self.mt5.symbol_info_tick(rec["symbol"]))
        if not tick.get("bid"):
            return                                                  # kein Kurs (Markt zu) – weiter warten
        price = float(tick["ask"] if d == 1 else tick["bid"])
        if d * (price - stop) <= 0:
            self._finish(rec, "verworfen", f"Kurs {price} liegt schon jenseits des Stops {stop}", now)
            return
        targets = s["ziele"]
        target = (float(targets[0] if self.cfg.target == "erstes" else targets[-1])) if targets else None
        if target is not None and d * (target - price) <= 0:
            self._finish(rec, "verworfen", f"Kurs {price} hat das Ziel {target} schon erreicht", now)
            return
        if lo is not None:
            near = lo if d == 1 else hi                                # Zonenrand näher am Stop
            far = hi if d == 1 else lo
            tol = self.cfg.tolerance_r * abs(near - stop)
            if d * (price - far) > tol:
                if rec.get("wartet") != "zone":
                    rec["wartet"] = "zone"
                    self._say(f"  Signal {rec['nachricht']} wartet auf die Einstiegszone {lo}-{hi} (Kurs {price})")
                return
        rr = d * (target - price) / (d * (price - stop)) if target is not None else float("inf")
        if rr < self.cfg.min_rr:
            if rec.get("wartet") != "crv":
                rec["wartet"] = "crv"
                self._say(f"  Signal {rec['nachricht']} wartet: Chance/Risiko {rr:.2f} < {self.cfg.min_rr:g}")
            return
        mine = self._positions()
        if len(mine) >= self.cfg.max_positions:
            self._finish(rec, "verworfen", f"bereits {len(mine)} Telegram-Position(en) offen "
                                           f"(Grenze {self.cfg.max_positions})", now)
            return
        day = str(now.date())
        opened_today = [r for r in self.st["signale"].values()
                        if r.get("eroeffnet_utc", "")[:10] == day]
        if len(opened_today) >= self.cfg.max_signals_per_day:
            self._finish(rec, "verworfen", f"heute schon {len(opened_today)} Signale gehandelt "
                                           f"(Grenze {self.cfg.max_signals_per_day})", now)
            return
        acc = _d(self.mt5.account_info())
        bal = float(acc["balance"])
        risk_money = bal * self.cfg.risk_pct / 100
        lpl = self._loss_per_lot(info, d, price, stop)
        if not (lpl > 0):
            self._finish(rec, "verworfen", "Verlust je Lot nicht berechenbar", now)
            return
        vol = self._floor_volume(info, self.cfg.lot if self.cfg.lot > 0 else risk_money / lpl)
        if self.cfg.lot > 0 and vol > 0 and vol * lpl > risk_money + 1e-9:
            self._finish(rec, "verworfen", f"{vol:g} Lot würden bis zum Stop {vol * lpl:.2f} riskieren, mehr als "
                                           f"{self.cfg.risk_pct:g} % des Kontos ({risk_money:.2f})", now)
            return
        if vol <= 0:
            self._finish(rec, "verworfen", f"Mindestlot {info.get('volume_min')} würde "
                                           f"{lpl * float(info.get('volume_min') or 0.01):.2f} riskieren "
                                           f"(erlaubt {risk_money:.2f})", now)
            return
        risk = vol * lpl
        k = self.st["konto"]
        start = k["tag_start"] or bal
        realized = max(0.0, start - bal)
        open_risk = sum(float(r.get("risiko") or 0) for r in self.st["signale"].values() if r["status"] == "offen")
        limit = self.cfg.max_daily_loss_pct / 100 * start
        if realized + open_risk + risk > limit + 1e-9:
            self._finish(rec, "verworfen", f"Tagesrisiko {realized + open_risk + risk:.2f} über der Grenze "
                                           f"{limit:.2f} ({self.cfg.max_daily_loss_pct:g} %)", now)
            return
        otype = self.mt5.ORDER_TYPE_BUY if d == 1 else self.mt5.ORDER_TYPE_SELL
        margin = self.mt5.order_calc_margin(otype, rec["symbol"], vol, price)
        if margin is None or margin > float(acc.get("margin_free") or 0):
            self._finish(rec, "verworfen", f"nicht genug freie Margin ({margin} benötigt, "
                                           f"{acc.get('margin_free')} frei)", now)
            return
        digits = int(info.get("digits") or 2)
        res = send_manual_order(self.mt5, rec["symbol"], d, vol, stop, target, digits, self.cfg.deviation_points,
                                f"TG {rec['nachricht']}", magic=MAGIC_TELEGRAM,
                                live_login=self.cfg.live_login)
        if not res["ok"]:
            self._finish(rec, "verworfen", f"Order nicht ausgeführt: {res['meldung']}", now)
            return
        ticket = res["ticket"]
        pos = self._positions()
        if ticket not in pos:
            cand = [t for t, p in pos.items() if getattr(p, "comment", "") == f"TG {rec['nachricht']}"[:31]]
            if len(cand) == 1:
                ticket = cand[0]
        rec.update(status="offen", ticket=ticket, einstieg=res["kurs"], volumen=vol, stop=round(stop, digits),
                   ziel=round(target, digits) if target is not None else None, risiko=round(risk, 2), digits=digits, eroeffnet_utc=now.isoformat(),
                   wartet=None)
        self.event("order_eroeffnet", now, nachricht=rec["nachricht"], ticket=ticket, symbol=rec["symbol"],
                   richtung=d, volumen=vol, kurs=res["kurs"], stop=rec["stop"], ziel=rec["ziel"], risiko=rec["risiko"])
        self._say(f"  ORDER (Demo) {'Kauf' if d == 1 else 'Verkauf'} {vol:g} Lot {rec['symbol']} @ {res['kurs']}, "
                  f"Stop {rec['stop']}, Ziel {rec['ziel'] or 'offen'}, Risiko {risk:.2f} {acc.get('currency', '')}")

    # ------------------------------------------------------------------ Folgenachrichten
    def _target_signal(self, text: str, reply_to: int | None, now) -> dict | None:
        sigs = self.st["signale"]
        if reply_to is not None and str(reply_to) in sigs:
            return sigs[str(reply_to)]
        sym = P.find_symbol(text)
        horizon = _ts(now) - pd.Timedelta(hours=self.cfg.followup_valid_h)
        live = [r for r in sigs.values() if r["status"] in ("wartend", "offen")
                and _ts(r["empfangen_utc"]) >= horizon and (sym is None or r["signal"]["symbol"] == sym)]
        return max(live, key=lambda r: int(r["nachricht"])) if live else None

    def _followup(self, msg_id: int, text: str, acts, reply_to, now) -> str:
        acts = [a for a in acts if a[0] != P.INFO]
        if not acts:
            return "Info-Nachricht"
        rec = self._target_signal(text, reply_to, now)
        if rec is None:
            self.event("folge_ohne_signal", now, nachricht=msg_id, text=text[:200])
            return "Folgenachricht ohne offenes Signal"
        out = []
        for act, val in acts:
            out.append(self._apply(rec, act, val, msg_id, text, now))
        return "; ".join(out)

    def _apply(self, rec: dict, act: str, val, msg_id: int, text: str, now) -> str:
        mode = self.cfg.followups
        note = {"nachricht": msg_id, "aktion": act, "wert": val, "utc": _ts(now).isoformat(), "text": text[:200]}
        rec["folgen"].append(note)

        def done(result: str) -> str:
            note["ergebnis"] = result
            self.event("folgenachricht", now, signal=rec["nachricht"], **note)
            self._say(f"  Folgenachricht zu Signal {rec['nachricht']} ({act}): {result}")
            return result

        if mode == "aus" or (mode == "nur_be_und_schliessen" and act == P.PARTIAL):
            return done("ignoriert (Einstellung folgenachrichten)")
        if rec["status"] == "wartend":
            if act in (P.CLOSE, P.CANCEL):
                self._finish(rec, "verworfen", "vom Kanal abgesagt bzw. geschlossen, bevor der Einstieg kam", now)
                return done("Signal verworfen")
            return done("Signal noch nicht eröffnet – nichts zu tun")
        if act == P.CANCEL:
            return done("Position ist schon offen – \"cancel\" wird nicht als Schließen gedeutet")
        pos = self._positions().get(int(rec["ticket"] or 0))
        if pos is None:
            return done("Position ist nicht mehr offen")
        info = self._sym(pos.symbol)
        d = 1 if int(pos.type) == self.mt5.ORDER_TYPE_BUY else -1
        digits = int(info.get("digits") or rec.get("digits") or 2)
        if act == P.CLOSE:
            res = close_manual_position(self.mt5, int(pos.ticket), self.cfg.deviation_points, f"TG exit {msg_id}",
                                        magic=MAGIC_TELEGRAM, live_login=self.cfg.live_login)
            return done(f"geschlossen @ {res['kurs']}" if res["ok"] else f"Schließen fehlgeschlagen: {res['meldung']}")
        if act == P.PARTIAL:
            pct = float(val) if val else self.cfg.partial_pct
            vol = self._floor_volume(info, float(pos.volume) * pct / 100)
            rest = round(float(pos.volume) - vol, 8)
            if vol <= 0 or rest < float(info.get("volume_min") or 0.01) - 1e-12:
                if pct < 50:
                    return done(f"Position mit {pos.volume:g} Lot ist zu klein für {pct:g} % – läuft voll weiter")
                # Mindestlot lässt sich nicht teilen: der Kanal nimmt den größeren Teil -> ganz schließen
                res = close_manual_position(self.mt5, int(pos.ticket), self.cfg.deviation_points,
                                            f"TG partial {msg_id}", magic=MAGIC_TELEGRAM,
                                            live_login=self.cfg.live_login)
                return done(f"{pos.volume:g} Lot nicht teilbar – ganz geschlossen @ {res['kurs']}" if res["ok"]
                            else f"Schließen fehlgeschlagen: {res['meldung']}")
            res = close_manual_position(self.mt5, int(pos.ticket), self.cfg.deviation_points, f"TG partial {msg_id}",
                                        magic=MAGIC_TELEGRAM, volume=vol, live_login=self.cfg.live_login)
            if not res["ok"]:
                return done(f"Teilgewinn fehlgeschlagen: {res['meldung']}")
            rec["risiko"] = round(float(rec.get("risiko") or 0) * rest / float(pos.volume), 2)
            rec["volumen"] = rest
            return done(f"{vol:g} Lot geschlossen @ {res['kurs']}, {rest:g} Lot laufen weiter")
        # Stop ändern
        new_sl = float(pos.price_open) if act == P.BREAKEVEN else float(val)
        cur = float(pos.sl or 0)
        if cur and d * (new_sl - cur) <= 0:
            return done(f"Stop {cur} ist schon gleich gut oder besser als {new_sl} – unverändert")
        tick = _d(self.mt5.symbol_info_tick(pos.symbol))
        px = float(tick.get("bid") if d == 1 else tick.get("ask") or 0)
        gap = float(info.get("trade_stops_level") or 0) * float(info.get("point") or 0)
        if not px or d * (px - new_sl) <= gap:
            return done(f"Kurs {px} ist zu nah an oder jenseits von {new_sl} – Stop unverändert")
        res = modify_position_sltp(self.mt5, int(pos.ticket), new_sl, float(pos.tp or 0) or None, digits,
                                   magic=MAGIC_TELEGRAM, live_login=self.cfg.live_login)
        if not res["ok"]:
            return done(f"Stop ändern fehlgeschlagen: {res['meldung']}")
        rec["stop"] = round(new_sl, digits)
        if d * (new_sl - float(pos.price_open)) >= 0:
            rec["risiko"] = 0.0
        else:
            rec["risiko"] = round(float(rec.get("risiko") or 0) * abs(pos.price_open - new_sl)
                                  / max(abs(pos.price_open - cur), 1e-12), 2) if cur else rec.get("risiko")
        return done(f"Stop auf {round(new_sl, digits)} gesetzt")

    # ------------------------------------------------------------------ Durchlauf
    def tick(self, now=None) -> None:
        now = _ts(now if now is not None else pd.Timestamp.now(tz="UTC"))
        self.account_checks(now)
        mine = self._positions()
        for rec in list(self.st["signale"].values()):
            if rec["status"] == "wartend":
                self._try_entry(rec, now)
            elif rec["status"] == "offen" and int(rec["ticket"] or 0) not in mine:
                deals = [x for x in (self.mt5.history_deals_get(position=int(rec["ticket"])) or ())
                         if getattr(x, "entry", 0) != 0]
                result = round(sum(float(getattr(x, "profit", 0)) + float(getattr(x, "commission", 0))
                                   + float(getattr(x, "swap", 0)) for x in deals), 2) if deals else None
                rec["ergebnis"] = result
                rec["risiko"] = 0.0
                self._finish(rec, "beendet", f"beim Broker geschlossen, Ergebnis {result}", now)
        self.st["letzter_durchlauf_utc"] = now.isoformat()
        self.save()

    def status_lines(self) -> list[str]:
        k = self.st["konto"]
        lines = [f"Letzter Durchlauf: {str(self.st.get('letzter_durchlauf_utc', '–'))[:19]} UTC",
                 f"Handel: {'GESTOPPT – ' + str(k['grund']) if k['gestoppt'] else 'frei'}"
                 + (" – Tagesgrenze erreicht" if k["gesperrt"] else "")]
        for rec in sorted(self.st["signale"].values(), key=lambda r: int(r["nachricht"]))[-15:]:
            s = rec["signal"]
            lines.append(f"  {rec['empfangen_utc'][:16]}  {'KAUF' if s['richtung'] == 1 else 'VERKAUF'} {s['symbol']} "
                         f"Stop {s['stop']} Ziel {s['ziele'][0] if s['ziele'] else 'offen'} -> {rec['status']}"
                         + (f" (Ticket {rec['ticket']}, {rec.get('volumen')} Lot)" if rec.get("ticket") else "")
                         + (f", Ergebnis {rec['ergebnis']}" if rec.get("ergebnis") is not None else "")
                         + (f" – {rec['grund']}" if rec.get("grund") and rec["status"] != "beendet" else ""))
        return lines
