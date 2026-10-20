"""Automatischer Phase-3-Bericht (Markdown) und maschinenlesbare Ergebnisdatei (JSON)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .runner import CRITERIA, SELECTION_SCENARIO


def _v(x, suf=""):
    return "–" if x is None else f"{x}{suf}"


def _ok(job):
    return [r for r in job.get("results", []) if "error" not in r]


def _row(m):
    if not m or not m.get("trades"):
        return "0 | – | – | – | – | – | –"
    return (f"{m['trades']} | {_v(m['net_profit_eur'])} | {_v(m['profit_factor'])} | {_v(m['win_rate_pct'])} | "
            f"{_v(m['expectancy_r'])} | {_v(m['max_drawdown_pct'])} | {_v(m['sharpe'])}")


_OLD_RULES = {"initial": 1000.0, "risk_per_trade": 0.0025, "max_daily_loss": 0.01, "max_drawdown": 0.05,
              "max_positions": 2}   # Läufe vor dem 20.10.2026 speicherten die Regeln nicht immer


def _rules(data: dict) -> dict:
    return data.get("risk_rules") or _OLD_RULES


def _pct(x: float) -> str:
    return f"{100 * x:g} %".replace(".", ",")


def _eur(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def risk_text(data: dict) -> str:
    r = _rules(data)
    return (f"Risiko {_pct(r['risk_per_trade'])} je Trade, max. {_pct(r['max_daily_loss'])} Tagesverlust, "
            f"{_pct(r['max_drawdown'])} Drawdown als Testgrenze, max. {r['max_positions']} Positionen, "
            f"Startkapital {_eur(r['initial'], 0)}")


def build_markdown(data: dict, crosscheck: dict | None = None) -> str:
    jobs = [j for j in data["jobs"] if "results" in j]
    L = ["# Phase 3 – Backtest-Bericht (automatisch erzeugt)", "",
         f"- **Erstellt:** {data['created_utc'][:16].replace('T', ' ')} UTC",
         "- **Nur Simulation:** keine Orders an MetaTrader 5, keine Demo- oder Echtgeld-Trades",
         f"- **Auswahl/Rangliste auf Kostenszenario:** `{SELECTION_SCENARIO}` · {risk_text(data)}", ""]

    if "units_expected" in data:
        L += ["## Vollständigkeit", "",
              f"- **Testeinheiten abgeschlossen:** {data['units_completed']} von {data['units_expected']}"]
        if data.get("units_missing"):
            L.append(f"- **Fehlend ({len(data['units_missing'])}):** " + ", ".join(data["units_missing"]))
        else:
            L.append("- Alle vorgesehenen Märkte, Zeitrahmen und Strategien wurden getestet.")
        L.append("")
        L += ["## Ressourcen", "", "| Markt | TF | Kerzen | Laufzeit s | max. RAM Prozess MB | min. freier RAM MB | Blockgröße |",
              "|---|---|---|---|---|---|---|"]
        for j in jobs:
            mem = [r.get("memory") or {} for r in _ok(j)]
            pm = [m.get("process_mb") for m in mem if m.get("process_mb") is not None]
            av = [m.get("available_mb") for m in mem if m.get("available_mb") is not None]
            blk = sorted({r.get("block_bars") for r in _ok(j) if r.get("block_bars")})
            rt = sum(r.get("runtime_s") or 0 for r in _ok(j))
            L.append(f"| {j['symbol']} | {j['tf']} | {j.get('bars')} | {rt:.0f} | {max(pm) if pm else '–'} | "
                     f"{min(av) if av else '–'} | {', '.join(f'{b:,}' for b in blk) if blk else 'ohne Blöcke'} |")
        L.append("")
    rc = data.get("reference_comparison")
    if rc:
        L += ["## Abgleich mit Lauf 2 (Reproduzierbarkeit)", "",
              f"- Verglichene Einheiten (gleiche Zeiträume): {rc['compared']}, davon **identisch: {rc['identical']}**",
              f"- Abweichend: {', '.join(rc['different']) if rc['different'] else 'keine'}",
              f"- Nicht verglichen, weil sich die Zeiträume (Daten) geändert haben: "
              f"{', '.join(rc['skipped_changed_periods']) if rc['skipped_changed_periods'] else 'keine'}", ""]

    L += ["## Zeiträume (Training / Validierung / Test)", "", "| Markt | Training | Validierung | Test |", "|---|---|---|---|"]
    seen = set()
    for j in jobs:
        if j["symbol"] in seen:
            continue
        seen.add(j["symbol"])
        p = j["periods"]
        L.append(f"| {j['symbol']} | {p['start']} – {p['train_end']} | {p['train_end']} – {p['val_end']} | "
                 f"{p['val_end']} – {p['end']} |")
    L.append("")

    rr = _rules(data)
    L += [f"## Mindestlot-Prüfung ({_pct(rr['risk_per_trade'])} von {_eur(rr['initial'], 0)} = "
          f"{_eur(rr['initial'] * rr['risk_per_trade'])} Risiko)", "",
          "| Markt | TF | max. Stopabstand bei Mindestlot | ATR(14) Median (Testzeitraum) | Kerzen mit 1,5·ATR-Stop machbar | mit 3·ATR-Stop |",
          "|---|---|---|---|---|---|"]
    for j in jobs:
        f = j.get("feasibility") or {}
        L.append(f"| {j['symbol']} | {j['tf']} | {_v(f.get('max_stop_distance_at_min_lot'))} | {_v(f.get('atr14_median_test'))} | "
                 f"{_v(f.get('share_bars_1_5atr_feasible_pct'), ' %')} | {_v(f.get('share_bars_3atr_feasible_pct'), ' %')} |")
    L.append("")

    for sym, rk in data["rankings"].items():
        L += [f"## Rangliste {sym}", "", f"**Ergebnis: {rk['verdict']}.**", "",
              "| Rang | TF | Strategie | qualifiziert | Score | Val: Trades / Netto € / PF / DD % | Test: Trades / Netto € / PF / DD % | WF positive Abschnitte |",
              "|---|---|---|---|---|---|---|---|"]
        for r in rk["ranking"][:15]:
            v, t = r["validation"], r["test"]
            L.append(f"| {r['rank']} | {r['tf']} | {r['strategy']} | {'ja' if r['qualified'] else 'nein'} | {r['score']} | "
                     f"{v['trades']} / {_v(v['net_profit_eur'])} / {_v(v['profit_factor'])} / {_v(v['max_drawdown_pct'])} | "
                     f"{t['trades']} / {_v(t['net_profit_eur'])} / {_v(t['profit_factor'])} / {_v(t['max_drawdown_pct'])} | "
                     f"{_v(r['wf_positive_folds_pct'], ' %')} |")
        L.append("")
        top = rk["ranking"][:5]
        if top and not rk["qualified"]:
            L += ["Gründe (Top 5):", ""] + [f"- **{r['strategy']} {r['tf']}:** {'; '.join(r['reasons'][:4])}" for r in top] + [""]

    L += ["## Risikomodelle (Top 10 je Markt, konservativ)", "",
          f"Parameter wurden mit {_pct(rr['risk_per_trade'])} Risiko gewählt; die übrigen Risiken nur zum Vergleich.", "",
          "| Markt | TF | Strategie | Risiko | Val Trades / Netto € / DD % / Mindestlot-Ausfall % | Test Trades / Netto € / DD % |",
          "|---|---|---|---|---|---|"]
    for sym, rk in data["rankings"].items():
        for r in rk["ranking"][:10]:
            for risk, per in (r.get("risk_variants") or {}).items():
                v, t = per.get("validation") or {}, per.get("test") or {}
                L.append(f"| {sym} | {r['tf']} | {r['strategy']} | {risk} | {v.get('trades')} / {_v(v.get('net_profit_eur'))} / "
                         f"{_v(v.get('max_drawdown_pct'))} / {_v(v.get('min_lot_skip_pct'))} | "
                         f"{t.get('trades')} / {_v(t.get('net_profit_eur'))} / {_v(t.get('max_drawdown_pct'))} |")
    L.append("")

    L += ["## Alle Kombinationen (konservativ, Validierung und Test)", "",
          "| Markt | TF | Strategie | Parameter | Zeitraum | Trades | Netto € | PF | Treffer % | Erw. R | DD % | Sharpe |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for j in jobs:
        for r in _ok(j):
            ev = r["evaluation"][SELECTION_SCENARIO]
            for per in ("validation", "test"):
                L.append(f"| {j['symbol']} | {j['tf']} | {r['strategy']} | `{json.dumps(r['params'])}` | "
                         f"{'Val' if per == 'validation' else 'Test'} | {_row(ev[per])} |")
    L.append("")

    L += ["## Basis- vs. konservatives Kostenszenario (Test, Summe aller Strategien)", "",
          "| Markt | TF | Netto € basis | Netto € konservativ |", "|---|---|---|---|"]
    for j in jobs:
        b = sum((r["evaluation"]["basis"]["test"]["net_profit_eur"] or 0) for r in _ok(j))
        k = sum((r["evaluation"][SELECTION_SCENARIO]["test"]["net_profit_eur"] or 0) for r in _ok(j))
        L.append(f"| {j['symbol']} | {j['tf']} | {b:.2f} | {k:.2f} |")
    L.append("")

    if crosscheck:
        L += ["## Abgleich mit MT5-Rechenfunktionen (order_calc_profit / order_calc_margin)", "",
              "| Symbol | Volumen | Kursbewegung | Gewinn eigene / MT5 | Abw. % | Margin eigene / MT5 | Abw. % |",
              "|---|---|---|---|---|---|---|"]
        for sym, rows in crosscheck.items():
            for x in rows:
                L.append(f"| {sym} | {x['volume']} | {x['move']} | {x['profit_own']} / {x['profit_mt5']} | {x['profit_dev_pct']} | "
                         f"{x['margin_own']} / {x['margin_mt5']} | {x['margin_dev_pct']} |")
        L.append("")

    if data.get("errors"):
        L += ["## Fehler während des Laufs", "", "| Markt | TF | Strategie | Fehler |", "|---|---|---|---|"]
        L += [f"| {e['symbol']} | {e['tf']} | {e.get('strategy') or '–'} | {e['error']} |" for e in data["errors"]]
        L += ["", "Details (Traceback) in der JSON-Datei.", ""]

    L += ["## Qualitätsanforderungen", "", "| Kriterium | Wert |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in CRITERIA.items()]
    L.append("")
    return "\n".join(L)


def write(report_dir: Path, data: dict, crosscheck: dict | None = None) -> tuple[Path, Path, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(data["created_utc"]).strftime("%Y%m%d_%H%M%S")
    md = report_dir / f"phase3_bericht_{stamp}.md"
    js = report_dir / f"phase3_ergebnisse_{stamp}.json"
    latest = report_dir / "phase3_ergebnisse.json"
    payload = {**data, "mt5_crosscheck": crosscheck}
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    js.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    md.write_text(build_markdown(data, crosscheck), encoding="utf-8")
    return md, js, latest
