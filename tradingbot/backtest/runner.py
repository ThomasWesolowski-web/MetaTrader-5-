"""Ablauf Phase 3: alle Strategien × Märkte × Zeitrahmen testen, bewerten und eine Rangliste erstellen.

Je (Markt, Zeitrahmen) ein Job (parallelisierbar). Je Strategie und Parametersatz werden die Signale
einmal auf den vollständigen Daten berechnet (kausal – nur Vergangenheit) und daraus Trades simuliert.
Die Parameterwahl nutzt ausschließlich Trainingsdaten (Walk-Forward und Gesamttraining).
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..config import Config
from ..instruments import CurrencyConverter, InstrumentSpec, conversion_sources
from ..sessions import get_calendar
from ..storage import MarketStore
from ..strategies import TradeStrategy, available, create
from ..strategies import base as strategy_base
from ..timeframes import seconds
from .account import RiskRules, run_account
from .costs import scenarios_for
from .engine import simulate
from .metrics import compute
from ..quality import continuous_start
from .walkforward import objective, slice_trades, split_periods, wf_windows

SELECTION_SCENARIO = "konservativ"
RISK_VARIANTS = (0.0025, 0.005, 0.01)

# Qualitätsanforderungen für "geeignet für den Demohandel" (konservatives Kostenszenario, 0,25 % Risiko)
CRITERIA = {
    "validation_min_trades": 50, "validation_min_pf": 1.2, "validation_max_dd_pct": 5.0,
    "wf_min_trades": 50, "wf_min_positive_folds_pct": 55.0,
    "test_min_trades": 30, "test_min_pf": 1.1, "test_max_dd_pct": 5.0,
    "max_min_lot_skip_pct": 50.0,
}


def trade_strategies() -> list[str]:
    return [n for n in available() if issubclass(strategy_base._REGISTRY[n], TradeStrategy)]


def _load_context(store: MarketStore, symbol: str, conv_tfs) -> dict:
    doc = store.read_json("specs.json", {}) or {}
    if symbol not in doc.get("instruments", {}):
        raise RuntimeError(f"Keine Kontraktdaten für {symbol} in data/specs.json – zuerst daten_aktualisieren.py ausführen")
    spec = InstrumentSpec.from_dict(doc["instruments"][symbol])
    conv = CurrencyConverter.from_store(store, conversion_sources(doc), timeframes=tuple(conv_tfs))
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


def run_job(cfg: Config, symbol: str, tf: str, strategy_names: list[str] | None = None,
            log=print) -> dict:
    t0 = time.time()
    store = MarketStore(cfg.data_root)
    ctx = _load_context(store, symbol, cfg.conversion_timeframes)
    spec, conv = ctx["spec"], ctx["conv"]
    bars = store.load(symbol, tf)
    if bars.empty:
        return {"symbol": symbol, "tf": tf, "error": "keine Daten"}
    # Zeiträume je Markt einheitlich für alle Zeitrahmen: ab Beginn der LÜCKENLOSEN Intraday-Historie
    # (Referenz H1). Frühere, nur vereinzelt vorhandene Kerzen werden nicht verwendet – sonst läge der
    # Trainingszeitraum großteils in einer Datenlücke (bei Gold: Intraday-Lücke ca. 2007–2017).
    ref = store.load(symbol, "H1") if tf != "H1" else bars
    ref = ref if not ref.empty else bars
    first = max(continuous_start(ref), continuous_start(bars))
    last = pd.Timestamp(bars["time"].iloc[-1])
    bars = bars[bars["time"] >= first].reset_index(drop=True)
    periods = split_periods(first.normalize(), last)
    cal = get_calendar(cfg.calendar_for(symbol))
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

    names = strategy_names or trade_strategies()
    results = []
    for name in names:
        ts0 = time.time()
        cls = strategy_base._REGISTRY[name]
        grid = cls.param_grid or [{}]
        raw: dict[str, dict[int, tuple]] = {k: {} for k in scen}
        n_signals = {}
        for pi, params in enumerate(grid):
            strat = create(name, **params)
            sig = strat.entries(bars)
            n_signals[pi] = len(sig)
            for sk in scen:
                raw[sk][pi] = simulate(bars, sig, tf_s, spreads[sk], scen[sk].slippage_frac, rules.max_positions)

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
        results.append({
            "strategy": name, "description": cls.description, "rules": cls.rules,
            "params": grid[best], "param_grid_size": len(grid), "train_objective": _num(tr_scores[best]),
            "signals": n_signals[best], "raw_skipped": raw[SELECTION_SCENARIO][best][1],
            "walk_forward": {"metrics": wf_m, "positive_folds_pct": _num(wf_pos), "folds": folds,
                             "param_stability_pct": _num(100 * max(chosen.count(c) for c in set(chosen)) / len(chosen))
                             if chosen else None},
            "evaluation": evals, "risk_variants": risk_var,
            "runtime_s": round(time.time() - ts0, 1),
        })
        log(f"  {symbol} {tf:<3} {name:<24} Val {_fmt(evals[SELECTION_SCENARIO]['validation'])} | "
            f"Test {_fmt(evals[SELECTION_SCENARIO]['test'])} ({time.time() - ts0:.0f}s)")
    return {
        "symbol": symbol, "tf": tf, "bars": int(len(bars)), "first": str(first)[:16], "last": str(last)[:16],
        "bars_per_year": {str(k): int(v) for k, v in bars.groupby(bars["time"].dt.year).size().items()},
        "periods": periods.to_dict(), "cost_scenarios": {k: v.to_dict() for k, v in scen.items()},
        "feasibility": _feasibility(bars, spec, conv, ctx["account_ccy"], rules, periods.val_end),
        "results": results, "runtime_s": round(time.time() - t0, 1),
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
             - 0.15 * clip(((max(v["max_drawdown_pct"] or 0, t["max_drawdown_pct"] or 0)) - 5) / 10, 0, 1))
    return len(reasons) == 0, reasons, round(score, 4)


def rank(jobs: list[dict]) -> dict:
    out = {}
    for sym in sorted({j["symbol"] for j in jobs}):
        rows = []
        for j in jobs:
            if j["symbol"] != sym or "results" not in j:
                continue
            for r in j["results"]:
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
    keys = ("trades", "net_profit_eur", "return_pct", "max_drawdown_pct", "profit_factor", "win_rate_pct",
            "expectancy_eur", "expectancy_r", "t_stat", "sharpe", "avg_hold_hours", "positive_years_pct",
            "min_lot_skip_pct", "dd_limit_breached")
    return {k: m.get(k) for k in keys}


# ---------------------------------------------------------------------- Gesamtlauf
def _job_entry(args):
    cfg, sym, tf, names = args
    lines = []
    res = run_job(cfg, sym, tf, names, log=lines.append)
    return res, lines


def run_all(cfg: Config, symbols, timeframes, strategy_names=None, workers: int = 1, log=print) -> dict:
    jobs = [(cfg, s, tf, strategy_names) for s in symbols for tf in timeframes]
    out = []
    if workers <= 1:
        for j in jobs:
            log(f"Starte {j[1]} {j[2]} ...")
            out.append(run_job(*j, log=log))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_job_entry, j): j for j in jobs}
            for f in as_completed(futs):
                res, lines = f.result()
                for line in lines:
                    log(line)
                log(f"Fertig: {res['symbol']} {res['tf']} ({res.get('runtime_s')} s)")
                out.append(res)
    order = {tf: i for i, tf in enumerate(timeframes)}
    out.sort(key=lambda r: (r["symbol"], order.get(r["tf"], 99)))
    return {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "criteria": CRITERIA,
            "selection_scenario": SELECTION_SCENARIO, "risk_rules": asdict(RiskRules()),
            "risk_variants": [f"{x * 100:g}%" for x in RISK_VARIANTS],
            "jobs": out, "rankings": rank(out)}
