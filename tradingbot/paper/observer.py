"""Paper-Trading (Phase 4): Kandidaten mit neuen Live-Kursen beobachten – ohne Orders.

Ablauf je Durchlauf und Kandidat:
  1. Kerzen nur lesend aus MT5 (copy_rates_range) ab Beobachtungsbeginn − Vorlauf. Verwendet werden ausschließlich
     abgeschlossene Kerzen: Kerzenende <= Uhrzeit UND (ein Tick nach dem Kerzenende liegt vor ODER es gibt keine
     aktuellen Ticks). Die laufende Kerze wird nie verwendet.
  2. Signale mit derselben Strategie-Klasse und denselben Parametern wie im Backtest (Phase 3).
  3. Schutz vor Look-ahead und nachträglichen Signalen:
       * nur Signale auf Kerzen, die nach Beobachtungsbeginn begonnen haben;
       * jedes Signal wird beim ersten Erkennen mit Zeitstempel festgehalten ("angenommen");
       * ein Signal auf einer bereits ausgewerteten Kerze, das erst später auftaucht, wird NICHT gehandelt
         (Abweichung, z. B. nachträglich geänderte Kursdaten);
       * ein Signal, das später als max_delay_s nach Kerzenschluss erkannt wird (Programm lief nicht), wird NICHT
         gehandelt ("verspätet").
  4. Ein- und Ausstiege mit derselben Engine wie im Backtest (engine.simulate): Einstieg zur Eröffnung der
     Folgekerze (Long zum Ask = Bid + Spread, plus Slippage), Stop vor Ziel, kein Einstieg über Datenlücken,
     Lücken-Ausführung zum Eröffnungskurs. Abgeschlossene Trades werden eingefroren.
  5. Kosten und Ergebnis je Mindestlot (0,01) in € sowie Referenzkonto 1 000 € mit den Risikoregeln aus RiskRules
     (gleiche Kontosimulation wie Phase 3).

Es werden keine Kurse erfunden: Fehlen Kerzen oder Ticks, wird das protokolliert und nichts ausgeführt.
MT5 wird ausschließlich lesend verwendet (MT5Client enthält keine Order-Funktion).
"""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
import pandas as pd

from .. import features
from .. import indicators as ind
from ..backtest import resources
from ..backtest.account import RiskRules, run_account, swap_amount, swap_nights
from ..backtest.costs import scenarios_for
from ..backtest.engine import simulate
from ..backtest.metrics import compute
from ..config import Config, PaperCandidate
from ..instruments import CurrencyConverter, InstrumentSpec
from ..risk_check import check_symbol
from ..server_time import parse_scheme
from ..sessions import get_calendar
from ..storage import MarketStore, rates_to_frame
from ..strategies import Signals, create
from ..timeframes import seconds
from .store import PaperStore

BAR_COLS = ["time", "time_server", "open", "high", "low", "close", "tick_volume", "spread"]
CONVERSION_TF = "M5"
CHUNK_BARS = 40_000
CHECK_SYMBOLS = ("XAUUSD", "BTCUSD")
CHECK_TFS = ("M1", "M5", "M15", "M30", "H1")
CLOCK_TOLERANCE_S = 120
STOP_FILE = "stop.anfrage"


def _f(x, nd=2):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


