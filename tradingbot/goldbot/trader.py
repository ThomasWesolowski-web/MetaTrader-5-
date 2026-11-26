"""Gold-Bot: handelt die Order-Block-Strategie (strategie.py) automatisch auf dem DEMOKONTO.

Ablauf
  * alle paar Sekunden: eigene Orders/Positionen verwalten (Ausführung erkennen, Stop auf Einstieg nach Ziel 1,
    Ergebnisse erfassen, Limit-Order löschen, wenn Ziel 1 ohne Ausführung erreicht wurde)
  * nach jeder neuen abgeschlossenen M15-Kerze: Analyse, verfallene/ungültige Limit-Orders löschen, neues Setup prüfen
  * vor jeder Order: Demokonto, Algo-Trading, eigene Grenzen, Handelszeit, Spread, Risiko je Trade, Margin und –
    falls eingeschaltet – die KI-Prüfung (ki.py). Ein Nein der KI heißt: kein Trade.

Teile: Mit teile = 2 werden zwei Orders zu je `lot` gesendet: Teil 1 mit Ziel 1, Teil 2 mit Ziel 2. Ist Teil 1 im
Gewinn geschlossen (oder der Kurs erreicht Ziel 1), wird der Stop von Teil 2 auf den Einstieg gezogen – das ist
"bei Ziel 1 die Hälfte schließen, Stop auf Einstieg". Mit teile = 1: eine Order bis Ziel 2, Stop auf Einstieg bei Ziel 1.

Eigene Grenzen (unabhängig von den anderen Bots): Tagesverlust und Drawdown werden NUR aus den eigenen Trades
(Magic 20261125) gemessen. Die Sperren des Demo-Bots (data/paper) und der Telegram-Signale (data/telegram) werden
weder gelesen noch verändert – und deren Trades lösen hier nichts aus.
Orders gehen ausschließlich über tradingbot/execution.py.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .. import meldungen
from ..execution import (MAGIC_GOLDBOT, account_allowed, cancel_pending_order, close_manual_position,
                         modify_position_sltp, send_manual_order, send_pending_order)
from ..paper.store import PaperStore
from .config import GoldConfig
from .strategie import Analysis, Setup, analyse, to_frame

RELEASE_FILE = "handel_freigeben.anfrage"
STOP_FILE = "stop.anfrage"
BAR_S = 900
KEEP_KEYS = 500


def _d(obj) -> dict:
    return {} if obj is None else (obj._asdict() if hasattr(obj, "_asdict") else dict(obj))


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class GoldStore(PaperStore):
    """Ablage unter <data>/goldbot/ (state.json, ereignisse.jsonl, status.json)."""

    def __init__(self, data_root: str | Path):
        self.dir = Path(data_root) / "goldbot"

    def state(self) -> dict:
        st = self.read("state.json")
        if not isinstance(st, dict):
            st = {}
        st.setdefault("aktiv", None)
        st.setdefault("gehandelt", [])
        st.setdefault("trades", [])
        st.setdefault("tage", {})
        st.setdefault("letzte_kerze", None)
        st.setdefault("konto", {"referenz": None, "kum": 0.0, "hoechst": 0.0, "gestoppt": False, "grund": None,
                                "tag": None, "tag_ergebnis": 0.0, "tag_gesperrt": False})
        return st


class GoldBot:
    def __init__(self, mt5, cfg: GoldConfig, store: GoldStore | None = None, log=print, ai=None):
        self.mt5, self.cfg, self.log = mt5, cfg, log
        self.store = store or GoldStore(cfg.data_root)
        self.st = self.store.state()
        self.ai = ai                      # callable(setup, df, analysis) -> (bool, text); None = ohne KI
        self.last: Analysis | None = None

    # ------------------------------------------------------------------ Hilfen
    def save(self) -> None:
        self.st["gehandelt"] = self.st["gehandelt"][-KEEP_KEYS:]
        self.st["trades"] = self.st["trades"][-300:]
        self.store.save_state(self.st)

    def say(self, text: str, notify: bool = True) -> None:
        self.log(text)
        if notify and self.cfg.notify:
            meldungen.write(self.cfg.data_root, "goldbot", "🥇 Gold-Bot (Demo)\n" + text)

    def event(self, kind: str, now, **kw) -> None:
        self.store.event(kind, now, **kw)

    def _orders(self) -> dict:
        return {int(o.ticket): o for o in (self.mt5.orders_get() or ()) if getattr(o, "magic", None) == MAGIC_GOLDBOT}

    def _positions(self) -> dict:
        return {int(p.ticket): p for p in (self.mt5.positions_get() or ()) if getattr(p, "magic", None) == MAGIC_GOLDBOT}

    def _info(self) -> dict:
        try:
            self.mt5.symbol_select(self.cfg.symbol, True)
        except Exception:                                             # noqa: BLE001
            pass
        return _d(self.mt5.symbol_info(self.cfg.symbol))

    def _tick(self) -> dict:
        return _d(self.mt5.symbol_info_tick(self.cfg.symbol))

    def _result(self, ticket: int) -> float | None:
        deals = [x for x in (self.mt5.history_deals_get(position=int(ticket)) or ()) if getattr(x, "entry", 0) != 0]
        if not deals:
            return None
        return round(sum(float(getattr(x, "profit", 0)) + float(getattr(x, "commission", 0))
                         + float(getattr(x, "swap", 0)) for x in deals), 2)

    def bars(self) -> pd.DataFrame:
        """Abgeschlossene M15-Kerzen (Position 0 = laufende Kerze wird ausgelassen)."""
        rates = self.mt5.copy_rates_from_pos(self.cfg.symbol, self.mt5.TIMEFRAME_M15, 1, int(self.cfg.bars))
        if rates is None or len(rates) == 0:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        return to_frame(rates)

    # ------------------------------------------------------------------ Konto und eigene Grenzen
    def account_checks(self, now) -> None:
        k = self.st["konto"]
        acc = _d(self.mt5.account_info())
        if (self.store.dir / RELEASE_FILE).exists():
            (self.store.dir / RELEASE_FILE).unlink(missing_ok=True)
            k.update(gestoppt=False, grund=None, hoechst=k["kum"], referenz=acc.get("balance") or k["referenz"])
            self.event("handel_freigegeben", now)
            self.say("Handel wieder freigegeben (Drawdown ab jetzt neu gemessen)")
        if not acc:
            return
        if not k["referenz"]:
            k["referenz"] = float(acc["balance"])
        day = str(_ts(now).date())
        if k["tag"] != day:
            k.update(tag=day, tag_ergebnis=0.0, tag_gesperrt=False)
        floating = sum(float(getattr(p, "profit", 0) or 0) for p in self._positions().values())
        loss_today = -(float(k["tag_ergebnis"]) + floating)
        if not k["tag_gesperrt"] and loss_today >= self.cfg.max_daily_loss_pct / 100 * float(acc["balance"]):
            k["tag_gesperrt"] = True
            self.event("tageslimit_erreicht", now, verlust=round(loss_today, 2))
            self.say(f"Tagesverlustgrenze {self.cfg.max_daily_loss_pct:g} % erreicht ({loss_today:.2f}) – heute keine "
                     f"neuen Trades")
        dd = float(k["hoechst"]) - float(k["kum"])
        if not k["gestoppt"] and dd >= self.cfg.max_drawdown_pct / 100 * float(k["referenz"]):
            k.update(gestoppt=True, grund=f"eigener Drawdown {dd:.2f} >= {self.cfg.max_drawdown_pct:g} % von "
                                          f"{float(k['referenz']):.2f}")
            self.event("handel_gestoppt", now, grund=k["grund"])
            self.say(f"HANDEL GESTOPPT: {k['grund']}. Weiter nur mit: python goldbot.py --handel-freigeben")

    def _book(self, result: float | None) -> None:
        if result is None:
            return
        k = self.st["konto"]
        k["kum"] = round(float(k["kum"]) + result, 2)
        k["hoechst"] = max(float(k["hoechst"]), k["kum"])
        k["tag_ergebnis"] = round(float(k["tag_ergebnis"]) + result, 2)

    # ------------------------------------------------------------------ Durchlauf
    def tick(self, now=None) -> Analysis | None:
        now = _ts(now if now is not None else pd.Timestamp.now(tz="UTC"))
        self.account_checks(now)
        self.manage(now)
        df = self.bars()
        new = len(df) and int(df["time"].iloc[-1]) != self.st["letzte_kerze"]
        if new:
            self.st["letzte_kerze"] = int(df["time"].iloc[-1])
            self.last = analyse(df, self.cfg)
            self.store.write_status({"utc": now.isoformat(), "analyse": self.last.lines()})
            self.on_new_bar(self.last, df, now)
        self.st["letzter_durchlauf_utc"] = now.isoformat()
        self.save()
        return self.last if new else None

    def on_new_bar(self, an: Analysis, df: pd.DataFrame, now) -> None:
        tr = self.st["aktiv"]
        if tr and tr["status"] == "wartend":
            why = None
            age = (int(df["time"].iloc[-1]) - int(tr["kerze"])) // BAR_S
            if an.trend != tr["richtung"]:
                why = f"Trend ist nicht mehr {'abwärts' if tr['richtung'] == -1 else 'aufwärts'} ({an.trend_text})"
            elif age >= self.cfg.pending_valid_bars:
                why = f"Limit-Order nach {age} Kerzen nicht ausgeführt – verfallen"
            elif self._friday_closed(now):
                why = "Freitagabend – keine offenen Limit-Orders übers Wochenende"
            else:
                b = [s for s in an.setups if s.plan == "B" and s.direction == tr["richtung"]
                     and s.key not in self.st["gehandelt"]]
                if b:
                    why = "Plan B (Fortsetzung) hat ausgelöst – Limit-Order wird ersetzt"
            if why:
                self.cancel_trade(tr, why, now)
        if self.st["aktiv"]:
            return
        for s in sorted(an.setups, key=lambda s: s.plan != "B"):            # Plan B zuerst (läuft sofort)
            if s.key in self.st["gehandelt"]:
                continue
            self.enter(s, df, an, now)
            break

    def _friday_closed(self, now) -> bool:
        now = _ts(now)
        return now.weekday() == 4 and now.hour >= self.cfg.friday_to_utc or now.weekday() >= 5

    # ------------------------------------------------------------------ Einstieg
    def blocked(self, now) -> str | None:
        k = self.st["konto"]
        now = _ts(now)
        if not self.cfg.orders_enabled:
            return "Orders ausgeschaltet (goldbot.toml: orders = false) – nur Analyse"
        acc = _d(self.mt5.account_info())
        why = account_allowed(acc)                                        # NUR Demokonto
        if why:
            return why
        if self.cfg.demo_login and int(acc.get("login") or 0) != self.cfg.demo_login:
            return f"eingeloggt ist Konto {acc.get('login')}, freigegeben ist nur das Demokonto {self.cfg.demo_login}"
        if _d(self.mt5.terminal_info()).get("trade_allowed") is False:
            return "Algo-Trading ist in MT5 ausgeschaltet (Schaltfläche 'Algo Trading')"
        if k["gestoppt"]:
            return f"Handel gestoppt: {k['grund']}"
        if k["tag_gesperrt"]:
            return "Tagesverlustgrenze erreicht"
        if not (self.cfg.entry_from_utc <= now.hour < self.cfg.entry_to_utc) or self._friday_closed(now):
            return (f"außerhalb der Handelszeit ({self.cfg.entry_from_utc}-{self.cfg.entry_to_utc} Uhr UTC, freitags bis "
                    f"{self.cfg.friday_to_utc} Uhr)")
        day = str(now.date())
        if self.st["tage"].get(day, 0) >= self.cfg.max_trades_per_day:
            return f"heute schon {self.st['tage'][day]} Trades (Grenze {self.cfg.max_trades_per_day})"
        return None

    def _floor(self, info: dict, vol: float) -> float:
        step = float(info.get("volume_step") or 0.01)
        v = round(math.floor(vol / step + 1e-9) * step, 8)
        return v if v >= float(info.get("volume_min") or step) - 1e-12 else 0.0

    def _loss_per_lot(self, info: dict, d: int, price: float, stop: float) -> float:
        otype = self.mt5.ORDER_TYPE_BUY if d == 1 else self.mt5.ORDER_TYPE_SELL
        try:
            v = self.mt5.order_calc_profit(otype, self.cfg.symbol, 1.0, price, stop)
            if v is not None and v < 0:
                return float(-v)
        except Exception:                                             # noqa: BLE001
            pass
        ts, tv = float(info.get("trade_tick_size") or 0), float(info.get("trade_tick_value") or 0)
        return abs(price - stop) / ts * tv if ts > 0 and tv > 0 else float("nan")

    def skip(self, s: Setup, why: str, now, remember: bool = True, notify: bool = True) -> None:
        if remember:
            self.st["gehandelt"].append(s.key)
        self.event("setup_nicht_gehandelt", now, setup=s.to_dict(), grund=why)
        self.say(f"Setup Plan {s.plan} {s.side} nicht gehandelt: {why}", notify=notify)

    def enter(self, s: Setup, df: pd.DataFrame, an: Analysis, now) -> dict | None:
        now = _ts(now)
        self.event("setup", now, **s.to_dict())
        self.log(f"SETUP Plan {s.plan}: {s.side} " + (f"Limit {s.entry}" if s.entry else "Markt")
                 + f", Stop {s.stop}, Ziel 1 {s.ziel1}, Ziel 2 {s.ziel2} – {s.reason}")
        why = self.blocked(now)
        if why:
            # Sperre kann sich ändern (Konto, Algo-Schalter, Uhrzeit, Freigabe) -> Setup nicht verbrauchen und jeden
            # Grund nur einmal melden
            first = self.st.get("letzte_sperre") != why
            self.st["letzte_sperre"] = why
            self.skip(s, why, now, remember=False, notify=first and not why.startswith("außerhalb"))
            return None
        self.st["letzte_sperre"] = None
        info = self._info()
        tick = self._tick()
        if not info or not tick.get("bid"):
            self.skip(s, f"kein Kurs für {self.cfg.symbol} (Symbolname in goldbot.toml prüfen)", now, remember=False)
            return None
        point = float(info.get("point") or 0.01)
        spread = (float(tick["ask"]) - float(tick["bid"])) / point
        if spread > self.cfg.max_spread_points:
            self.skip(s, f"Spread {spread:.0f} Punkte > {self.cfg.max_spread_points}", now, remember=s.plan == "B")
            return None
        d = s.direction
        market = float(tick["ask"] if d == 1 else tick["bid"])
        entry = s.entry if s.plan == "A" else market
        if d * (market - s.stop) <= 0 or d * (s.ziel1 - market) <= 0:
            self.skip(s, f"Kurs {market} liegt schon jenseits von Stop oder Ziel 1", now)
            return None
        if s.plan == "A" and d * (market - entry) <= 0:
            self.skip(s, f"Kurs {market} ist schon im Order Block – keine Limit-Order möglich", now, remember=False,
                      notify=False)
            return None
        rr = d * (s.ziel1 - entry) / (d * (entry - s.stop))
        if rr < self.cfg.min_rr - 1e-9:
            self.skip(s, f"Chance/Risiko zum aktuellen Kurs nur {rr:.2f}", now)
            return None
        lpl = self._loss_per_lot(info, d, entry, s.stop)
        vol = self._floor(info, self.cfg.lot)
        if not (lpl > 0) or vol <= 0:
            self.skip(s, f"Lotgröße {self.cfg.lot} nicht handelbar (Mindestlot {info.get('volume_min')})", now)
            return None
        acc = _d(self.mt5.account_info())
        risk = vol * lpl * self.cfg.parts
        allowed = float(acc["balance"]) * self.cfg.max_risk_pct / 100
        if risk > allowed + 1e-9:
            self.skip(s, f"{self.cfg.parts} x {vol:g} Lot würden bis zum Stop {risk:.2f} riskieren, mehr als "
                         f"{self.cfg.max_risk_pct:g} % ({allowed:.2f})", now)
            return None
        otype = self.mt5.ORDER_TYPE_BUY if d == 1 else self.mt5.ORDER_TYPE_SELL
        margin = self.mt5.order_calc_margin(otype, self.cfg.symbol, vol * self.cfg.parts, entry)
        if margin is None or margin > float(acc.get("margin_free") or 0):
            self.skip(s, f"nicht genug freie Margin ({margin} benötigt)", now, remember=False)
            return None
        ai_text = None
        if self.ai is not None:
            ok, ai_text = self.ai(s, df, an)
            self.event("ki_pruefung", now, setup=s.key, freigabe=ok, begruendung=ai_text)
            if not ok:
                self.skip(s, f"KI sagt Nein – {ai_text}", now)
                return None

        digits = int(info.get("digits") or 2)
        tag = s.key.split(":")[-1][-6:]
        legs_plan = [(1, s.ziel1), (2, s.ziel2)] if self.cfg.parts == 2 else [(1, s.ziel2)]
        legs = []
        for nr, tp in legs_plan:
            comment = f"GB{s.plan} {tag} {nr}/{self.cfg.parts}"
            if s.plan == "A":
                res = send_pending_order(self.mt5, self.cfg.symbol, d, vol, entry, s.stop, tp, digits,
                                         comment=comment)
            else:
                res = send_manual_order(self.mt5, self.cfg.symbol, d, vol, s.stop, tp, digits,
                                        self.cfg.deviation_points, comment, magic=MAGIC_GOLDBOT)
            if not res["ok"]:
                self.say(f"Order Teil {nr} nicht ausgeführt: {res['meldung']}")
                if s.plan == "A":                                    # halbe Limit-Order zurücknehmen
                    for leg in legs:
                        cancel_pending_order(self.mt5, leg["order"])
                    legs = []
                break
            legs.append({"nr": nr, "order": res["ticket"], "position": None if s.plan == "A" else res["ticket"],
                         "volumen": vol, "ziel": tp, "kommentar": comment,
                         "status": "wartend" if s.plan == "A" else "offen",
                         "kurs": None if s.plan == "A" else res.get("kurs")})
        self.st["gehandelt"].append(s.key)
        if not legs:
            self.event("order_fehlgeschlagen", now, setup=s.key)
            return None
        day = str(now.date())
        self.st["tage"][day] = self.st["tage"].get(day, 0) + 1
        self.st["tage"] = dict(list(self.st["tage"].items())[-10:])
        tr = {"key": s.key, "plan": s.plan, "richtung": d, "einstieg": entry, "stop": s.stop, "ziel1": s.ziel1,
              "ziel2": s.ziel2, "risiko": round(risk, 2), "legs": legs, "be": False, "kerze": s.bar_time,
              "status": "wartend" if s.plan == "A" else "offen", "erstellt_utc": now.isoformat(), "grund": s.reason,
              "ki": ai_text}
        self.st["aktiv"] = tr
        self.event("order_gesendet", now, **{k: v for k, v in tr.items() if k != "legs"}, teile=len(legs))
        kind = f"Limit-{'Kauf' if d == 1 else 'Verkauf'} @ {entry:.2f}" if s.plan == "A" else \
            f"{'Kauf' if d == 1 else 'Verkauf'} zum Markt @ {legs[0].get('kurs') or entry:.2f}"
        self.say(f"ORDER Plan {s.plan}: {kind}, {len(legs)} x {vol:g} Lot\n"
                 f"Stop {s.stop:.2f} | Ziel 1 {s.ziel1:.2f} | Ziel 2 {s.ziel2:.2f} | Risiko {risk:.2f} "
                 f"{acc.get('currency', '')}\n{s.reason}" + (f"\nKI: {ai_text}" if ai_text else ""))
        return tr

    # ------------------------------------------------------------------ Verwaltung
    def cancel_trade(self, tr: dict, why: str, now) -> None:
        for leg in tr["legs"]:
            if leg["status"] == "wartend":
                res = cancel_pending_order(self.mt5, leg["order"])
                if res["ok"] or "nicht (mehr) offen" in res["meldung"]:
                    leg["status"] = "geloescht"
                else:
                    self.log(f"Limit-Order {leg['order']} löschen fehlgeschlagen: {res['meldung']}")
        self.event("limit_geloescht", now, setup=tr["key"], grund=why)
        self.say(f"Limit-Order(s) Plan {tr['plan']} gelöscht: {why}")
        self._maybe_finish(tr, now)

    def manage(self, now) -> None:
        tr = self.st["aktiv"]
        if not tr:
            return
        orders, positions = self._orders(), self._positions()
        for leg in tr["legs"]:
            if leg["status"] == "wartend" and leg["order"] not in orders:
                pos = positions.get(leg["order"]) or next(
                    (p for p in positions.values() if int(getattr(p, "identifier", 0) or 0) == leg["order"]
                     or getattr(p, "comment", "") == leg["kommentar"]), None)
                if pos is not None:
                    leg.update(status="offen", position=int(pos.ticket), kurs=float(pos.price_open))
                    tr["status"] = "offen"
                    self.event("limit_ausgefuehrt", now, setup=tr["key"], teil=leg["nr"], kurs=leg["kurs"])
                    self.say(f"Limit-Order Teil {leg['nr']} ausgeführt @ {leg['kurs']:.2f}")
                else:
                    result = self._result(leg["order"])
                    if result is not None:                         # ausgeführt und schon wieder geschlossen
                        leg.update(status="offen", position=leg["order"])
                        tr["status"] = "offen"
                    else:
                        leg["status"] = "geloescht"
                        self.event("limit_verschwunden", now, setup=tr["key"], teil=leg["nr"])
                        self.say(f"Limit-Order Teil {leg['nr']} ist nicht mehr da (von Hand gelöscht?)")
            if leg["status"] == "offen" and leg["position"] not in positions:
                result = self._result(leg["position"])
                leg.update(status="beendet", ergebnis=result)
                self._book(result)
                hit = "Ziel" if (result or 0) > 0 else "Stop" if (result or 0) < 0 else "Einstieg"
                self.event("teil_beendet", now, setup=tr["key"], teil=leg["nr"], ergebnis=result)
                self.say(f"Teil {leg['nr']} geschlossen ({hit}), Ergebnis {result}")
        tick = self._tick()
        d = tr["richtung"]
        px = float((tick.get("bid") if d == 1 else tick.get("ask")) or 0)
        reached = bool(px) and d * (px - tr["ziel1"]) >= 0
        if tr["status"] == "wartend" and reached:
            self.cancel_trade(tr, f"Ziel 1 ({tr['ziel1']}) ohne Ausführung erreicht – Zug verpasst", now)
            return
        leg1 = tr["legs"][0]
        if not tr["be"] and tr["status"] == "offen" and (
                reached or (leg1["status"] == "beendet" and (leg1.get("ergebnis") or 0) > 0)):
            self._breakeven(tr, positions, now)
        self._maybe_finish(tr, now)

    def _breakeven(self, tr: dict, positions: dict, now) -> None:
        info = self._info()
        digits = int(info.get("digits") or 2)
        done = True
        for leg in tr["legs"]:
            pos = positions.get(leg["position"]) if leg["status"] == "offen" else None
            if pos is None:
                continue
            d = tr["richtung"]
            be = float(pos.price_open)
            if float(pos.sl or 0) and d * (float(pos.sl) - be) >= 0:
                continue
            res = modify_position_sltp(self.mt5, int(pos.ticket), be, float(pos.tp or 0) or None, digits,
                                       magic=MAGIC_GOLDBOT)
            if res["ok"]:
                self.say(f"Ziel 1 erreicht – Stop von Teil {leg['nr']} auf Einstieg {be:.2f}")
            else:
                done = False
                self.log(f"Stop auf Einstieg fehlgeschlagen: {res['meldung']}")
        tr["be"] = done
        self.event("stop_auf_einstieg", now, setup=tr["key"], ok=done)

    def _maybe_finish(self, tr: dict, now) -> None:
        if any(leg["status"] in ("wartend", "offen") for leg in tr["legs"]):
            return
        total = sum(leg.get("ergebnis") or 0 for leg in tr["legs"])
        filled = any(leg["status"] == "beendet" for leg in tr["legs"])
        tr.update(status="beendet" if filled else "geloescht", ergebnis=round(total, 2) if filled else None,
                  ende_utc=_ts(now).isoformat())
        self.st["trades"].append(tr)
        self.st["aktiv"] = None
        self.event("trade_beendet", now, setup=tr["key"], status=tr["status"], ergebnis=tr["ergebnis"])
        if filled:
            k = self.st["konto"]
            self.say(f"Trade Plan {tr['plan']} beendet: Ergebnis {total:+.2f} (Gold-Bot gesamt {k['kum']:+.2f})")

    def close_all(self, now, why: str = "von Hand") -> None:
        """Alles des Gold-Bots schließen/löschen (python goldbot.py --alles-schliessen)."""
        for t in self._orders():
            cancel_pending_order(self.mt5, t)
        for t in self._positions():
            close_manual_position(self.mt5, t, self.cfg.deviation_points, "GB exit", magic=MAGIC_GOLDBOT)
        if self.st["aktiv"]:
            self.manage(now)
        self.say(f"Alle Gold-Bot-Orders und -Positionen geschlossen ({why})")

    # ------------------------------------------------------------------ Anzeige
    def status_lines(self) -> list[str]:
        k = self.st["konto"]
        lines = [f"Letzter Durchlauf: {str(self.st.get('letzter_durchlauf_utc', '–'))[:19]} UTC",
                 f"Handel: {'GESTOPPT – ' + str(k['grund']) if k['gestoppt'] else 'frei'}"
                 + (" – Tagesgrenze erreicht" if k["tag_gesperrt"] else ""),
                 f"Ergebnis Gold-Bot gesamt: {float(k['kum']):+.2f} (heute {float(k['tag_ergebnis']):+.2f})"]
        tr = self.st["aktiv"]
        if tr:
            lines.append(f"Aktiv: Plan {tr['plan']} {'KAUF' if tr['richtung'] == 1 else 'VERKAUF'} @ {tr['einstieg']}, "
                         f"Stop {tr['stop']}, Ziele {tr['ziel1']} / {tr['ziel2']} – {tr['status']}"
                         + (" (Stop auf Einstieg)" if tr["be"] else ""))
        for t in self.st["trades"][-10:]:
            lines.append(f"  {t['erstellt_utc'][:16]}  Plan {t['plan']} {'KAUF' if t['richtung'] == 1 else 'VERKAUF'} "
                         f"@ {t['einstieg']} -> {t['status']}"
                         + (f", Ergebnis {t['ergebnis']:+.2f}" if t.get("ergebnis") is not None else ""))
        st = self.store.read("status.json") or {}
        if st.get("analyse"):
            lines += ["Letzte Analyse:"] + ["  " + x for x in st["analyse"]]
        return lines
