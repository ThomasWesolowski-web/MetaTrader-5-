"""Ablauf Phase 3: alle Strategien × Märkte × Zeitrahmen testen, bewerten und eine Rangliste erstellen.

Ausgelegt auf 8 GB RAM: standardmäßig 1 Prozess, Märkte und Zeitrahmen nacheinander; Signale werden
blockweise (Standard 100 000 Kerzen + 10 000 Vorlauf) berechnet, bei Speichermangel mit kleinerer
Blockgröße wiederholt; Indikatoren werden je Block einmal berechnet (features.shared). Nach jeder Testeinheit
(Markt × Zeitrahmen × Strategie) wird das Ergebnis gespeichert; ein abgebrochener Lauf setzt dort fort.
Die Parameterwahl nutzt ausschließlich Trainingsdaten (Walk-Forward und Gesamttraining).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict

import numpy as np
import pandas as pd

from .. import features
from .. import indicators as ind
from ..config import Config
from ..instruments import CurrencyConverter, InstrumentSpec, conversion_sources
from ..sessions import get_calendar
from ..storage import MarketStore
from ..strategies import Signals, TradeStrategy, available, create
from ..strategies import base as strategy_base
from ..timeframes import seconds
from .account import RiskRules, run_account
from .costs import scenarios_for
from .engine import simulate
from .metrics import compute
from ..quality import continuous_start, dense_start
from . import resources
from . import walkforward as wf_module
from .walkforward import objective, slice_trades, split_periods, wf_windows

SELECTION_SCENARIO = "konservativ"
RISK_VARIANTS = (0.0025, 0.005, 0.01)

# Qualitätsanforderungen für "geeignet für den Demohandel" (konservatives Kostenszenario, Risiko laut RiskRules:
# seit 20.10.2026 1 % je Trade und 20 % Drawdown-Grenze, vorher 0,25 % / 5 % – Freigabe des Nutzers)
CRITERIA = {
    "validation_min_trades": 50, "validation_min_pf": 1.2, "validation_max_dd_pct": 20.0,
    "wf_min_trades": 50, "wf_min_positive_folds_pct": 55.0,
    "test_min_trades": 30, "test_min_pf": 1.1, "test_max_dd_pct": 20.0,
    "max_min_lot_skip_pct": 50.0,
}


def trade_strategies() -> list[str]:
    return [n for n in available() if issubclass(strategy_base._REGISTRY[n], TradeStrategy)]


def strategies_for(tf: str, names) -> list[str]:
    """Strategien, die auf diesem Zeitrahmen geprüft werden (TradeStrategy.timeframes)."""
    return [n for n in names if tf in strategy_base._REGISTRY[n].timeframes]


def _load_context(store: MarketStore, symbol: str, conv_tfs, start=None) -> dict:
    doc = store.read_json("specs.json", {}) or {}
    if symbol not in doc.get("instruments", {}):
        raise RuntimeError(f"Keine Kontraktdaten für {symbol} in data/specs.json – zuerst daten_aktualisieren.py ausführen")
    spec = InstrumentSpec.from_dict(doc["instruments"][symbol])
    conv = CurrencyConverter.from_store(store, conversion_sources(doc), timeframes=tuple(conv_tfs), start=start)
    acc = doc["account"]
    return {"spec": spec, "conv": conv, "account_ccy": acc["currency"], "leverage": float(acc["leverage"]),
            "rollover3": spec.swap_rollover3days, "doc": doc}


def _feasibility(bars, spec, conv, account_ccy, rules: RiskRules, test_start) -> dict:
    """Erlaubt die Mindestlotgröße das Risiko pro Trade überhaupt?"""
    rate = float(conv.rate(spec.currency_profit, account_ccy)[0])
    risk_eur = rules.initial * rules.risk_per_trade
    max_stop = risk_eur / (spec.volume_min * spec.contract_size * rate) if rate > 0 else float("nan")
    h, l, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = ind.atr(h, l, c, 14)
    m = (bars["time"] >= test_start).to_numpy()
    a_test = a[m & np.isfinite(a)]
    if len(a_test) == 0:
        return {}
    return {
        "risk_eur_per_trade": round(risk_eur, 2),
        "max_stop_distance_at_min_lot": round(max_stop, 4),
        "atr14_median_test": round(float(np.median(a_test)), 4),
        "share_bars_1_5atr_feasible_pct": round(100 * float(np.mean(1.5 * a_test <= max_stop)), 1),
        "share_bars_3atr_feasible_pct": round(100 * float(np.mean(3.0 * a_test <= max_stop)), 1),
    }


# ---------------------------------------------------------------------- Speicher- und Blockverarbeitung
JOB_COLUMNS = ["open", "high", "low", "close", "tick_volume", "spread"]   # tick_volume für den Wochen-VWAP
DEFAULT_BLOCK = 100_000      # Kerzen je Block für die Signalberechnung
MIN_BLOCK = 10_000           # kleinste Blockgröße bei automatischer Verkleinerung
WARMUP = 10_000              # Vorlauf je Block: Indikatoren schwingen ein (EMA 200: Restfehler < 1e-40)
LOW_MEMORY_MB = 1500         # unter diesem freien RAM wird die Blockgröße vorsorglich halbiert


def _signals_blocked(name: str, grid: list[dict], bars: pd.DataFrame, block: int | None, warmup: int = WARMUP):
    """Signale aller Parametersätze einer Strategie, blockweise berechnet.

    Jeder Block [start, end) wird mit `warmup` Kerzen Vorlauf gerechnet; übernommen werden nur Signale mit
    Index im Block. Da alle Strategien kausal sind (Look-ahead-Test), entsprechen die Signale exakt denen
    der Gesamtberechnung, sobald die Indikatoren eingeschwungen sind (Test + Prüfung im Testlauf).
    Je Block werden Indikatoren über features.shared() einmal berechnet und von allen Parametersätzen genutzt."""
    n = len(bars)
    if not block or n <= block:
        with features.shared(bars):
            return [create(name, **p).entries(bars) for p in grid]
    span = strategy_base._REGISTRY[name].warmup_seconds
    t_ns = pd.DatetimeIndex(bars["time"]).as_unit("ns").asi8 if span else None
    acc = [{"idx": [], "d": [], "s": [], "t": []} for _ in grid]
    exl, exs, mb = [None] * len(grid), [None] * len(grid), [0] * len(grid)
    for start in range(0, n, block):
        end = min(n, start + block)
        w0 = max(0, start - warmup)
        if span:                              # zeitlicher Vorlauf (z. B. ganze Woche für den Wochen-VWAP)
            w0 = min(w0, int(np.searchsorted(t_ns, t_ns[start] - span * 10**9)))
        off = start - w0
        sub = bars.iloc[w0:end].reset_index(drop=True)
        with features.shared(sub):
            for pi, p in enumerate(grid):
                sg = create(name, **p).entries(sub)
                m = sg.idx >= off
                acc[pi]["idx"].append(sg.idx[m] + w0)
                acc[pi]["d"].append(sg.direction[m])
                acc[pi]["s"].append(sg.stop[m])
                acc[pi]["t"].append(sg.target[m])
                mb[pi] = sg.max_bars
                for store_, arr in ((exl, sg.exit_long), (exs, sg.exit_short)):
                    if arr is not None:
                        if store_[pi] is None:
                            store_[pi] = np.zeros(n, dtype=bool)
                        store_[pi][start:end] = np.asarray(arr, dtype=bool)[off:]
        del sub
    out = []
    for pi in range(len(grid)):
        cat = lambda key, dt: np.concatenate(acc[pi][key]).astype(dt) if acc[pi][key] else np.array([], dtype=dt)
        out.append(Signals(cat("idx", int), cat("d", int), cat("s", float), cat("t", float), mb[pi], exl[pi], exs[pi]))
    return out


def compute_signals(name: str, grid: list[dict], bars: pd.DataFrame, state: dict, log=print):
    """Wie _signals_blocked, verkleinert bei Speichermangel automatisch die Blockgröße (bleibt danach klein)."""
    while True:
        try:
            return _signals_blocked(name, grid, bars, state.get("block"))
        except MemoryError:
            resources.free_memory()
            blk = state.get("block") or len(bars)
            if blk <= MIN_BLOCK:
                raise
            state["block"] = max(MIN_BLOCK, blk // 2)
            log(f"  Speichermangel bei {name}: Blockgröße auf {state['block']:,} Kerzen verkleinert, neuer Versuch")


# ---------------------------------------------------------------------- Fingerabdruck und Prüfpunkte
_CODE_FILES = ["indicators.py", "features.py", "instruments.py", "quality.py", "sessions.py", "storage.py",
               "strategies/base.py", "strategies/library.py", "backtest/account.py", "backtest/costs.py",
               "backtest/engine.py", "backtest/metrics.py", "backtest/walkforward.py", "backtest/runner.py"]


def code_fingerprint() -> str:
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha256()
    for f in _CODE_FILES:
        h.update(f.encode())
        h.update((root / f).read_bytes().replace(b"\r\n", b"\n"))   # Windows-Zeilenenden ignorieren
    return h.hexdigest()[:16]


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def job_fingerprint(cfg: Config, store: MarketStore, symbol: str, tf: str) -> str:
    """Code + Einstellungen + Datenstand. Block- und Prozessanzahl gehören bewusst NICHT dazu – sie
    verändern die Ergebnisse nicht."""
    doc = store.read_json("specs.json", {}) or {}
    inst = {k: {kk: (round(vv, 4) if kk == "margin_rate" else vv) for kk, vv in v.items() if kk != "tick_value"}
            for k, v in doc.get("instruments", {}).items()}
    conv_syms = [c["symbol"] for c in conversion_sources(doc)]
    data = {"job": store.bounds(symbol, tf), "h1": store.bounds(symbol, "H1"),
            "conv": {s_: [store.bounds(s_, t) for t in cfg.conversion_timeframes] for s_ in conv_syms}}
    settings = {"criteria": CRITERIA, "risk": asdict(RiskRules()), "risk_variants": RISK_VARIANTS,
                "selection": SELECTION_SCENARIO, "costs": {k: v.to_dict() for k, v in scenarios_for(symbol).items()},
                "calendar": cfg.calendar_for(symbol), "conv_tfs": list(cfg.conversion_timeframes),
                "min_trades_select": wf_module.MIN_TRADES_SELECT, "account": doc.get("account", {}).get("currency"),
                "leverage": doc.get("account", {}).get("leverage"), "scheme": doc.get("time_scheme", {}).get("scheme")}
    return _hash({"code": code_fingerprint(), "settings": settings, "data": data, "inst": inst.get(symbol),
                  "conversions": doc.get("conversions"), "symbol": symbol, "tf": tf})


class Checkpoints:
    """Eine Datei je abgeschlossener Testeinheit (Markt × Zeitrahmen × Strategie), atomar geschrieben.
    Wiederverwendet wird nur, was zum aktuellen Fingerabdruck passt."""

    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol, tf, name):
        return self.dir / f"{symbol}_{tf}_{name}.json"

    def load(self, symbol, tf, name, fp):
        f = self._path(symbol, tf, name)
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None                       # beschädigte Datei -> neu berechnen
        return d.get("result") if d.get("fingerprint") == fp else None

    def save(self, symbol, tf, name, fp, result):
        f = self._path(symbol, tf, name)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"fingerprint": fp, "saved_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                                   "result": result}, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, f)


class Progress:
    def __init__(self, total: int):
        self.total, self.done, self.computed_s, self.computed_n, self.t0 = total, 0, 0.0, 0, time.time()

    def tick(self, seconds_: float | None) -> str:
        self.done += 1
        if seconds_ is not None:
            self.computed_s += seconds_
            self.computed_n += 1
        rest = self.total - self.done
        eta = (self.computed_s / self.computed_n * rest) if self.computed_n else 0
        el = time.time() - self.t0
        return (f"[{self.done:>3}/{self.total}] verstrichen {int(el // 60)}:{int(el % 60):02d} min, "
                f"Rest ~{int(eta // 60)} min")


# ---------------------------------------------------------------------- Testeinheiten
def run_job(cfg: Config, symbol: str, tf: str, strategy_names: list[str] | None = None, log=print,
            block_bars: int | None = DEFAULT_BLOCK, checkpoints: Checkpoints | None = None,
            progress: Progress | None = None, state: dict | None = None) -> dict:
    t0 = time.time()
    names = strategies_for(tf, strategy_names or trade_strategies())
    store = MarketStore(cfg.data_root)
    state = state if state is not None else {"block": block_bars}
    fp_job = job_fingerprint(cfg, store, symbol, tf)

    # Alle Einheiten schon vorhanden? Dann Daten gar nicht erst laden.
    cached = {n: checkpoints.load(symbol, tf, n, _hash([fp_job, n])) for n in names} if checkpoints else {}
    meta = checkpoints.load(symbol, tf, "_job", fp_job) if checkpoints else None
    if checkpoints and meta and all(v is not None for v in cached.values()):
        for n in names:
            log(f"  {symbol} {tf:<3} {n:<24} übernommen (Prüfpunkt)" + (f" {progress.tick(None)}" if progress else ""))
        return {**meta, "results": [cached[n] for n in names], "resumed": len(names)}

    # Zeiträume ab dichter Historie (Referenz H1); nur benötigte Spalten und Jahre laden
    cal = get_calendar(cfg.calendar_for(symbol))
    ref = store.load(symbol, "H1", columns=[])
    if ref.empty:
        ref = store.load(symbol, tf, columns=[])
    if ref.empty:
        return {"symbol": symbol, "tf": tf, "error": "keine Daten"}
    first = max(continuous_start(ref), dense_start(ref, "H1" if not store.bounds(symbol, "H1") is None else tf, cal))
    del ref
    bars = store.load(symbol, tf, start=first, columns=JOB_COLUMNS)
    if bars.empty:
        return {"symbol": symbol, "tf": tf, "error": "keine Daten"}
    first = max(first, continuous_start(bars))
    if first > bars["time"].iloc[0]:
        bars = bars[bars["time"] >= first].reset_index(drop=True)
    last = pd.Timestamp(bars["time"].iloc[-1])
    periods = split_periods(first.normalize(), last)
    log(f"Lade {symbol} {tf}: {len(bars):,} Kerzen {str(bars['time'].iloc[0])[:10]} – {str(last)[:10]} "
        f"(Spalten {', '.join(JOB_COLUMNS)}) | {resources.status()}")
    ctx = _load_context(store, symbol, cfg.conversion_timeframes, start=first)
    spec, conv = ctx["spec"], ctx["conv"]
    scen = scenarios_for(symbol)
    rules = RiskRules()
    tf_s = seconds(tf)
    spreads = {k: v.spread_price(bars["spread"].to_numpy(), spec.point) for k, v in scen.items()}

    def account(trades, scenario, r=rules):
        return run_account(trades, spec, conv, ctx["account_ccy"], ctx["leverage"], scen[scenario], r,
                           cal.always_open, ctx["rollover3"])

    def metrics(trades, scenario, a, b, r=rules, raw_skipped=None):
        res = account(trades, scenario, r)
        return compute(res, r.initial, pd.Timestamp(a), pd.Timestamp(b), raw_skipped), res

    meta = {"symbol": symbol, "tf": tf, "bars": int(len(bars)), "first": str(first)[:16], "last": str(last)[:16],
            "bars_per_year": {str(k): int(v) for k, v in bars.groupby(bars["time"].dt.year).size().items()},
            "periods": periods.to_dict(), "cost_scenarios": {k: v.to_dict() for k, v in scen.items()},
            "feasibility": _feasibility(bars, spec, conv, ctx["account_ccy"], rules, periods.val_end)}
    if checkpoints:
        checkpoints.save(symbol, tf, "_job", fp_job, meta)

    results = []
    for name in names:
        fp_unit = _hash([fp_job, name])
        if cached.get(name) is not None:
            results.append(cached[name])
            log(f"  {symbol} {tf:<3} {name:<24} übernommen (Prüfpunkt)" + (f" {progress.tick(None)}" if progress else ""))
            continue
        ts0 = time.time()
        avail, _ = resources.system_mb()
        if avail is not None and avail < LOW_MEMORY_MB and (state.get("block") or len(bars)) > MIN_BLOCK:
            state["block"] = max(MIN_BLOCK, (state.get("block") or len(bars)) // 2)
            log(f"  Wenig freier RAM ({avail:,.0f} MB): Blockgröße vorsorglich auf {state['block']:,} Kerzen")
        try:
            res = _evaluate_strategy(name, bars, periods, scen, rules, tf_s, spreads, metrics, state, log)
        except Exception as e:  # eine fehlerhafte Einheit darf den Gesamtlauf nicht abbrechen
            log(f"  FEHLER {symbol} {tf} {name}: {type(e).__name__}: {e} | {resources.status()}")
            results.append({"strategy": name, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
            resources.free_memory()
            if progress:
                progress.tick(time.time() - ts0)
            continue
        res["runtime_s"] = round(time.time() - ts0, 1)
        res["memory"] = resources.snapshot()
        res["block_bars"] = state.get("block") if len(bars) > (state.get("block") or len(bars)) else None
        if checkpoints:
            checkpoints.save(symbol, tf, name, fp_unit, res)
        results.append(res)
        ev = res["evaluation"][SELECTION_SCENARIO]
        log(f"  {symbol} {tf:<3} {name:<24} Val {_fmt(ev['validation'])} | Test {_fmt(ev['test'])} "
            f"({time.time() - ts0:.0f}s) | {resources.status()}" + (f" {progress.tick(time.time() - ts0)}" if progress else ""))
        resources.free_memory()
    del bars, spreads
    resources.free_memory()
    return {**meta, "results": results, "runtime_s": round(time.time() - t0, 1)}


def _evaluate_strategy(name, bars, periods, scen, rules, tf_s, spreads, metrics, state, log) -> dict:
    cls = strategy_base._REGISTRY[name]
    grid = cls.param_grid or [{}]
    raw: dict[str, dict[int, tuple]] = {k: {} for k in scen}
    n_signals = {}
    sigs = compute_signals(name, grid, bars, state, log)
    for pi, sig in enumerate(sigs):
        n_signals[pi] = len(sig)
        for sk in scen:
            raw[sk][pi] = simulate(bars, sig, tf_s, spreads[sk], scen[sk].slippage_frac, rules.max_positions)
    del sigs

    # ---- Walk-Forward im Trainingszeitraum (Auswahl je Fenster nur In-Sample)
    sel = raw[SELECTION_SCENARIO]
    folds, oos_parts, chosen = [], [], []
    for is_a, is_b, oos_a, oos_b in wf_windows(periods.start, periods.train_end):
        scores = {pi: objective(metrics(slice_trades(sel[pi][0], is_a, is_b, True), SELECTION_SCENARIO,
                                        is_a, is_b)[0]) for pi in sel}
        best = max(scores, key=lambda k: scores[k])
        if not math.isfinite(scores[best]):
            folds.append({"oos": [str(oos_a)[:10], str(oos_b)[:10]], "params": None, "net_eur": None})
            continue
        part = slice_trades(sel[best][0], oos_a, oos_b)
        fm, _ = metrics(part, SELECTION_SCENARIO, oos_a, oos_b)
        folds.append({"oos": [str(oos_a)[:10], str(oos_b)[:10]], "params": grid[best],
                      "net_eur": fm["net_profit_eur"], "trades": fm["trades"]})
        oos_parts.append(part)
        chosen.append(best)
    if oos_parts:
        stitched = pd.concat(oos_parts).sort_values("entry_time").reset_index(drop=True)
        wf_m, _ = metrics(stitched, SELECTION_SCENARIO, periods.start, periods.train_end)
    else:
        wf_m = None
    valid_folds = [f for f in folds if f["net_eur"] is not None]
    wf_pos = (100 * sum(1 for f in valid_folds if f["net_eur"] > 0) / len(valid_folds)) if valid_folds else None

    # ---- Endgültige Parameterwahl auf dem gesamten Trainingszeitraum
    tr_a, tr_b = periods.window("train")
    tr_scores = {pi: objective(metrics(slice_trades(sel[pi][0], tr_a, tr_b, True), SELECTION_SCENARIO,
                                       tr_a, tr_b)[0]) for pi in sel}
    best = max(tr_scores, key=lambda k: tr_scores[k])

    evals = {}
    for sk in scen:
        evals[sk] = {}
        for per in ("train", "validation", "test"):
            a, b = periods.window(per)
            m, _ = metrics(slice_trades(raw[sk][best][0], a, b), sk, a, b, raw_skipped=None)
            evals[sk][per] = m
    risk_var = {}
    for rv in RISK_VARIANTS:
        r = RiskRules(risk_per_trade=rv)
        risk_var[f"{rv * 100:g}%"] = {
            per: metrics(slice_trades(sel[best][0], *periods.window(per)), SELECTION_SCENARIO,
                         *periods.window(per), r=r)[0] for per in ("validation", "test")}
    return {
        "strategy": name, "description": cls.description, "rules": cls.rules,
        "params": grid[best], "param_grid_size": len(grid), "train_objective": _num(tr_scores[best]),
        "signals": n_signals[best], "raw_skipped": raw[SELECTION_SCENARIO][best][1],
        "walk_forward": {"metrics": wf_m, "positive_folds_pct": _num(wf_pos), "folds": folds,
                         "param_stability_pct": _num(100 * max(chosen.count(c) for c in set(chosen)) / len(chosen))
                         if chosen else None},
        "evaluation": evals, "risk_variants": risk_var,
    }


def _num(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 3)


def _fmt(m):
    if not m or not m["trades"]:
        return "0 Trades"
    return (f"{m['trades']:>4} Tr {m['net_profit_eur']:>8.2f} € PF {m['profit_factor'] or 0:>5.2f} "
            f"DD {m['max_drawdown_pct']:>5.1f}%")


# ---------------------------------------------------------------------- Bewertung und Rangliste
def assess(r: dict) -> tuple[bool, list[str], float]:
    """Qualifikation nach CRITERIA und Rangwert (höher = besser)."""
    c = CRITERIA
    ev = r["evaluation"][SELECTION_SCENARIO]
    v, t, wf = ev["validation"], ev["test"], r["walk_forward"]["metrics"]
    reasons = []

    def chk(cond, text):
        if not cond:
            reasons.append(text)

    chk(v["trades"] >= c["validation_min_trades"], f"Validierung: nur {v['trades']} Trades (< {c['validation_min_trades']})")
    chk((v["net_profit_eur"] or 0) > 0 and (v["expectancy_r"] or 0) > 0, "Validierung: Erwartungswert nach Kosten <= 0")
    chk((v["profit_factor"] or 0) >= c["validation_min_pf"], f"Validierung: Profitfaktor {v['profit_factor']} < {c['validation_min_pf']}")
    chk((v["max_drawdown_pct"] or 0) <= c["validation_max_dd_pct"], f"Validierung: Drawdown {v['max_drawdown_pct']} % > {c['validation_max_dd_pct']} %")
    chk(wf is not None and wf["trades"] >= c["wf_min_trades"] and (wf["net_profit_eur"] or 0) > 0,
        "Walk-Forward: zu wenige Trades oder Verlust")
    chk((r["walk_forward"]["positive_folds_pct"] or 0) >= c["wf_min_positive_folds_pct"],
        f"Walk-Forward: nur {r['walk_forward']['positive_folds_pct']} % positive Abschnitte")
    chk(t["trades"] >= c["test_min_trades"], f"Test: nur {t['trades']} Trades")
    chk((t["net_profit_eur"] or 0) > 0 and (t["profit_factor"] or 0) >= c["test_min_pf"], "Test: nicht profitabel")
    chk((t["max_drawdown_pct"] or 0) <= c["test_max_dd_pct"], f"Test: Drawdown {t['max_drawdown_pct']} %")
    mls = v.get("min_lot_skip_pct")
    chk(mls is None or mls <= c["max_min_lot_skip_pct"], f"Mindestlot: {mls} % der Trades nicht ausführbar")

    def clip(x, lo, hi):
        return max(lo, min(hi, x))
    score = (0.35 * clip((v["t_stat"] or 0) / 3, -1, 1)
             + 0.25 * clip(((r["walk_forward"]["positive_folds_pct"] or 0) - 50) / 50, -1, 1)
             + 0.25 * clip(((t["profit_factor"] or 0) - 1) / 1, -1, 1)
             - 0.15 * clip(((max(v["max_drawdown_pct"] or 0, t["max_drawdown_pct"] or 0)) - c["test_max_dd_pct"])
                           / (2 * c["test_max_dd_pct"]), 0, 1))
    return len(reasons) == 0, reasons, round(score, 4)


def rank(jobs: list[dict]) -> dict:
    out = {}
    for sym in sorted({j["symbol"] for j in jobs}):
        rows = []
        for j in jobs:
            if j["symbol"] != sym or "results" not in j:
                continue
            for r in j["results"]:
                if "error" in r:
                    continue
                ok, reasons, score = assess(r)
                ev = r["evaluation"][SELECTION_SCENARIO]
                rows.append({"tf": j["tf"], "strategy": r["strategy"], "params": r["params"], "qualified": ok,
                             "score": score, "reasons": reasons,
                             "validation": _brief(ev["validation"]), "test": _brief(ev["test"]),
                             "walk_forward": _brief(r["walk_forward"]["metrics"]),
                             "wf_positive_folds_pct": r["walk_forward"]["positive_folds_pct"],
                             "risk_variants": {k: {p: _brief(m) for p, m in v.items()}
                                               for k, v in r["risk_variants"].items()}})
        rows.sort(key=lambda x: (not x["qualified"], -x["score"]))
        for i, row in enumerate(rows, 1):
            row["rank"] = i
        out[sym] = {"ranking": rows, "qualified": [r for r in rows if r["qualified"]],
                    "verdict": ("geeignete Strategie gefunden" if any(r["qualified"] for r in rows)
                                else "aktuell keine Strategie für den Demohandel geeignet")}
    return out


def _brief(m):
    if not m:
        return None
    keys = ("trades", "net_profit_eur", "return_pct", "max_drawdown_pct", "max_drawdown_eur", "profit_factor",
            "win_rate_pct", "expectancy_eur", "expectancy_r", "t_stat", "sharpe", "avg_hold_hours", "positive_years_pct",
            "min_lot_skip_pct", "dd_limit_breached")
    return {k: m.get(k) for k in keys}


# ---------------------------------------------------------------------- Gesamtlauf
def _safe_job(cfg, sym, tf, names, log, **kw) -> dict:
    try:
        return run_job(cfg, sym, tf, names, log=log, **kw)
    except Exception as e:  # z. B. fehlende Daten – übrige Märkte/Zeitrahmen laufen weiter
        log(f"FEHLER {sym} {tf}: {type(e).__name__}: {e}")
        return {"symbol": sym, "tf": tf, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}


def _job_entry(args):
    cfg, sym, tf, names, block, ckpt_dir = args
    lines = []
    res = _safe_job(cfg, sym, tf, names, lines.append, block_bars=block,
                    checkpoints=Checkpoints(ckpt_dir) if ckpt_dir else None)
    return res, lines


def safe_workers(requested: int, log=print) -> int:
    """Mehrere Prozesse duplizieren die Datensätze im Speicher. Nur erlauben, wenn genug RAM frei ist
    (Richtwert 2,5 GB je Prozess); sonst 1 Prozess."""
    if requested <= 1:
        return 1
    avail, total = resources.system_mb()
    if avail is None or avail < requested * 2500:
        log(f"Hinweis: {requested} Prozesse angefordert, aber nur "
            f"{'unbekannt viel' if avail is None else f'{avail / 1024:.1f} GB'} RAM frei – es wird 1 Prozess verwendet.")
        return 1
    return requested


def run_all(cfg: Config, symbols, timeframes, strategy_names=None, workers: int = 1, log=print,
            block_bars: int | None = DEFAULT_BLOCK, checkpoint_dir=None) -> dict:
    """Märkte nacheinander (Standard: 1 Prozess), je Markt die Zeitrahmen nacheinander."""
    names = strategy_names or trade_strategies()
    workers = safe_workers(workers, log)
    ckpt = Checkpoints(checkpoint_dir) if checkpoint_dir else None
    jobs = [(s, tf) for s in symbols for tf in timeframes if strategies_for(tf, names)]
    expected = {(s, tf, n) for s, tf in jobs for n in strategies_for(tf, names)}
    progress = Progress(len(expected))
    log(f"{len(expected)} Testeinheiten ({len(symbols)} Märkte × {len(timeframes)} Zeitrahmen × "
        f"{len(names)} Strategien, jede nur auf ihren Zeitrahmen), Blockgröße {block_bars or 'aus'}, "
        f"{workers} Prozess(e) | {resources.status()}")
    out = []
    state = {"block": block_bars}
    if workers <= 1:
        for sym, tf in jobs:
            out.append(_safe_job(cfg, sym, tf, names, log, block_bars=block_bars, checkpoints=ckpt,
                                 progress=progress, state=state))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_job_entry, (cfg, s, tf, names, block_bars, checkpoint_dir)): (s, tf) for s, tf in jobs}
            for f in as_completed(futs):
                try:
                    res, lines = f.result()
                except Exception as e:  # z. B. Prozess durch Speichermangel beendet
                    sym, tf = futs[f]
                    res = {"symbol": sym, "tf": tf, "error": f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc()}
                    lines = [f"FEHLER {sym} {tf}: {type(e).__name__}: {e}"]
                for line in lines:
                    log(line)
                log(f"Fertig: {res['symbol']} {res['tf']} ({res.get('runtime_s')} s) | {resources.status()}")
                out.append(res)
    order = {tf: i for i, tf in enumerate(timeframes)}
    out.sort(key=lambda r: (r["symbol"], order.get(r["tf"], 99)))
    done = {(j["symbol"], j["tf"], r["strategy"]) for j in out for r in j.get("results", []) if "error" not in r}
    return {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "criteria": CRITERIA,
            "selection_scenario": SELECTION_SCENARIO, "risk_rules": asdict(RiskRules()),
            "risk_variants": [f"{x * 100:g}%" for x in RISK_VARIANTS], "code_fingerprint": code_fingerprint(),
            "units_expected": len(expected), "units_completed": len(done),
            "units_missing": sorted(f"{s} {tf} {n}" for s, tf, n in expected - done),
            "errors": [{"symbol": j["symbol"], "tf": j["tf"], "strategy": r.get("strategy"), "error": r["error"]}
                       for j in out for r in ([j] if "error" in j else [x for x in j.get("results", []) if "error" in x])],
            "jobs": out, "rankings": rank(out)}


# ---------------------------------------------------------------------- Testlauf
TESTLAUF_BARS = 150_000
TESTLAUF_BLOCK = 20_000      # bewusst klein, damit die Blockgrenzen wirklich geprüft werden


def _same_signals(a: Signals, b: Signals) -> bool:
    if not (np.array_equal(a.idx, b.idx) and np.array_equal(a.direction, b.direction)
            and np.array_equal(a.stop, b.stop) and np.array_equal(a.target, b.target, equal_nan=True)
            and a.max_bars == b.max_bars):
        return False
    for x, y in ((a.exit_long, b.exit_long), (a.exit_short, b.exit_short)):
        if (x is None) != (y is None) or (x is not None and not np.array_equal(np.asarray(x, bool), np.asarray(y, bool))):
            return False
    return True


def _canonical(result: dict) -> str:
    return _hash({k: v for k, v in result.items() if k not in ("runtime_s", "memory", "block_bars")})


def run_testlauf(cfg: Config, symbols, strategy_names=None, log=print, checkpoint_dir=None) -> dict:
    """Kleiner Testlauf vor dem vollständigen Backtest (auf echten Daten):
    1. Blockweise Signalberechnung = Gesamtberechnung (letzte 150 000 M1-Kerzen je Markt, alle Strategien
       und Parametersätze, Blockgröße 20 000)
    2. Alle Strategien auf H1 je Markt vollständig (Ergebnisse zählen als Prüfpunkte für den Hauptlauf)
    3. Reproduzierbarkeit: eine Einheit ein zweites Mal ohne Prüfpunkt rechnen – Ergebnis muss identisch sein."""
    names = strategy_names or trade_strategies()
    t0 = time.time()
    store = MarketStore(cfg.data_root)
    log(f"TESTLAUF | {resources.status()} | Code {code_fingerprint()}")
    mismatches, errors, peak = [], [], 0.0
    for sym in symbols:
        # feinster vorhandener Zeitrahmen (normalerweise M1)
        tf_test = next((t for t in ("M1", "M5", "M15", "M30", "H1") if store.bounds(sym, t)), None)
        if tf_test is None:
            errors.append(f"{sym}: keine Kursdaten")
            continue
        b = store.bounds(sym, tf_test)
        last_utc = pd.Timestamp(b["last_server"], unit="s", tz="UTC")
        span = pd.Timedelta(seconds=seconds(tf_test) * TESTLAUF_BARS * 3)
        bars = store.load(sym, tf_test, start=last_utc - span, columns=JOB_COLUMNS)
        bars = bars.iloc[-TESTLAUF_BARS:].reset_index(drop=True)
        log(f"  {sym} {tf_test}: {len(bars):,} Kerzen – Blockberechnung (Blöcke à {TESTLAUF_BLOCK:,}) gegen "
            f"Gesamtberechnung je Strategie")
        for name in names:
            grid = strategy_base._REGISTRY[name].param_grid or [{}]
            try:
                full = _signals_blocked(name, grid, bars, None)
                part = _signals_blocked(name, grid, bars, TESTLAUF_BLOCK)
            except Exception as e:
                errors.append(f"{sym} {name}: {type(e).__name__}: {e}")
                continue
            bad = [json.dumps(p) for p, x, y in zip(grid, full, part) if not _same_signals(x, y)]
            nsig = sum(len(x) for x in full)
            pm = resources.process_mb() or 0
            peak = max(peak, pm)
            log(f"    {name:<24} {nsig:>7} Signale  {'IDENTISCH' if not bad else 'ABWEICHUNG ' + ', '.join(bad)}"
                f" | {resources.status()}")
            mismatches += [f"{sym} {name} {p}" for p in bad]
        del bars
        resources.free_memory()

    log("  Vollständige H1-Einheiten (werden als Prüfpunkte für den Hauptlauf gespeichert)")
    ckpt = Checkpoints(checkpoint_dir) if checkpoint_dir else None
    h1 = run_all(cfg, symbols, ["H1"], names, workers=1, log=log, checkpoint_dir=checkpoint_dir)
    errors += [f"{e['symbol']} {e['tf']} {e.get('strategy')}: {e['error']}" for e in h1["errors"]]

    reproducible = None
    h1_names = strategies_for("H1", names)
    if symbols and h1_names:
        first_job = next((j for j in h1["jobs"] if j["symbol"] == symbols[0] and "results" in j), None)
        if first_job:
            ref = next((r for r in first_job["results"] if r.get("strategy") == h1_names[0] and "error" not in r), None)
            again = run_job(cfg, symbols[0], "H1", [h1_names[0]], log=lambda *a: None, checkpoints=None)
            rep = next((r for r in again.get("results", []) if "error" not in r), None)
            reproducible = ref is not None and rep is not None and _canonical(ref) == _canonical(rep)
            log(f"  Reproduzierbarkeit {symbols[0]} H1 {h1_names[0]}: {'IDENTISCH' if reproducible else 'ABWEICHUNG'}")
    ok = not mismatches and not errors and reproducible is not False
    summary = {"ok": ok, "code_fingerprint": code_fingerprint(), "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
               "signal_mismatches": mismatches, "errors": errors, "reproducible": reproducible,
               "peak_process_mb": round(peak), "runtime_s": round(time.time() - t0, 1), "memory": resources.snapshot()}
    if ckpt:
        (ckpt.dir / "testlauf.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"TESTLAUF {'BESTANDEN' if ok else 'NICHT BESTANDEN'} in {summary['runtime_s']:.0f} s"
        + ("" if ok else f" – Abweichungen: {mismatches[:5]} Fehler: {errors[:5]}"))
    return summary


def testlauf_ok(checkpoint_dir) -> bool:
    f = Path(checkpoint_dir) / "testlauf.json"
    if not f.exists():
        return False
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(d.get("ok")) and d.get("code_fingerprint") == code_fingerprint()


# ---------------------------------------------------------------------- Abgleich mit einem früheren Lauf
def compare_reference(data: dict, ref: dict) -> dict:
    """Ergebnisse mit einem früheren Lauf vergleichen (z. B. Lauf 2 aus docs/phase3_zusammenfassung.json).
    Verglichen wird nur, wo die Zeiträume identisch sind (sonst haben sich die Daten geändert)."""
    keys = ("trades", "net_profit_eur", "profit_factor", "max_drawdown_pct", "expectancy_r")
    rj = {(j["symbol"], j["tf"]): j for j in ref.get("jobs", [])}
    out = {"compared": 0, "identical": 0, "different": [], "skipped_changed_periods": []}
    for j in data.get("jobs", []):
        r = rj.get((j.get("symbol"), j.get("tf")))
        if not r or "results" not in j:
            continue
        if r.get("periods") != j.get("periods"):
            out["skipped_changed_periods"].append(f"{j['symbol']} {j['tf']}")
            continue
        rr = {x["strategy"]: x for x in r.get("results", []) if "error" not in x}
        for x in j["results"]:
            y = rr.get(x.get("strategy"))
            if "error" in x or y is None:
                continue
            out["compared"] += 1
            same = x["params"] == y["params"]
            for per in ("validation", "test"):
                a, b = x["evaluation"][SELECTION_SCENARIO][per], y["evaluation"][SELECTION_SCENARIO][per]
                same &= all(a.get(k) == b.get(k) for k in keys)
            if same:
                out["identical"] += 1
            else:
                out["different"].append(f"{j['symbol']} {j['tf']} {x['strategy']}")
    return out