class Observer:
    def __init__(self, cfg: Config, client, now_utc_fn=None, log=print):
        self.cfg, self.client, self.log = cfg, client, log
        self.pc = cfg.paper
        self.now_utc_fn = now_utc_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self.store = PaperStore(cfg.data_root)
        self.market = MarketStore(cfg.data_root)
        self.rules = RiskRules()          # wie im Backtest – bewusst nicht über config.toml einstellbar
        self.snap: dict[str, dict] = {}
        self.cycles = 0

    # ------------------------------------------------------------------ Start
    def start(self) -> None:
        doc = self.market.read_json("specs.json", {}) or {}
        if not doc.get("time_scheme"):
            raise RuntimeError("data/specs.json fehlt oder enthält kein Zeitschema – zuerst daten_aktualisieren.py ausführen")
        self.doc = doc
        self.scheme = parse_scheme(doc["time_scheme"]["scheme"])
        for c in self.pc.candidates:
            create(c.strategy, **c.params)          # unbekannte Strategie/Parameter -> Fehler vor dem Start
        self.client.connect()
        acc = self.client.account_info()
        self.account = {"currency": acc["currency"], "leverage": float(acc.get("leverage") or 1),
                        "mode": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(acc.get("trade_mode"), str(acc.get("trade_mode"))),
                        "server": acc.get("server")}
        self.symbols = sorted({c.symbol for c in self.pc.candidates})
        self.specs: dict[str, InstrumentSpec] = {}
        for s in sorted(set(self.symbols) | set(CHECK_SYMBOLS)):
            info = self.client.symbol_info(s)
            if info is None:
                if s in self.symbols:
                    raise RuntimeError(f"Symbol {s} ist im Terminal nicht verfügbar")
                continue
            spec = InstrumentSpec.from_mt5(info)
            stored = (doc.get("instruments") or {}).get(s)
            if stored:
                spec.margin_rate = float(stored.get("margin_rate", 1.0))   # kalibriert in Phase 2
            self.specs[s] = spec
        self.costs = {s: scenarios_for(s)[self.pc.cost_scenario] for s in self.symbols}
        self.state = self.store.state()
        now = self.now_utc_fn()
        self.store.event("programmstart", now, konto=self.account, zeitschema=self.scheme.name,
                         kandidaten=[{"key": c.key, "params": c.params} for c in self.pc.candidates],
                         risikoregeln=asdict(self.rules), kostenszenario=self.pc.cost_scenario)
        self.log(f"Konto: {self.account['mode']}, {self.account['currency']}, Hebel 1:{self.account['leverage']:g} – "
                 f"nur lesender Zugriff, keine Orders")
        try:
            self.contract_check(now)
        except Exception as e:  # die Prüfung darf die Beobachtung nicht verhindern
            self.store.event("fehler", now, bereich="kontraktpruefung", meldung=f"{type(e).__name__}: {e}")

    def close(self) -> None:
        try:
            self.store.event("programmende", self.now_utc_fn(), durchlaeufe=self.cycles)
        except Exception:
            pass
        try:
            self.client.shutdown()
        except Exception:
            pass

    # ------------------------------------------------------------------ Punkt 3: Kontrakt und Mindestlot
    def _live_converter(self) -> CurrencyConverter:
        conv = CurrencyConverter()
        for c in self.doc.get("conversions", []):
            t = self.client.tick(c["symbol"])
            if t and t.get("bid") and t.get("ask"):
                conv.add_live(c["symbol"], c["base"], c["quote"], (t["bid"] + t["ask"]) / 2)
        return conv

    def contract_check(self, now: pd.Timestamp) -> dict:
        conv = self._live_converter()
        out = {"created_utc": now.isoformat(), "source": "live aus MT5 (symbol_info, nur lesend)",
               "account_currency": self.account["currency"], "symbols": {}}
        for sym, spec in self.specs.items():
            tick = self.client.tick(sym) or {}
            if not tick.get("bid"):
                out["symbols"][sym] = {"error": "kein aktueller Kurs"}
                continue
            price = (tick["bid"] + tick["ask"]) / 2
            try:
                rate = float(conv.rate(spec.currency_profit, self.account["currency"])[0])
            except KeyError:
                out["symbols"][sym] = {"error": f"kein Umrechnungskurs {spec.currency_profit}->{self.account['currency']}"}
                continue
            atr = {}
            for tf in CHECK_TFS:
                r = self.client.rates_from_pos(sym, tf, 1, 600)        # ab 1: laufende Kerze ausgelassen
                if r is None or len(r) < 100:
                    atr[tf] = None
                    continue
                a = ind.atr(r["high"].astype(float), r["low"].astype(float), r["close"].astype(float), 14)[-500:]
                a = a[np.isfinite(a)]
                atr[tf] = float(np.median(a)) if len(a) else None
            costs = scenarios_for(sym)[self.pc.cost_scenario]
            res = check_symbol(spec, rate, price, costs, atr, self.rules, (self.doc.get("instruments") or {}).get(sym))
            res["atr_basis"] = "Median ATR(14) der letzten 500 abgeschlossenen Kerzen"
            res["spread_points_now"] = round((tick["ask"] - tick["bid"]) / spec.point)
            out["symbols"][sym] = res
            if res.get("spec_differences"):
                self.store.event("spezifikation_geaendert", now, symbol=sym, unterschiede=res["spec_differences"])
        self.store.write_contract(out)
        return out

    # ------------------------------------------------------------------ Kurse
    def _fetch(self, sym: str, tf: str, frm: int, to: int) -> np.ndarray | None:
        tf_s = seconds(tf)
        parts, begin = [], frm
        while begin <= to:
            end = min(to, begin + CHUNK_BARS * tf_s)
            r = self.client.rates_range(sym, tf, begin, end, retry_empty=False)
            if r is not None and len(r):
                parts.append(r)
            begin = end + 1
        if not parts:
            return None
        r = np.concatenate(parts)
        _, idx = np.unique(r["time"], return_index=True)
        return r[idx]

    def _complete(self, rates: np.ndarray, tf_s: int, now_srv: int, tick_srv: int | None, fresh: bool) -> np.ndarray:
        end = rates["time"].astype("int64") + tf_s
        ok = end <= now_srv
        if tick_srv is not None and fresh:
            ok &= end <= tick_srv          # ein Tick nach Kerzenende -> Kerze sicher abgeschlossen
        return rates[ok]

    def _converter(self, sym: str, bars: pd.DataFrame, tf_s: int, eur: tuple | None) -> CurrencyConverter:
        rates = {}
        if eur is not None:
            rates[eur[0]] = eur[1]
        spec = self.specs[sym]
        if spec.currency_base and spec.currency_base != spec.currency_profit:
            t_end = pd.DatetimeIndex(bars["time"]).as_unit("ns").asi8 + tf_s * 10**9
            rates[sym] = (t_end, bars["close"].to_numpy(dtype=float), spec.currency_base, spec.currency_profit)
        return CurrencyConverter(rates)

    def _eur_rates(self, now_srv: int) -> tuple | None:
        """Umrechnungskurse (z. B. EURUSD M5, nur abgeschlossene Kerzen) ab dem frühesten Beobachtungsbeginn."""
        convs = self.doc.get("conversions", [])
        if not convs:
            return None
        c = convs[0]
        starts = [v["start_server"] for v in self.state["candidates"].values()] or [now_srv]
        r = self._fetch(c["symbol"], CONVERSION_TF, min(starts) - 3 * 86400, now_srv)
        if r is None:
            return None
        r = r[r["time"].astype("int64") + seconds(CONVERSION_TF) <= now_srv]
        if not len(r):
            return None
        t_end = self.scheme.to_utc(r["time"].astype("int64") + seconds(CONVERSION_TF)).as_unit("ns").asi8
        return c["symbol"], (t_end, r["close"].astype(float), c["base"], c["quote"])

    # ------------------------------------------------------------------ Durchlauf
    def cycle(self) -> dict:
        now = self.now_utc_fn()
        now_srv = int(self.scheme.to_server(pd.DatetimeIndex([now]))[0])
        self.cycles += 1
        markets = self.state.setdefault("market", {})
        ticks, status_m = {}, {}
        for sym in self.symbols:
            t = self.client.tick(sym)
            ticks[sym] = t
            if not t or not t.get("time"):
                zustand, age = "kein Tick", None
            else:
                age = now_srv - int(t["time"])
                if age > self.pc.stale_after_s:
                    zustand = "keine aktuellen Kurse"
                elif age < -CLOCK_TOLERANCE_S:
                    zustand = "Uhrzeit weicht ab"
                else:
                    zustand = "aktuell"
            if markets.get(sym) != zustand:
                kind = "kurse_aktuell" if zustand == "aktuell" else "keine_aktuellen_kurse"
                self.store.event(kind, now, symbol=sym, zustand=zustand, tick_alter_s=age,
                                 hinweis=None if zustand == "aktuell" else
                                 "Markt geschlossen, keine Verbindung oder PC-Uhr falsch – es wird nichts ausgeführt")
                markets[sym] = zustand
            status_m[sym] = {"zustand": zustand, "tick_alter_s": age,
                             "tick_utc": None if not t else str(self.scheme.to_utc([int(t["time"])])[0]),
                             "bid": None if not t else t.get("bid"), "ask": None if not t else t.get("ask")}
        if all(v["zustand"] == "kein Tick" for v in status_m.values()):
            self.store.event("verbindung", now, meldung="keine Ticks – Verbindung zum Terminal wird neu aufgebaut")
            try:
                self.client.connect()
            except Exception as e:
                self.store.event("fehler", now, bereich="verbindung", meldung=f"{type(e).__name__}: {e}")

        cache = {}

        def eur():
            """Umrechnungskurse nur laden, wenn ein Kandidat neu bewertet wird (höchstens einmal je Durchlauf)."""
            if "eur" not in cache:
                cache["eur"] = None
                try:
                    cache["eur"] = self._eur_rates(now_srv)
                except Exception as e:
                    self.store.event("fehler", now, bereich="umrechnungskurse", meldung=f"{type(e).__name__}: {e}")
            return cache["eur"]

        for c in self.pc.candidates:
            t = ticks.get(c.symbol)
            try:
                self._process(c, now, now_srv, None if not t else int(t["time"]),
                              status_m[c.symbol]["zustand"] == "aktuell", t, eur)
            except Exception as e:  # ein Kandidat darf die anderen nicht aufhalten
                self.store.event("fehler", now, kandidat=c.key, meldung=f"{type(e).__name__}: {e}")
                self.log(f"  FEHLER {c.key}: {type(e).__name__}: {e}")
            self.store.save_state(self.state)          # nach jedem Kandidaten: Abbruch erzeugt keine Doppelungen
        doc = {"updated_utc": now.isoformat(), "hinweis": "Hypothetische Beobachtung – es werden keine Orders gesendet",
               "risikoregeln": asdict(self.rules), "kostenszenario": {s: c.to_dict() for s, c in self.costs.items()},
               "kandidaten": self.snap}
        self.store.write_trades(doc)
        status = {"updated_utc": now.isoformat(), "durchlauf": self.cycles, "konto": self.account,
                  "zeitschema": self.scheme.name, "maerkte": status_m, "arbeitsspeicher": resources.snapshot(),
                  "kandidaten": {k: {"beobachtung_seit_utc": v["beobachtung_seit_utc"], "letzte_kerze_utc": v["letzte_kerze_utc"],
                                     "signale": len(v["signale"]), "offen": sum(1 for x in v["trades"] if x["status"] == "offen"),
                                     "geschlossen": sum(1 for x in v["trades"] if x["status"] == "geschlossen")}
                                 for k, v in self.snap.items()}}
        self.store.write_status(status)
        return status

    def stop_requested(self) -> bool:
        """STOP.bat legt data/paper/stop.anfrage an -> Beobachtung endet sauber nach dem laufenden Durchlauf."""
        f = self.store.dir / STOP_FILE
        if f.exists():
            f.unlink(missing_ok=True)
            return True
        return False

    def run(self, interval_s: int | None = None, max_cycles: int | None = None, sleep=time.sleep) -> None:
        interval_s = interval_s or self.pc.interval_s
        n = 0
        while True:
            if self.stop_requested():
                self.log("Stopp angefordert – Beobachtung wird beendet.")
                return
            t0 = time.time()
            try:
                st = self.cycle()
                parts = [f"{k.split('_', 1)[1]}: {v['offen']} offen/{v['geschlossen']} geschl."
                         for k, v in st["kandidaten"].items()]
                self.log(f"{st['updated_utc'][:19]} UTC  Durchlauf {st['durchlauf']}  "
                         + "  ".join(f"{s} {m['zustand']}" for s, m in st["maerkte"].items()) + "  |  " + "; ".join(parts))
            except Exception as e:  # unbeaufsichtigter Betrieb: ein Fehler beendet die Beobachtung nicht
                self.log(f"FEHLER im Durchlauf: {type(e).__name__}: {e} – nächster Versuch beim nächsten Durchlauf")
                try:
                    self.store.event("fehler", self.now_utc_fn(), bereich="durchlauf", meldung=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
            n += 1
            if max_cycles and n >= max_cycles:
                return
            # in Sekundenschritten warten, damit eine Stopp-Anforderung schnell wirkt
            for _ in range(int(max(1.0, interval_s - (time.time() - t0)))):
                if (self.store.dir / STOP_FILE).exists():
                    break
                sleep(1)

    # ------------------------------------------------------------------ Kandidat
    def _process(self, cand: PaperCandidate, now, now_srv, tick_srv, fresh, tick, eur) -> None:
        key, sym, tf = cand.key, cand.symbol, cand.timeframe
        tf_s = seconds(tf)
        cands = self.state.setdefault("candidates", {})
        st = cands.get(key)
        if st is None:
            start_srv = -(-now_srv // tf_s) * tf_s          # erste Kerze, die nach dem Start beginnt
            st = cands[key] = {"symbol": sym, "timeframe": tf, "strategy": cand.strategy, "params": cand.params,
                               "start_server": int(start_srv), "start_utc": str(self.scheme.to_utc([start_srv])[0]),
                               "last_bar_server": None, "accepted": {}, "rejected": {}, "closed": {}, "opened": [],
                               "gaps": []}
            self.store.event("beobachtung_start", now, kandidat=key, params=cand.params, beginn_utc=st["start_utc"],
                             hinweis="ausgewertet werden nur Signale auf Kerzen ab diesem Zeitpunkt")
        if st["params"] != cand.params:
            raise RuntimeError(f"Parameter in config.toml ({cand.params}) weichen von der laufenden Beobachtung "
                               f"({st['params']}) ab – Beobachtung nicht vermischen")
        rates = self._fetch(sym, tf, st["start_server"] - self.pc.warmup_bars * tf_s, now_srv)
        if rates is None:
            self._ensure_snap(cand, st)
            return
        rates = self._complete(rates, tf_s, now_srv, tick_srv, fresh)
        if not len(rates):
            self._ensure_snap(cand, st)
            return
        last_srv = int(rates["time"][-1])
        prev = st["last_bar_server"]
        if prev is not None and last_srv <= prev and key in self.snap:
            return                                          # keine neue abgeschlossene Kerze
        bars = rates_to_frame(rates, self.scheme)[BAR_COLS]
        del rates
        self._evaluate(cand, st, bars, now, tf_s, prev, tick, fresh, eur)
        st["last_bar_server"] = last_srv

    def _ensure_snap(self, cand, st):
        if cand.key not in self.snap:
            self.snap[cand.key] = self._empty_snap(cand, st)

    def _empty_snap(self, cand, st):
        return {"symbol": cand.symbol, "timeframe": cand.timeframe, "strategy": cand.strategy, "params": cand.params,
                "beobachtung_seit_utc": st["start_utc"], "letzte_kerze_utc": None, "signale": [], "trades": [],
                "abgelehnte_signale": list(st["rejected"].values()), "luecken": len(st["gaps"]),
                "mindestlot": None, "referenzkonto": None}

    def _evaluate(self, cand, st, bars, now, tf_s, prev, tick, fresh, eur) -> None:
        key, sym = cand.key, cand.symbol
        spec, costs = self.specs[sym], self.costs[sym]
        srv = bars["time_server"].to_numpy(dtype="int64")
        t = pd.DatetimeIndex(bars["time"])
        n = len(bars)
        start_srv = st["start_server"]
        tf_td = pd.Timedelta(seconds=tf_s)

        # Datenlücken im Beobachtungszeitraum (keine Kurse erfinden, nur protokollieren)
        d = np.diff(srv)
        for i in np.nonzero(d > tf_s)[0]:
            if srv[i + 1] < start_srv or str(int(srv[i])) in st["gaps"]:
                continue
            st["gaps"].append(str(int(srv[i])))
            self.store.event("datenluecke", now, kandidat=key, von_utc=str(t[i] + tf_td), bis_utc=str(t[i + 1]),
                             fehlende_kerzen=int(d[i] // tf_s - 1),
                             hinweis="keine Ausführung in der Lücke; Einstiege über die Lücke entfallen")

        with features.shared(bars):
            sig = create(cand.strategy, **cand.params).entries(bars)
        skeys = [f"{int(srv[int(i)])}|{int(dd)}" for i, dd in zip(sig.idx, sig.direction)]
        for k, sk in enumerate(skeys):
            i = int(sig.idx[k])
            if srv[i] < start_srv or sk in st["accepted"] or sk in st["rejected"]:
                continue
            lag = (now - (t[i] + tf_td)).total_seconds()
            info = {"key": sk, "signal_kerze_utc": str(t[i]), "kerzenschluss_utc": str(t[i] + tf_td),
                    "richtung": "long" if sig.direction[k] == 1 else "short", "stop": float(sig.stop[k]),
                    "ziel": _f(sig.target[k], spec.digits), "schlusskurs": float(bars["close"].iat[i]),
                    "erkannt_utc": now.isoformat(), "verzoegerung_s": round(lag)}
            if tick and fresh:
                info["kurs_bei_erkennung"] = {"bid": tick.get("bid"), "ask": tick.get("ask")}
            if prev is not None and srv[i] <= prev:
                info["grund"] = "nachträglich aufgetaucht – die Kerze war bereits ausgewertet (Kursdaten geändert?)"
                st["rejected"][sk] = info
                self.store.event("abweichung", now, kandidat=key, **info)
            elif lag > self.pc.max_delay_s:
                info["grund"] = (f"verspätet erkannt ({lag / 60:.0f} min nach Kerzenschluss, Programm lief nicht) – "
                                 f"nicht gehandelt")
                st["rejected"][sk] = info
                self.store.event("signal_verspaetet", now, kandidat=key, **info)
            else:
                st["accepted"][sk] = info
                self.store.event("signal", now, kandidat=key, **info)
        present = set(skeys)
        for sk, info in st["accepted"].items():
            if sk not in present and not info.get("verschwunden"):
                info["verschwunden"] = now.isoformat()
                self.store.event("abweichung", now, kandidat=key, signal=sk,
                                 grund="angenommenes Signal bei Neuberechnung nicht mehr vorhanden (Kursdaten geändert?)")

        keep = np.array([sk in st["accepted"] for sk in skeys], dtype=bool)
        sub = Signals(sig.idx[keep], sig.direction[keep], sig.stop[keep], sig.target[keep], sig.max_bars,
                      sig.exit_long, sig.exit_short)
        sp = costs.spread_price(bars["spread"].to_numpy(), spec.point)
        raw, _ = simulate(bars, sub, tf_s, sp, costs.slippage_frac, self.rules.max_positions)
        o, c = bars["open"].to_numpy(dtype=float), bars["close"].to_numpy(dtype=float)

        traded = set()
        current = {}
        for r in raw.itertuples(index=False):
            sk = f"{int(srv[r.signal_i])}|{int(r.direction)}"
            traded.add(sk)
            tid = f"{key}|{sk}"
            rec = {"id": tid, "signal": sk, "signal_kerze_utc": str(t[r.signal_i]), "direction": int(r.direction),
                   "richtung": "long" if r.direction == 1 else "short",
                   "einstieg_utc": str(r.entry_time), "einstieg": float(r.entry), "stop": float(r.stop),
                   "ziel": _f(r.target, spec.digits), "risiko_preis": float(r.risk),
                   "eroeffnung_einstiegskerze": float(o[r.entry_i]), "spread_einstieg": float(sp[r.entry_i]),
                   "entry_server": int(r.entry_server)}
            if r.exit_reason == "end":                     # Datenende = Position noch offen
                rec.update(status="offen", bewertung_kurs=float(c[-1] if r.direction == 1 else c[-1] + sp[-1]),
                           bewertung_utc=str(t[-1] + tf_td))
            else:
                lag = (now - (t[r.exit_i] + tf_td)).total_seconds()
                rec.update(status="geschlossen", ausstieg_utc=str(r.exit_time), ausstieg=float(r.exit),
                           ausstiegsgrund=r.exit_reason, exit_server=int(r.exit_server),
                           eroeffnung_ausstiegskerze=float(o[r.exit_i]), spread_ausstieg=float(sp[r.exit_i]),
                           ausstieg_erkannt_utc=now.isoformat(), ausstieg_verspaetet_erkannt=bool(lag > self.pc.max_delay_s))
            current[tid] = rec

        for tid, rec in current.items():
            if rec["status"] == "geschlossen":
                if tid in st["closed"]:
                    f = st["closed"][tid]
                    same = (abs(f["einstieg"] - rec["einstieg"]) < 1e-9 and abs(f["ausstieg"] - rec["ausstieg"]) < 1e-9
                            and f["ausstieg_utc"] == rec["ausstieg_utc"])
                    if not same and not f.get("abweichung_gemeldet"):
                        f["abweichung_gemeldet"] = now.isoformat()
                        self.store.event("abweichung", now, kandidat=key, trade=tid,
                                         grund="Neuberechnung eines abgeschlossenen Trades weicht ab – eingefrorener Wert bleibt")
                    continue
                if tid not in st["opened"]:
                    st["opened"].append(tid)
                    self.store.event("einstieg", now, kandidat=key, trade=tid, zeit_utc=rec["einstieg_utc"],
                                     richtung=rec["richtung"], kurs=rec["einstieg"], stop=rec["stop"], ziel=rec["ziel"])
                st["closed"][tid] = rec
                self.store.event("ausstieg", now, kandidat=key, trade=tid, zeit_utc=rec["ausstieg_utc"],
                                 kurs=rec["ausstieg"], grund=rec["ausstiegsgrund"],
                                 verspaetet_erkannt=rec["ausstieg_verspaetet_erkannt"])
            elif tid not in st["opened"]:
                st["opened"].append(tid)
                self.store.event("einstieg", now, kandidat=key, trade=tid, zeit_utc=rec["einstieg_utc"],
                                 richtung=rec["richtung"], kurs=rec["einstieg"], stop=rec["stop"], ziel=rec["ziel"])

        contiguous = np.zeros(n, dtype=bool)
        contiguous[:-1] = np.diff(srv) == tf_s
        idx_of = {sk: int(sig.idx[k]) for k, sk in enumerate(skeys)}
        signals = []
        for sk, info in st["accepted"].items():
            row = dict(info)
            if sk in traded or any(x.get("signal") == sk for x in st["closed"].values()):
                row["status"] = "gehandelt"
            elif info.get("verschwunden"):
                row["status"] = "nicht mehr vorhanden (Abweichung)"
            elif idx_of.get(sk) == n - 1:
                row["status"] = "Einstieg zur nächsten Eröffnung ausstehend"
            elif sk in idx_of and not contiguous[idx_of[sk]]:
                row["status"] = "kein Einstieg: Datenlücke nach der Signalkerze"
            else:
                row["status"] = "kein Einstieg: bereits 2 Positionen offen oder Stop ungültig"
            signals.append(row)

        # Bewertung: eingefrorene abgeschlossene Trades + aktuell offene
        conv = self._converter(sym, bars, tf_s, eur())
        always_open = get_calendar(self.cfg.calendar_for(sym)).always_open
        closed = sorted(st["closed"].values(), key=lambda x: x["einstieg_utc"])
        open_ = [r for r in current.values() if r["status"] == "offen"]
        try:
            acc_view = self._account(closed, spec, costs, conv, always_open)
            valued = [{**rec, **self._value(rec, spec, costs, conv, always_open)} for rec in closed + open_]
        except KeyError as e:               # kein Umrechnungskurs: €-Werte nicht verfügbar, nichts schätzen
            if not st.get("umrechnung_fehlt"):
                st["umrechnung_fehlt"] = now.isoformat()
                self.store.event("fehler", now, kandidat=key, bereich="umrechnung",
                                 meldung=f"{e} – Beträge in € sind nicht verfügbar, Kurse und Signale werden weiter protokolliert")
            acc_view = {"executed": {}, "metrics": None}
            valued = [{**rec, "euro_nicht_verfuegbar": True} for rec in closed + open_]
        else:
            st.pop("umrechnung_fehlt", None)
        trades = []
        for v in valued:
            rec = v
            ex = acc_view["executed"].get((rec["einstieg_utc"], rec["direction"]))
            if rec["status"] == "geschlossen" and not v.get("euro_nicht_verfuegbar"):
                if ex:
                    v["referenzkonto"] = {"ausgefuehrt": True, "volumen": ex[0], "netto_eur": ex[1]}
                else:
                    v["referenzkonto"] = {"ausgefuehrt": False,
                                          "grund": f"Mindestlot: 0,01 Lot überschreitet das Risiko von {100 * self.rules.risk_per_trade:g} %".replace(".", ",")
                                          if not v["innerhalb_risikogrenze"] else
                                          "Risikoregel (Tagesverlust, Positionen oder Margin)"}
            trades.append(v)
        self.snap[key] = {
            **self._empty_snap(cand, st), "letzte_kerze_utc": str(t[-1]), "signale": signals, "trades": trades,
            "mindestlot": self._summary_min_lot([x for x in trades if x["status"] == "geschlossen"
                                                 and not x.get("euro_nicht_verfuegbar")]),
            "referenzkonto": acc_view["metrics"],
            "kostenszenario": costs.name,
        }

    # ------------------------------------------------------------------ Bewertung in €
    def _value(self, rec, spec, costs, conv, always_open) -> dict:
        ccy = self.account["currency"]
        vol, cs, d = spec.volume_min, spec.contract_size, rec["direction"]
        r_e = float(conv.rate(spec.currency_profit, ccy, [pd.Timestamp(rec["einstieg_utc"])])[0])
        entry = rec["einstieg"]
        comm_lot = (costs.commission_per_lot_rt + costs.commission_frac_rt * entry * cs) * r_e
        risk = (rec["risiko_preis"] * cs * r_e + comm_lot) * vol
        limit = self.rules.initial * self.rules.risk_per_trade
        out = {"volumen_mindestlot": vol, "risiko_eur": _f(risk), "risiko_pct_referenz": _f(100 * risk / self.rules.initial, 3),
               "innerhalb_risikogrenze": bool(np.isfinite(risk) and risk <= limit + 1e-9),
               "umrechnung_einstieg": _f(r_e, 6)}
        slip_e = costs.slippage_frac * rec["eroeffnung_einstiegskerze"]
        if rec["status"] == "offen":
            r_n = float(conv.rate(spec.currency_profit, ccy, [pd.Timestamp(rec["bewertung_utc"])])[0])
            r_n = r_n if np.isfinite(r_n) else r_e
            out["unrealisiert_brutto_eur"] = _f(d * (rec["bewertung_kurs"] - entry) * cs * vol * r_n)
            out["spread_eur"] = _f((rec["spread_einstieg"] if d == 1 else 0.0) * cs * vol * r_e)
            out["slippage_eur"] = _f(slip_e * cs * vol * r_e)
            return out
        x_t = pd.Timestamp(rec["ausstieg_utc"])
        r_x = float(conv.rate(spec.currency_profit, ccy, [x_t])[0])
        r_x = r_x if np.isfinite(r_x) else r_e
        exit_, reason = rec["ausstieg"], rec["ausstiegsgrund"]
        ox = rec["eroeffnung_ausstiegskerze"]
        if reason == "stop":
            slip_x = costs.slippage_frac * rec["stop"]
        elif reason == "stop_gap":
            slip_x = costs.slippage_frac * (ox if d == 1 else ox + rec["spread_ausstieg"])
        elif reason == "target":
            slip_x = 0.0
        else:
            slip_x = costs.slippage_frac * ox
        gross = d * (exit_ - entry) * cs * vol * r_x
        comm = (costs.commission_per_lot_rt * vol + costs.commission_frac_rt * cs * vol * (entry + exit_) / 2) * r_x
        nights = swap_nights(int(rec["entry_server"]), int(rec["exit_server"]), always_open, spec.swap_rollover3days)
        amt, sccy = swap_amount(spec, d, vol, entry, exit_, nights)
        sw = amt if sccy == "DEPOSIT" or not amt else float(amt * conv.rate(sccy, ccy, [x_t])[0])
        net = gross - comm + sw
        out.update({
            "brutto_eur": _f(gross),
            "spread_eur": _f((rec["spread_einstieg"] if d == 1 else rec["spread_ausstieg"]) * cs * vol * r_x),
            "slippage_eur": _f((slip_e + slip_x) * cs * vol * r_x),
            "kommission_eur": _f(comm), "swap_eur": _f(sw), "swap_naechte": nights, "netto_eur": _f(net),
            "r_netto": _f(net / risk, 3) if risk > 0 else None,
            "haltedauer_h": _f((x_t - pd.Timestamp(rec["einstieg_utc"])).total_seconds() / 3600),
        })
        return out

    @staticmethod
    def _summary_min_lot(closed: list[dict]) -> dict | None:
        if not closed:
            return {"trades": 0}
        net = np.array([x["netto_eur"] for x in closed if x.get("netto_eur") is not None], dtype=float)
        if not len(net):
            return {"trades": len(closed)}
        cum = np.concatenate([[0.0], np.cumsum(net)])
        dd = float(np.max(np.maximum.accumulate(cum) - cum))
        gp, gl = net[net > 0].sum(), -net[net < 0].sum()
        r = [x["r_netto"] for x in closed if x.get("r_netto") is not None]
        return {"trades": int(len(net)), "gewinner": int((net > 0).sum()), "netto_eur": _f(net.sum()),
                "profitfaktor": _f(gp / gl, 3) if gl > 0 else None,
                "durchschnitt_r": _f(float(np.mean(r)), 3) if r else None,
                "max_drawdown_eur": _f(dd), "max_drawdown_pct_referenz": _f(100 * dd / (1000.0 + float(np.max(cum)))),
                "kosten_eur": {k: _f(sum(x.get(f"{k}_eur") or 0 for x in closed))
                               for k in ("spread", "slippage", "kommission", "swap")}}

    def _account(self, closed, spec, costs, conv, always_open) -> dict:
        if not closed:
            return {"executed": {}, "metrics": None}
        raw = pd.DataFrame({
            "entry_time": pd.to_datetime([x["einstieg_utc"] for x in closed], utc=True),
            "exit_time": pd.to_datetime([x["ausstieg_utc"] for x in closed], utc=True),
            "entry_server": [x["entry_server"] for x in closed], "exit_server": [x["exit_server"] for x in closed],
            "direction": [x["direction"] for x in closed], "entry": [x["einstieg"] for x in closed],
            "exit": [x["ausstieg"] for x in closed], "risk": [x["risiko_preis"] for x in closed],
            "spread_entry": [x["spread_einstieg"] for x in closed], "exit_reason": [x["ausstiegsgrund"] for x in closed]})
        res = run_account(raw, spec, conv, self.account["currency"], self.account["leverage"], costs, self.rules,
                          always_open, spec.swap_rollover3days)
        executed = {(str(r.entry_time), int(r.direction)): (float(r.volume), round(float(r.net_eur), 2))
                    for r in res.trades.itertuples(index=False)}
        m = compute(res, self.rules.initial)
        keep = ("trades", "net_profit_eur", "return_pct", "max_drawdown_pct", "max_drawdown_eur", "profit_factor",
                "win_rate_pct", "expectancy_eur", "expectancy_r", "skipped", "min_lot_skip_pct", "costs_eur")
        return {"executed": executed, "metrics": {k: m.get(k) for k in keep}}
