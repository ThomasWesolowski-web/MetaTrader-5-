"""Daten für das Dashboard (Phase 4) – ohne Streamlit, damit alles testbar ist.

Quellen (nur lesen, nichts wird neu berechnet oder geschätzt):
  Backtest           reports/phase3_ergebnisse.json (vollständig) oder docs/phase3_zusammenfassung.json (kompakt)
  Datenqualität      neuester reports/phase2_datenbericht_*.json
  Aktualität         data/market/<Symbol>/_meta.json (Importstatus je Zeitrahmen)
  Live-Beobachtung   data/paper/*.json
  Kontrakt/Mindestlot data/paper/kontraktpruefung.json (live) oder Berechnung aus data/specs.json + gespeicherten Kursen

Fehlt eine Kennzahl in der Quelle (z. B. Drawdown in € in Ergebnissen vor Phase 4), wird None geliefert und im
Dashboard als „nicht verfügbar“ angezeigt.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest.account import RiskRules
from .backtest.costs import scenarios_for
from .instruments import CurrencyConverter, InstrumentSpec, conversion_sources
from .paper.store import PaperStore
from .risk_check import check_symbol
from .storage import MarketStore
from .timeframes import seconds

PERIODS = {"train": "Training", "validation": "Validierung", "test": "Test"}
TF_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mtime(path) -> float:
    """Änderungszeit als Cache-Schlüssel (0, wenn nicht vorhanden)."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------------- Backtest (Phase 3)
def phase3_source(report_dir: Path, project_root: Path) -> tuple[Path | None, str]:
    full = Path(report_dir) / "phase3_ergebnisse.json"
    if full.exists():
        return full, "vollständig (reports/phase3_ergebnisse.json)"
    compact = Path(project_root) / "docs" / "phase3_zusammenfassung.json"
    if compact.exists():
        return compact, "kompakt (docs/phase3_zusammenfassung.json)"
    return None, "keine Phase-3-Ergebnisse gefunden"


def load_phase3(path: Path | None) -> dict | None:
    return None if path is None else _read_json(path)


def completeness(data: dict) -> dict:
    created = data.get("created_utc")
    return {"erstellt_utc": f"erstellt {created[:19].replace('T', ' ')} UTC" if created else (data.get("source") or "–"),
            "einheiten_erwartet": data.get("units_expected"), "einheiten_fertig": data.get("units_completed"),
            "fehlend": data.get("units_missing"), "fehler": data.get("errors") or [],
            "abgleich_lauf2": data.get("reference_comparison"), "kostenszenario_auswahl": data.get("selection_scenario")}


def ranking_table(data: dict, symbol: str) -> pd.DataFrame:
    rk = (data.get("rankings") or {}).get(symbol) or {}
    rows = []
    for r in rk.get("ranking", []):
        v, t = r.get("validation") or {}, r.get("test") or {}
        rows.append({"Rang": r.get("rank"), "TF": r["tf"], "Strategie": r["strategy"],
                     "Parameter": json.dumps(r["params"], ensure_ascii=False), "qualifiziert": r["qualified"],
                     "Score": r["score"],
                     "Val Trades": v.get("trades"), "Val Netto €": v.get("net_profit_eur"), "Val PF": v.get("profit_factor"),
                     "Val DD %": v.get("max_drawdown_pct"), "Val DD €": v.get("max_drawdown_eur"),
                     "Test Trades": t.get("trades"), "Test Netto €": t.get("net_profit_eur"), "Test PF": t.get("profit_factor"),
                     "Test DD %": t.get("max_drawdown_pct"), "Test DD €": t.get("max_drawdown_eur"),
                     "WF positive Abschnitte %": r.get("wf_positive_folds_pct"),
                     "Gründe": "; ".join(r.get("reasons", []))})
    return pd.DataFrame(rows)


def verdict(data: dict, symbol: str) -> str | None:
    return ((data.get("rankings") or {}).get(symbol) or {}).get("verdict")


def find_result(data: dict, symbol: str, tf: str, strategy: str) -> tuple[dict | None, dict | None]:
    for j in data.get("jobs", []):
        if j.get("symbol") == symbol and j.get("tf") == tf:
            for r in j.get("results", []):
                if r.get("strategy") == strategy and "error" not in r:
                    return j, r
            return j, None
    return None, None


