"""Trades je Kandidat auf echten MT5-Kerzen – mit dem Simulator und den Kosten der Zeitmaschine (NUR RECHNEN).

Jeder Kandidat wird EINMAL über die ganze Historie gerechnet (Signale nur aus abgeschlossenen Kerzen, Einstieg zur
nächsten Eröffnung, Ask = Bid + Spread, Stop vor Ziel in derselben Kerze, Lücken zum Eröffnungskurs, Kommission und
Swap). Die Auswahl (auswahl.py) schneidet daraus nur Zeitfenster – dadurch bleibt die Rechnung schnell, und jede
Regel wird genau so gerechnet, wie sie live laufen würde.

Ergebnis je Kandidat: kompakte Arrays (Einstieg, Ausstieg, R, USD) – siehe Trades.
"""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import features
from ..backtest.account import _mt5_weekday, swap_amount
from ..backtest.costs import CostScenario
from ..backtest.engine import simulate
from ..instruments import InstrumentSpec
from ..strategies import base as strat_base
from ..strategies import library as _library  # noqa: F401 – meldet alle Strategien an
from ..strategies.zusatz import SITZUNGEN, entry_hours, filter_signals
from ..timeframes import seconds
from .kandidaten import GOLDBOT, GRIDS, RICHTUNGEN, Kandidat, freeze

GOLDBOT_DF = "__goldbot_trades__"
MIN_TRADES_SPEICHERN = 8        # Kandidaten mit weniger Trades über die ganze Historie werden nicht aufbewahrt


@dataclass
class Trades:
    """Trades eines Kandidaten, nach Einstieg sortiert. Zeiten in ns seit 1970 (UTC)."""
    entry: np.ndarray
    exit: np.ndarray
    r: np.ndarray
    pnl: np.ndarray

    def __len__(self) -> int:
        return len(self.entry)

    @classmethod
    def leer(cls) -> "Trades":
        z = np.empty(0, dtype=np.int64)
        f = np.empty(0, dtype=np.float64)
        return cls(z, z.copy(), f, f.copy())

    def nimm(self, m) -> "Trades":
        return Trades(self.entry[m], self.exit[m], self.r[m], self.pnl[m])

    @classmethod
    def verbinden(cls, teile: list["Trades"]) -> "Trades":
        if not teile:
            return cls.leer()
        return cls(*(np.concatenate([getattr(t, k) for t in teile]) for k in ("entry", "exit", "r", "pnl")))


# ============================================================================ Kosten in USD
def _swap_je_nacht(spec: InstrumentSpec, d: int, lot: float, px: float) -> float:
    return swap_amount(spec, d, lot, px, px, 1.0)[0]


def _naechte(entry_server: np.ndarray, exit_server: np.ndarray, always_open: bool, rollover3: int) -> np.ndarray:
    """Gewichtete Übernachtungen je Trade wie account.swap_nights, aber für alle Trades auf einmal."""
    if len(entry_server) == 0:
        return np.empty(0)
    d0, d1 = entry_server // 86400, exit_server // 86400
    lo, hi = int(d0.min()), int(d1.max()) + 1
    days = np.arange(lo, hi)
    if always_open:
        w = np.ones(len(days))
    else:
        wd = _mt5_weekday(days)
        w = np.where((wd >= 1) & (wd <= 5), 1.0, 0.0)
        w[wd == rollover3] = 3.0
    cum = np.concatenate([[0.0], np.cumsum(w)])
    return np.where(d1 > d0, cum[d1 - lo] - cum[d0 - lo], 0.0)


def in_usd(raw: pd.DataFrame, spec: InstrumentSpec, cost: CostScenario, lot: float,
           always_open: bool = False) -> pd.DataFrame:
    """Rohtrades des Simulators (Kurse) -> USD mit Kommission und Swap für die feste Lotgröße."""
    cs = spec.contract_size
    out = pd.DataFrame({"entry_time": raw["entry_time"], "exit_time": raw["exit_time"],
                        "direction": raw["direction"].astype(int), "entry": raw["entry"], "exit": raw["exit"],
                        "stop": raw["stop"], "risk": raw["risk"], "exit_reason": raw["exit_reason"]})
    out["volume"] = lot
    out["gross"] = raw["pnl_price"].to_numpy(float) * cs * lot
    out["commission"] = -(cost.commission_per_lot_rt * lot + cost.commission_frac_rt * raw["entry"].to_numpy(float)
                          * cs * lot)
    if cost.swap and len(raw):
        n = _naechte(raw["entry_server"].to_numpy("int64"), raw["exit_server"].to_numpy("int64"), always_open,
                     spec.swap_rollover3days)
        d = out["direction"].to_numpy()
        px = raw["entry"].to_numpy(float)
        if spec.swap_mode in (5, 6):
            per = np.array([_swap_je_nacht(spec, int(a), lot, float(p)) for a, p in zip(d, px)])
        else:
            per = np.where(d == 1, _swap_je_nacht(spec, 1, lot, 1.0), _swap_je_nacht(spec, -1, lot, 1.0))
        out["swap"] = per * n
    else:
        out["swap"] = 0.0
    out["pnl"] = out["gross"] + out["commission"] + out["swap"]
    out["risk_usd"] = out["risk"] * cs * lot
    out["r"] = out["pnl"] / out["risk_usd"]
    return out


