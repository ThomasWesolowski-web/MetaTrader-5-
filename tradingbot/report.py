"""Datenbericht (Markdown + JSON) aus Import- und Qualitätsergebnissen."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

GAP_LABELS = {"weekend": "Wochenende", "daily_break": "Tagespause", "holiday": "Feiertag",
              "low_activity": "ohne Ticks", "recurring_pause": "regelm. Pause", "data_gap": "**unerklärt**"}


def _fmt_time(s):
    return "–" if not s else str(s)[:16].replace("T", " ")


def build_markdown(update: dict | None, quality: dict, created: pd.Timestamp) -> str:
    specs = quality.get("specs") or (update or {}).get("doc") or {}
    L = ["# Phase 2 – Datenbericht", "",
         f"- **Erstellt:** {created:%Y-%m-%d %H:%M} UTC",
         "- **Trades ausgeführt:** keine (nur lesender Zugriff)", ""]
    acc = specs.get("account", {})
    if acc:
        L += [f"- **Konto:** {acc.get('trade_mode')} · Server {acc.get('server')} · {acc.get('currency')} · "
              f"Hebel 1:{acc.get('leverage'):g}",
              f"- **Terminal:** Build {specs.get('terminal', {}).get('build')} · "
              f"Max. Balken im Chart {specs.get('terminal', {}).get('maxbars')}", ""]
    ts = specs.get("time_scheme")
    if ts:
        L += ["## Zeitbasis", "",
              f"- Intern einheitlich **UTC**. MT5-Zeitstempel = Serverzeit; Umrechnung über Schema **`{ts['scheme']}`**",
              f"- Bestimmt per {ts['method']}, Sicherheit **{ts['confidence']}**, "
              f"{ts['events']} tägliche Goldpausen ausgewertet"
              + (f", Pause liegt bei {ts['break_hour_ny']:.2f} Uhr New York" if ts.get("break_hour_ny") is not None else ""),
              "- Aktuell gemessener Abstand Serverzeit–UTC: " +
              ("nicht messbar" if ts.get("live_offset_hours") is None else f"{ts['live_offset_hours']:+.2f} h")]
        if ts.get("scores"):
            L.append("- Konstanz der Goldpause je Kandidat: " +
                     ", ".join(f"`{k}` {v:.1%}" for k, v in sorted(ts["scores"].items(), key=lambda x: -x[1])))
        L += [f"- Hinweis: {n}" for n in ts.get("notes", [])] + [""]

    inst = specs.get("instruments", {})
    if inst:
        L += ["## Kontraktspezifikationen (aus MT5)", "",
              "| Symbol | Gewinnwährung | Kontraktgröße | Tickgröße | Tickwert (Konto) | Volumen min/Schritt/max | Modus | Marginsatz | Swap long/short |",
              "|---|---|---|---|---|---|---|---|---|"]
        for s, d in inst.items():
            L.append(f"| {s} | {d['currency_profit']} | {d['contract_size']:g} | {d['tick_size']:g} | {d['tick_value']:g} | "
                     f"{d['volume_min']:g} / {d['volume_step']:g} / {d['volume_max']:g} | {d['calc_mode']} | "
                     f"{d['margin_rate']:g} | {d['swap_long']:g} / {d['swap_short']:g} |")
        conv = specs.get("conversions", [])
        L += ["", f"Umrechnung in Kontowährung über: {', '.join(c['symbol'] for c in conv) or '–'}", ""]
    val = specs.get("validation", {})
    if val:
        L += ["## Abgleich der Berechnungen mit dem Terminal", "",
              "| Symbol | Gewinn 1 Lot bei +1 % Kurs (eigene / MT5) | Abw. | Tickwert (eigene / MT5) | Abw. | Margin 1 Lot (MT5) | eff. Hebel |",
              "|---|---|---|---|---|---|---|"]
        for s, v in val.items():
            if "error" in v and len(v) == 1:
                L.append(f"| {s} | Fehler: {v['error']} | | | | | |")
                continue
            p, t, m = v.get("profit_1lot_plus1pct", {}), v.get("tick_value", {}), v.get("margin_1lot", {})
            L.append(f"| {s} | {p.get('eigene')} / {p.get('mt5')} | {p.get('abweichung_pct')} % | "
                     f"{t.get('eigene')} / {t.get('mt5')} | {t.get('abweichung_pct')} % | {m.get('mt5')} | "
                     f"1:{m.get('effektiver_hebel')} |")
        L.append("")

    if update:
        L += ["## Import dieses Laufs", "", "| Symbol | TF | neu | gesamt | erste Kerze (UTC) | letzte Kerze (UTC) | Abfragen | Historie vollständig |",
              "|---|---|---|---|---|---|---|---|"]
        for r in update["imports"]:
            L.append(f"| {r.symbol} | {r.tf} | {r.new_rows} | {r.rows_total} | {_fmt_time(r.first_utc)} | "
                     f"{_fmt_time(r.last_utc)} | {r.requests} | {'ja' if r.history_exhausted else 'nein'} |"
                     + (f" Fehler: {r.errors}" if r.errors else ""))
        L.append("")

    res = quality.get("results", [])
    if res:
        L += ["## Datenbestand und Qualität", "",
              "| Symbol | TF | Kerzen | von (UTC) | dicht ab | bis (UTC) | Dichte | größte Lücke (Tage) | Dupl. | ungültig | Lücken (Kategorien) | unerklärt (h Handelszeit) | Abdeckung | Ausreißer |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for q in res:
            s = q.summary
            if not s.get("rows"):
                continue
            gaps = ", ".join(f"{GAP_LABELS.get(k, k)} {v}" for k, v in sorted(s["gaps"].items())) or "–"
            invalid = s["invalid_ohlc"] + s["nonpositive_prices"] + s["nan_rows"] + s["negative_spread"]
            anom = ", ".join(f"{k} {v}" for k, v in s["anomalies"].items()) or "0"
            L.append(f"| {q.symbol} | {q.tf} | {s['rows']} | {_fmt_time(s['first_utc'])} | "
                     f"{_fmt_time(s.get('dense_from_utc'))} | {_fmt_time(s['last_utc'])} | "
                     f"{s.get('bar_density_pct')} % | {s.get('largest_gap_days')} | {s['duplicates']} | {invalid} | "
                     f"{gaps} | {s['data_gap_open_hours']} | {s['coverage_pct']} % | {anom} |")
        L.append("")
        years = sorted({y for q in res for y in (q.summary.get("density_by_year_pct") or {})})
        if years:
            L += ["### Datendichte je Jahr (% der laut Handelskalender möglichen Kerzen)", "",
                  "| Symbol | TF | " + " | ".join(years) + " |", "|---|---|" + "---|" * len(years)]
            for q in res:
                dy = q.summary.get("density_by_year_pct") or {}
                if q.tf in ("M1", "M5", "H1"):
                    L.append(f"| {q.symbol} | {q.tf} | " + " | ".join(str(dy.get(y, "")) for y in years) + " |")
            L.append("")
        for q in res:
            ug = q.unexplained_gaps()
            if ug is not None and not ug.empty:
                top = ug.sort_values("open_hours", ascending=False).head(10)
                L += [f"### Größte unerklärte Lücken {q.symbol} {q.tf}", "", "| Beginn (UTC) | Ende (UTC) | Dauer h | davon Handelszeit h |", "|---|---|---|---|"]
                L += [f"| {r.start:%Y-%m-%d %H:%M} | {r.end:%Y-%m-%d %H:%M} | {r.hours} | {r.open_hours} |" for r in top.itertuples()]
                L.append("")
            if not q.anomalies.empty:
                top = q.anomalies.sort_values("score", ascending=False).head(5)
                L += [f"### Auffällige Kerzen {q.symbol} {q.tf} (Top 5 von {len(q.anomalies)})", "",
                      "| Zeit (UTC) | Art | Wert | Score | Schluss | Schluss davor |", "|---|---|---|---|---|---|"]
                L += [f"| {pd.Timestamp(r.time):%Y-%m-%d %H:%M} | {r.kind} | {r.value} | {r.score} | {r.close} | {r.prev_close} |"
                      for r in top.itertuples()]
                L.append("")
    cons = {k: v for k, v in quality.get("consistency", {}).items() if v}
    if cons:
        L += ["## Konsistenz höherer Zeitrahmen mit M1", "",
              "| Reihe | verglichene Kerzen | Schluss gleich | High/Low gleich | fehlt in M1 |", "|---|---|---|---|---|"]
        L += [f"| {k} | {v.get('compared')} | {v.get('close_match_pct')} % | {v.get('high_low_match_pct')} % | {v.get('missing_in_m1')} |"
              for k, v in cons.items()]
        L.append("")
    if specs.get("warnings"):
        L += ["## Warnungen", ""] + [f"- {w}" for w in specs["warnings"]] + [""]
    L += ["## Legende", "",
          "- **Lückenkategorien:** Wochenende/Tagespause/Feiertag laut Handelskalender (Gold: CME, New Yorker Zeit), "
          "„ohne Ticks“ = kurze Lücke in M1/M5, „regelm. Pause“ = wiederholt zur gleichen Serverzeit "
          "(z. B. Broker-Wartung), **unerklärt** = möglicher Datenfehler.",
          "- **Abdeckung** = Anteil der Handelszeit ohne unerklärte Lücken. **Dichte** = vorhandene Kerzen / laut "
          "Kalender mögliche Kerzen (M1 typischerweise < 100 %, weil Minuten ohne Ticks keine Kerze haben). "
          "**dicht ab** = erster Monat, ab dem die monatliche Dichte durchgehend mindestens 60 % des Medians der "
          "letzten 24 Monate erreicht (Backtests beginnen frühestens hier).",
          "- **Ausreißer:** outlier = extreme Rendite, spike = Ausschlag mit sofortiger Rückkehr (Fehltick-Verdacht), "
          "range_outlier = extreme Kerzenspanne. Markiert, nicht verändert.",
          "- Fehlende Kurse werden **nicht** aufgefüllt oder erfunden.", ""]
    return "\n".join(L)


def write_report(report_dir: Path, update: dict | None, quality: dict) -> tuple[Path, Path]:
    created = pd.Timestamp.now(tz="UTC")
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%d_%H%M%S")
    md = report_dir / f"phase2_datenbericht_{stamp}.md"
    js = report_dir / f"phase2_datenbericht_{stamp}.json"
    md.write_text(build_markdown(update, quality, created), encoding="utf-8")
    payload = {
        "created_utc": created.isoformat(),
        "specs": quality.get("specs"),
        "imports": [vars(r) for r in (update or {}).get("imports", [])],
        "quality": [{"symbol": q.symbol, "tf": q.tf, **q.summary,
                     "unexplained_gaps": q.unexplained_gaps().astype(str).to_dict("records") if not q.gaps.empty else []}
                    for q in quality.get("results", [])],
        "consistency": quality.get("consistency"),
    }
    js.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return md, js