def metrics_table(result: dict) -> pd.DataFrame:
    rows = []
    for scen, per in (result.get("evaluation") or {}).items():
        for p, m in per.items():
            costs = m.get("costs_eur") or {}
            rows.append({"Kostenszenario": scen, "Zeitraum": PERIODS.get(p, p), "Trades": m.get("trades"),
                         "Netto €": m.get("net_profit_eur"), "Rendite %": m.get("return_pct"),
                         "Profitfaktor": m.get("profit_factor"), "Trefferquote %": m.get("win_rate_pct"),
                         "Erwartungswert R": m.get("expectancy_r"), "Max. DD %": m.get("max_drawdown_pct"),
                         "Max. DD €": m.get("max_drawdown_eur"), "Spread € (Schätzung)": costs.get("spread_est"),
                         "Kommission €": costs.get("commission"), "Swap €": costs.get("swap"),
                         "Mindestlot nicht ausführbar %": m.get("min_lot_skip_pct")})
    return pd.DataFrame(rows)


def yearly_table(result: dict, scenario: str) -> pd.DataFrame:
    rows = []
    for p, m in ((result.get("evaluation") or {}).get(scenario) or {}).items():
        for y, v in (m.get("yearly") or {}).items():
            rows.append({"Jahr": y, "Zeitraum": PERIODS.get(p, p), "Ergebnis % (Konto)": v})
    return pd.DataFrame(rows)


def folds_table(result: dict) -> pd.DataFrame:
    wf = result.get("walk_forward") or {}
    return pd.DataFrame([{"Out-of-Sample von": f["oos"][0], "bis": f["oos"][1],
                          "Parameter": json.dumps(f.get("params"), ensure_ascii=False) if f.get("params") else "–",
                          "Trades": f.get("trades"), "Netto €": f.get("net_eur")} for f in wf.get("folds", [])])


def risk_variants_table(result: dict) -> pd.DataFrame:
    rows = []
    for rv, per in (result.get("risk_variants") or {}).items():
        for p, m in per.items():
            rows.append({"Risiko je Trade": rv, "Zeitraum": PERIODS.get(p, p), "Trades": m.get("trades"),
                         "Netto €": m.get("net_profit_eur"), "Profitfaktor": m.get("profit_factor"),
                         "Max. DD %": m.get("max_drawdown_pct"), "Max. DD €": m.get("max_drawdown_eur"),
                         "Mindestlot nicht ausführbar %": m.get("min_lot_skip_pct")})
    return pd.DataFrame(rows)


def cost_scenarios_by_tf(data: dict, period: str = "test") -> pd.DataFrame:
    rows = []
    for j in data.get("jobs", []):
        if "results" not in j:
            continue
        sums = {}
        costs = {}
        for r in j["results"]:
            if "error" in r:
                continue
            for scen, per in r["evaluation"].items():
                m = per.get(period) or {}
                sums[scen] = sums.get(scen, 0.0) + (m.get("net_profit_eur") or 0.0)
                c = m.get("costs_eur") or {}
                for k in ("spread_est", "commission", "swap"):
                    costs[(scen, k)] = costs.get((scen, k), 0.0) + (c.get(k) or 0.0)
        row = {"Markt": j["symbol"], "TF": j["tf"]}
        for scen in sorted(sums):
            row[f"Netto € {scen}"] = round(sums[scen], 2)
            row[f"Spread € {scen}"] = round(costs.get((scen, "spread_est"), 0.0), 2)
            row[f"Kommission € {scen}"] = round(costs.get((scen, "commission"), 0.0), 2)
            row[f"Swap € {scen}"] = round(costs.get((scen, "swap"), 0.0), 2)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["_o"] = df["TF"].map({t: i for i, t in enumerate(TF_ORDER)})
        df = df.sort_values(["Markt", "_o"]).drop(columns="_o").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------- Daten: Qualität und Aktualität
def latest_quality_report(report_dir: Path) -> Path | None:
    files = sorted(Path(report_dir).glob("phase2_datenbericht_*.json"))
    return files[-1] if files else None