def kompakt(df: pd.DataFrame) -> Trades:
    if df is None or not len(df):
        return Trades.leer()
    df = df.sort_values("entry_time")
    return Trades(pd.DatetimeIndex(df["entry_time"]).as_unit("ns").asi8.copy(),
                  pd.DatetimeIndex(df["exit_time"]).as_unit("ns").asi8.copy(),
                  df["r"].to_numpy(np.float64).copy(), df["pnl"].to_numpy(np.float64).copy())


# ============================================================================ Kandidaten rechnen
def signale(strategie: str, params: dict, bars: pd.DataFrame):
    return strat_base.create(strategie, **params).entries(bars)


def trades_regel(k: Kandidat, bars: pd.DataFrame, spec: InstrumentSpec, cost: CostScenario, lot: float = 0.01,
                 sig=None, hours=None) -> pd.DataFrame:
    """Alle Trades eines Kandidaten (volle Spalten, USD) – Signale werden vorher nach Uhrzeit/Richtung gefiltert,
    damit ein ausgelassenes Signal keine Position blockiert (wie finder_gefiltert im Demo-Bot)."""
    if sig is None:
        with features.shared(bars):
            sig = signale(k.strategie, k.p, bars)
    von, bis = SITZUNGEN[k.sitzung]
    sig = filter_signals(sig, bars, von, bis, k.richtung, hours=hours)
    if len(sig) == 0:
        return in_usd(_leer_roh(), spec, cost, lot)
    sp = cost.spread_price(bars["spread"].to_numpy(float), spec.point)
    raw, _ = simulate(bars, sig, seconds(k.tf), sp, cost.slippage_frac, max_positions=1)
    return in_usd(raw, spec, cost, lot)


def _leer_roh() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=float) for c in
                         ("entry", "exit", "stop", "risk", "pnl_price", "entry_server", "exit_server", "direction")}
                        | {"entry_time": pd.Series(dtype="datetime64[ns, UTC]"),
                           "exit_time": pd.Series(dtype="datetime64[ns, UTC]"),
                           "exit_reason": pd.Series(dtype=str)})