def quality_table(report: dict | None) -> pd.DataFrame:
    rows = []
    for q in (report or {}).get("quality", []):
        if not q.get("rows"):
            continue
        invalid = sum(q.get(k, 0) or 0 for k in ("invalid_ohlc", "nonpositive_prices", "nan_rows", "negative_spread"))
        rows.append({"Symbol": q["symbol"], "TF": q["tf"], "Kerzen": q["rows"], "von (UTC)": str(q.get("first_utc"))[:16],
                     "dicht ab": str(q.get("dense_from_utc") or "–")[:16], "bis (UTC)": str(q.get("last_utc"))[:16],
                     "Dichte %": q.get("bar_density_pct"), "größte Lücke (Tage)": q.get("largest_gap_days"),
                     "Duplikate": q.get("duplicates"), "ungültig": invalid,
                     "unerklärte Lücken": (q.get("gaps") or {}).get("data_gap", 0),
                     "unerklärt (h Handelszeit)": q.get("data_gap_open_hours"), "Abdeckung %": q.get("coverage_pct"),
                     "Ausreißer": sum((q.get("anomalies") or {}).values())})
    return pd.DataFrame(rows)


def freshness_table(data_root: Path, now_utc: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aktualität je Symbol und Zeitrahmen aus dem Importstatus (_meta.json) – liest keine Kursdaten."""
    now_utc = now_utc or pd.Timestamp.now(tz="UTC")
    store = MarketStore(data_root)
    rows = []
    for sym in store.symbols():
        meta = store.read_meta(sym).get("timeframes", {})
        for tf in sorted(meta, key=lambda x: TF_ORDER.index(x) if x in TF_ORDER else 99):
            m = meta[tf]
            last = pd.Timestamp(m["last_utc"]) if m.get("last_utc") else None
            end = None if last is None else last + pd.Timedelta(seconds=seconds(tf))
            age_h = None if end is None else round((now_utc - end).total_seconds() / 3600, 1)
            rows.append({"Symbol": sym, "TF": tf, "Kerzen": m.get("rows"), "erste Kerze (UTC)": str(m.get("first_utc"))[:16],
                         "letzte Kerze (UTC)": str(m.get("last_utc"))[:16], "Alter (h)": age_h,
                         "letzte Aktualisierung (UTC)": str(m.get("last_update_utc"))[:16],
                         "neu beim letzten Import": m.get("last_new_rows")})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- Live-Beobachtung
def load_paper(data_root: Path) -> dict:
    ps = PaperStore(data_root)
    return {"status": ps.read("status.json"), "trades": ps.read("trades.json"),
            "contract": ps.read("kontraktpruefung.json"), "events": ps.events(limit=500)}


def paper_candidates_table(trades_doc: dict | None) -> pd.DataFrame:
    rows = []
    for key, c in ((trades_doc or {}).get("kandidaten") or {}).items():
        ml = c.get("mindestlot") or {}
        acc = c.get("referenzkonto") or {}
        rows.append({"Kandidat": key, "TF": c["timeframe"], "Strategie": c["strategy"],
                     "Parameter": json.dumps(c["params"], ensure_ascii=False),
                     "Beobachtung seit (UTC)": str(c["beobachtung_seit_utc"])[:16],
                     "letzte Kerze (UTC)": str(c.get("letzte_kerze_utc") or "–")[:16],
                     "Signale": len(c.get("signale", [])), "nicht gehandelt (verspätet/Abweichung)": len(c.get("abgelehnte_signale", [])),
                     "offen": sum(t["status"] == "offen" for t in c.get("trades", [])),
                     "geschlossen": ml.get("trades", 0), "Netto € je 0,01 Lot": ml.get("netto_eur"),
                     "PF (0,01 Lot)": ml.get("profitfaktor"), "Max. DD € (0,01 Lot)": ml.get("max_drawdown_eur"),
                     "Max. DD % (0,01 Lot, auf 1.000 €)": ml.get("max_drawdown_pct_referenz"),
                     "Referenzkonto Trades": acc.get("trades"), "Referenzkonto Netto €": acc.get("net_profit_eur"),
                     "Referenzkonto DD %": acc.get("max_drawdown_pct"), "Referenzkonto DD €": acc.get("max_drawdown_eur"),
                     "Datenlücken": c.get("luecken")})
    return pd.DataFrame(rows)


def paper_trades_table(trades_doc: dict | None) -> pd.DataFrame:
    rows = []
    for key, c in ((trades_doc or {}).get("kandidaten") or {}).items():
        for t in c.get("trades", []):
            ref = t.get("referenzkonto") or {}
            rows.append({"Kandidat": key, "Status": t["status"], "Richtung": t["richtung"],
                         "Signal (UTC)": str(t["signal_kerze_utc"])[:16], "Einstieg (UTC)": str(t["einstieg_utc"])[:16],
                         "Einstieg": t["einstieg"], "Stop": t["stop"], "Ziel": t.get("ziel"),
                         "Ausstieg (UTC)": str(t.get("ausstieg_utc") or "–")[:16], "Ausstieg": t.get("ausstieg"),
                         "Grund": t.get("ausstiegsgrund"), "Risiko € (0,01)": t.get("risiko_eur"),
                         "innerhalb 0,25 %": t.get("innerhalb_risikogrenze"),
                         "Brutto €": t.get("brutto_eur"), "Spread €": t.get("spread_eur"),
                         "Slippage € (Annahme)": t.get("slippage_eur"), "Kommission € (Annahme)": t.get("kommission_eur"),
                         "Swap €": t.get("swap_eur"), "Netto €": t.get("netto_eur"), "R netto": t.get("r_netto"),
                         "unrealisiert € (offen)": t.get("unrealisiert_brutto_eur"),
                         "Referenzkonto": ("ausgeführt" if ref.get("ausgefuehrt") else ref.get("grund", "–")),
                         "Ausstieg verspätet erkannt": t.get("ausstieg_verspaetet_erkannt")})
    return pd.DataFrame(rows)


def paper_signals_table(trades_doc: dict | None) -> pd.DataFrame:
    rows = []
    for key, c in ((trades_doc or {}).get("kandidaten") or {}).items():
        for s in c.get("signale", []):
            rows.append({"Kandidat": key, "Signal-Kerze (UTC)": str(s["signal_kerze_utc"])[:16],
                         "Richtung": s["richtung"], "Stop": s["stop"], "Ziel": s.get("ziel"),
                         "erkannt (UTC)": str(s["erkannt_utc"])[:19], "Verzögerung s": s.get("verzoegerung_s"),
                         "Status": s.get("status")})
        for s in c.get("abgelehnte_signale", []):
            rows.append({"Kandidat": key, "Signal-Kerze (UTC)": str(s["signal_kerze_utc"])[:16],
                         "Richtung": s["richtung"], "Stop": s["stop"], "Ziel": s.get("ziel"),
                         "erkannt (UTC)": str(s["erkannt_utc"])[:19], "Verzögerung s": s.get("verzoegerung_s"),
                         "Status": "nicht gehandelt: " + s.get("grund", "")})
    return pd.DataFrame(rows)


def paper_equity(trades_doc: dict | None) -> pd.DataFrame:
    """Kumuliertes Nettoergebnis je 0,01 Lot über die Ausstiegszeit (nur abgeschlossene Trades)."""
    rows = []
    for key, c in ((trades_doc or {}).get("kandidaten") or {}).items():
        closed = sorted((t for t in c.get("trades", []) if t["status"] == "geschlossen" and t.get("netto_eur") is not None),
                        key=lambda t: t["ausstieg_utc"])
        cum = 0.0
        for t in closed:
            cum += t["netto_eur"]
            rows.append({"Zeit": pd.Timestamp(t["ausstieg_utc"]), "Kandidat": key, "kumuliert €": round(cum, 2)})
    return pd.DataFrame(rows)


def events_table(events: list[dict]) -> pd.DataFrame:
    rows = []
    for e in reversed(events):
        rest = {k: v for k, v in e.items() if k not in ("utc", "art", "kandidat", "symbol")}
        rows.append({"Zeit (UTC)": str(e.get("utc", ""))[:19], "Ereignis": e.get("art"),
                     "Kandidat/Symbol": e.get("kandidat") or e.get("symbol") or "",
                     "Details": json.dumps(rest, ensure_ascii=False, default=str)[:300]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- Kontrakt und Mindestlot
def contract_rows(doc: dict | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec_rows, tf_rows = [], []
    for sym, r in ((doc or {}).get("symbols") or {}).items():
        if "error" in r:
            spec_rows.append({"Symbol": sym, "Fehler": r["error"]})
            continue
        s = r["spec"]
        spec_rows.append({"Symbol": sym, "Kontraktgröße": s["contract_size"],
                          "Volumen min / Schritt / max": f"{s['volume_min']:g} / {s['volume_step']:g} / {s['volume_max']:g}",
                          "Tickgröße": s["tick_size"], "Modus": s["calc_mode"], "Kurs": r["price"],
                          f"{s['currency_profit']}→EUR": r["profit_ccy_to_eur"],
                          "€ je 1,0 Kursänderung bei Mindestlot": r["value_per_price_unit_min_lot_eur"],
                          "Risiko je Trade €": r["risk_eur"], "max. Stop bei Mindestlot": r["max_stop_distance"],
                          "max. Stop % vom Kurs": r["max_stop_pct_of_price"],
                          "Mindest-Stopabstand Broker": r["broker_min_stop_distance"],
                          "Kommission Mindestlot € (Annahme)": (r.get("commission_assumption") or {}).get("eur_min_lot"),
                          "Abweichungen zu specs.json": "keine" if not r.get("spec_differences")
                          else json.dumps(r["spec_differences"], ensure_ascii=False, default=str)})
        for tf, v in r.get("by_timeframe", {}).items():
            tf_rows.append({"Symbol": sym, "TF": tf, "ATR(14)": v.get("atr14"),
                            "max. Stop in ATR": v.get("max_stop_in_atr"),
                            "Risiko € bei 1,5 ATR (0,01 Lot)": v.get("risk_eur_min_lot_1_5atr"),
                            "1,5 ATR innerhalb 0,25 %": v.get("within_limit_1_5atr"),
                            "Risiko € bei 3 ATR (0,01 Lot)": v.get("risk_eur_min_lot_3atr"),
                            "3 ATR innerhalb 0,25 %": v.get("within_limit_3atr")})
    return pd.DataFrame(spec_rows), pd.DataFrame(tf_rows)


def _recent_start(bounds: dict, days: int) -> pd.Timestamp:
    """Beginn des Ladefensters: `days` Tage vor der letzten gespeicherten Kerze (Serverzeit, ±3 h genügen hier)."""
    return pd.Timestamp(bounds["last_server"], unit="s", tz="UTC") - pd.Timedelta(days=days + 1)


def offline_contract_check(data_root: Path, symbols=("XAUUSD", "BTCUSD"), tfs=("M1", "M5", "M15", "M30", "H1"),
                           days: int = 30) -> dict | None:
    """Wie die Live-Prüfung, aber aus data/specs.json und den zuletzt gespeicherten Kursen (letzte `days` Tage)."""
    store = MarketStore(data_root)
    doc = store.read_json("specs.json", {}) or {}
    if not doc.get("instruments"):
        return None
    acc_ccy = (doc.get("account") or {}).get("currency", "EUR")
    rates, latest = {}, {}
    for src in conversion_sources(doc):
        for tf in ("M5", "H1", "D1"):
            b = store.bounds(src["symbol"], tf)
            if b is None:
                continue
            df = store.load(src["symbol"], tf, start=_recent_start(b, 3), columns=["close"]).tail(1)
            if not df.empty:
                t_end = pd.DatetimeIndex(df["time"]).as_unit("ns").asi8 + seconds(tf) * 10**9
                rates[src["symbol"]] = (t_end, df["close"].to_numpy(dtype=float), src["base"], src["quote"])
                latest[src["symbol"]] = str(df["time"].iloc[-1])
                break
    conv = CurrencyConverter(rates)
    out = {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
           "source": f"gespeicherte Spezifikationen (data/specs.json vom {str(doc.get('updated_utc', '?'))[:16]}) "
                     f"und zuletzt gespeicherte Kurse", "account_currency": acc_ccy, "symbols": {}}
    for sym in symbols:
        d = doc["instruments"].get(sym)
        if not d:
            continue
        spec = InstrumentSpec.from_dict(d)
        atr, price = {}, None
        for tf in tfs:
            b = store.bounds(sym, tf)
            if b is None:
                atr[tf] = None
                continue
            df = store.load(sym, tf, start=_recent_start(b, days), columns=["high", "low", "close"]).tail(600)
            if len(df) < 100:
                atr[tf] = None
                continue
            a = ind.atr(df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float), 14)[-500:]
            a = a[np.isfinite(a)]
            atr[tf] = float(np.median(a)) if len(a) else None
            price = float(df["close"].iloc[-1]) if price is None or tf == "M1" else price
        if price is None:
            out["symbols"][sym] = {"error": "keine gespeicherten Kurse"}
            continue
        try:
            rate = float(conv.rate(spec.currency_profit, acc_ccy)[0])
        except KeyError:
            out["symbols"][sym] = {"error": f"kein Umrechnungskurs {spec.currency_profit}->{acc_ccy}"}
            continue
        r = check_symbol(spec, rate, price, scenarios_for(sym)["konservativ"], atr, RiskRules(), None)
        r["atr_basis"] = "Median ATR(14) der letzten 500 gespeicherten Kerzen"
        out["symbols"][sym] = r
    return out