def goldbot_regel(k: Kandidat, gold_trades: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Gold-Bot-Trades nach Uhrzeit/Richtung gefiltert (Gold-Bot wird einmal Kerze für Kerze gerechnet)."""
    if gold_trades is None or not len(gold_trades):
        return gold_trades
    von, bis = SITZUNGEN[k.sitzung]
    # Einstiegsstunde: Gold-Bot-Trades beginnen zur Eröffnung der Einstiegskerze
    hh = pd.DatetimeIndex(gold_trades["entry_time"]).hour.to_numpy()
    keep = (hh >= von) & (hh < bis)
    if k.richtung:
        keep &= gold_trades["direction"].to_numpy() == k.richtung
    return gold_trades[keep].reset_index(drop=True)


def goldbot_alle(bars: pd.DataFrame, spec: InstrumentSpec, cost: CostScenario, gcfg, konto: float) -> pd.DataFrame:
    from ..zeitmaschine.handel import goldbot_trades
    max_risk = konto * gcfg.max_risk_pct / 100 if gcfg.max_risk_pct else None
    df = goldbot_trades(bars, spec, cost, gcfg, start=min(gcfg.bars, len(bars) - 1), lot=gcfg.lot,
                        max_risk_usd=max_risk)
    return df


# ============================================================================ Parallel über (Zeitrahmen, Strategie)
_W: dict = {}


def _init(ctx: dict):
    _W.clear()
    _W.update(ctx)


def _bars(tf: str) -> pd.DataFrame:
    return _W["bars"][tf]


def _job(tf: str, strategie: str, kands: list[Kandidat]) -> tuple[str, str, dict, str | None, float]:
    t0 = time.time()
    spec, cost, lot = _W["spec"], _W["cost"], _W["lot"]
    out: dict[str, Trades] = {}
    try:
        bars = _bars(tf)
        if strategie == GOLDBOT:
            gt = goldbot_alle(bars, spec, cost, _W["gcfg"], _W["konto"])
            out[GOLDBOT_DF] = gt                                     # für Sieger-Auswertung aufbewahren
            for k in kands:
                tr = kompakt(goldbot_regel(k, gt, bars))
                if len(tr) >= MIN_TRADES_SPEICHERN:
                    out[k.key] = tr
            return tf, strategie, out, None, time.time() - t0
        hours = entry_hours(bars)
        by_params: dict[tuple, list[Kandidat]] = {}
        for k in kands:
            by_params.setdefault(k.params, []).append(k)
        with features.shared(bars):
            for params, ks in by_params.items():
                sig = signale(strategie, dict(params), bars)
                for k in ks:
                    tr = kompakt(trades_regel(k, bars, spec, cost, lot, sig=sig, hours=hours))
                    if len(tr) >= MIN_TRADES_SPEICHERN:
                        out[k.key] = tr
    except Exception as e:                                           # noqa: BLE001 – andere Jobs laufen weiter
        return tf, strategie, out, f"{type(e).__name__}: {e}", time.time() - t0
    return tf, strategie, out, None, time.time() - t0


def rechne_alle(kandidaten: list[Kandidat], bars: dict, spec: InstrumentSpec, cost: CostScenario, lot: float = 0.01,
                gcfg=None, konto: float = 357.0, kerne: int | None = None, log=print) -> tuple[dict, dict, list]:
    """Alle Kandidaten rechnen (bars: Zeitrahmen -> Kerzen).
    Rückgabe: (key -> Trades, key -> Kandidat, Fehler, Gold-Bot-Trades oder None)."""
    jobs: dict[tuple[str, str], list[Kandidat]] = {}
    for k in kandidaten:
        jobs.setdefault((k.tf, k.strategie), []).append(k)
    # Gold-Bot zuerst (dauert am längsten), danach große Zeitrahmen-Jobs
    order = sorted(jobs, key=lambda j: (j[1] != GOLDBOT, ["M5", "M15", "M30", "H1", "H4"].index(j[0])))
    ctx = {"bars": bars, "spec": spec, "cost": cost, "lot": lot, "gcfg": gcfg, "konto": konto}
    kerne = kerne or max(1, min(8, (os.cpu_count() or 2) - 1))
    by_key = {k.key: k for k in kandidaten}
    res: dict[str, Trades] = {}
    errors = []
    t0 = time.time()
    done = 0
    gold_df = None

    def take(r):
        nonlocal done
        nonlocal gold_df
        tf, st, out, err, dt = r
        done += 1
        if GOLDBOT_DF in out:
            gold_df = out.pop(GOLDBOT_DF)
        res.update(out)
        name = "Gold-Bot" if st == GOLDBOT else GRIDS[st][2]
        msg = f"  [{done:>3}/{len(order)}] {tf:<3} {name:<42} {len(jobs[(tf, st)]):>4} Varianten  {dt:5.0f} s"
        if err:
            errors.append(f"{tf} {st}: {err}")
            msg += f"  FEHLER: {err}"
        log(msg)

    if kerne <= 1:
        _init(ctx)
        for j in order:
            take(_job(j[0], j[1], jobs[j]))
    else:
        with ProcessPoolExecutor(max_workers=kerne, initializer=_init, initargs=(ctx,)) as ex:
            futs = [ex.submit(_job, j[0], j[1], jobs[j]) for j in order]
            for f in as_completed(futs):
                take(f.result())
    log(f"  fertig: {len(res):,} Varianten mit Trades in {time.time() - t0:.0f} s".replace(",", "."))
    return res, by_key, errors, gold_df


def kaufen_und_halten(bars: pd.DataFrame, spec: InstrumentSpec, a_ns: int, b_ns: int, lot: float = 0.01) -> dict:
    """Vergleich: Gold zum ersten Kurs ab a kaufen, bis b halten (mit Swap, ohne Hebel-Effekte)."""
    t = pd.DatetimeIndex(bars["time"]).as_unit("ns").asi8
    m = (t >= a_ns) & (t < b_ns)
    if m.sum() < 2:
        return {}
    i0, i1 = int(np.argmax(m)), int(len(m) - 1 - np.argmax(m[::-1]))
    p0, p1 = float(bars["open"].iloc[i0]), float(bars["close"].iloc[i1])
    ts = bars["time_server"].to_numpy("int64")
    nights = _naechte(np.array([ts[i0]]), np.array([ts[i1]]), False, spec.swap_rollover3days)[0]
    swap = _swap_je_nacht(spec, 1, lot, p0) * nights
    gross = (p1 - p0) * spec.contract_size * lot
    return {"von": str(pd.Timestamp(t[i0], tz="UTC").date()), "bis": str(pd.Timestamp(t[i1], tz="UTC").date()),
            "kurs_von": round(p0, 2), "kurs_bis": round(p1, 2), "usd": round(gross + swap, 2),
            "swap_usd": round(swap, 2)}


def tstat(r: np.ndarray) -> float:
    n = len(r)
    if n < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r)) / sd * math.sqrt(n) if sd > 0 else 0.0


__all__ = ["Trades", "in_usd", "kompakt", "trades_regel", "goldbot_regel", "goldbot_alle", "rechne_alle",
           "kaufen_und_halten", "tstat", "RICHTUNGEN", "freeze"]
