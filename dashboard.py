"""Phase 4 – Dashboard (nur Anzeige, keine Orders).

Start (Windows, im Projektordner, .venv aktiv):
    streamlit run dashboard.py

Zeigt Backtest-Ergebnisse (Phase 3) und die Live-Beobachtung (Paper-Trading) in getrennten Bereichen, dazu
Datenqualität, Aktualität der Daten sowie Kontraktspezifikationen und Stop-Grenzen bei Mindestlot.
Es liest nur vorhandene Dateien; Kursdaten werden nur für die Kontraktprüfung (letzte Kerzen) gelesen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from tradingbot import dashboard_data as dd
from tradingbot.backtest.account import RiskRules
from tradingbot.config import load_config

ROOT = Path(__file__).resolve().parent
cfg = load_config(os.environ.get("TRADINGBOT_CONFIG") or ROOT / "config.toml")
RULES = RiskRules()

st.set_page_config(page_title="Trading Gold & Bitcoin", layout="wide")


@st.cache_data(show_spinner=False)
def phase3(path: str | None, _mtime: float):
    return dd.load_phase3(Path(path) if path else None)


@st.cache_data(show_spinner=False)
def quality(path: str | None, _mtime: float):
    return dd._read_json(Path(path)) if path else None


@st.cache_data(show_spinner=False, ttl=300)
def offline_contract(root: str, _mtime: float):
    return dd.offline_contract_check(Path(root))


def table(df: pd.DataFrame, empty: str = "Keine Daten vorhanden.", **kw):
    if df is None or df.empty:
        st.info(empty)
    else:
        st.dataframe(df, width="stretch", hide_index=True, **kw)


def na_note(df: pd.DataFrame, cols: list[str]):
    present = [c for c in cols if c in df.columns]
    if present and df[present].isna().all().all():
        st.caption("Drawdown in € ist in diesen Ergebnissen nicht gespeichert (Backtest vor Phase 4) und wird nicht "
                   "geschätzt. Er erscheint nach dem nächsten Lauf von `python backtest_ausfuehren.py`.")


st.title("Trading-Software Gold & Bitcoin")
def de(x: float, nd: int = 2) -> str:
    """Zahl im deutschen Format (1.000,00)."""
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


st.caption(f"Nur Anzeige – es werden **keine Orders** gesendet, kein Demo- oder Echtgeldhandel. "
           f"Risikoregeln unverändert: {de(RULES.initial, 0)} € Referenzkapital, {de(100 * RULES.risk_per_trade)} % "
           f"je Trade, {de(100 * RULES.max_daily_loss, 0)} % Tagesverlust, {de(100 * RULES.max_drawdown, 0)} % "
           f"Drawdown-Grenze, max. {RULES.max_positions} Positionen.")

p3_path, p3_kind = dd.phase3_source(cfg.report_dir, ROOT)
data = phase3(str(p3_path) if p3_path else None, dd.mtime(p3_path) if p3_path else 0.0)
paper = dd.load_paper(cfg.data_root)
now = pd.Timestamp.now(tz="UTC")

tab_over, tab_bt, tab_live, tab_data, tab_contract = st.tabs(
    ["Übersicht", "Backtest (Phase 3)", "Live-Beobachtung (Paper-Trading)", "Daten", "Kontrakt & Mindestlot"])

# ---------------------------------------------------------------------- Übersicht
with tab_over:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Backtest (historische Simulation)")
        if data is None:
            st.warning("Keine Phase-3-Ergebnisse gefunden.")
        else:
            comp = dd.completeness(data)
            st.write(f"Quelle: {p3_kind}")
            if comp["einheiten_erwartet"]:
                st.write(f"Testeinheiten: **{comp['einheiten_fertig']} von {comp['einheiten_erwartet']}** abgeschlossen")
            for sym in (data.get("rankings") or {}):
                st.write(f"**{sym}:** {dd.verdict(data, sym)}")
    with c2:
        st.subheader("Live-Beobachtung (hypothetisch)")
        status = paper["status"]
        if not status:
            st.info("Noch keine Beobachtungsdaten. Start: `python beobachtung_ausfuehren.py`")
        else:
            age_min = (now - pd.Timestamp(status["updated_utc"])).total_seconds() / 60
            st.write(f"Letzter Durchlauf: {status['updated_utc'][:19]} UTC (vor {age_min:.0f} min)")
            if age_min > 10:
                st.warning("Die Beobachtung läuft derzeit nicht – in dieser Zeit entstehen keine Beobachtungsdaten.")
            for sym, m in status["maerkte"].items():
                st.write(f"{sym}: {m['zustand']}")
            for key, k in status["kandidaten"].items():
                st.write(f"{key}: {k['signale']} Signale, {k['offen']} offen, {k['geschlossen']} geschlossen")
    fr = dd.freshness_table(cfg.data_root, now)
    if not fr.empty:
        st.subheader("Datenstand")
        st.write("Älteste letzte Kerze je Symbol (Stunden seit Kerzenende):")
        table(fr.groupby("Symbol", as_index=False)["Alter (h)"].max())

# ---------------------------------------------------------------------- Backtest
with tab_bt:
    st.info("**Historische Simulation (Phase 3)** – keine Live-Ergebnisse. Kosten im Szenario „konservativ“, "
            "0,25 % Risiko je Trade, 1.000 € Startkapital.")
    if data is None:
        st.warning("Keine Phase-3-Ergebnisse gefunden. Erzeugen mit `python backtest_ausfuehren.py`.")
    else:
        comp = dd.completeness(data)
        st.write(f"Quelle: {p3_kind} · {comp['erstellt_utc']}")
        if comp["fehler"]:
            st.error(f"{len(comp['fehler'])} Testeinheiten mit Fehler: " +
                     ", ".join(f"{e['symbol']} {e['tf']} {e.get('strategy', '')}" for e in comp["fehler"]))
        if comp["fehlend"]:
            st.warning(f"Fehlende Testeinheiten: {comp['fehlend']}")
        markets = list((data.get("rankings") or {}).keys())
        sym = st.selectbox("Markt", markets, key="bt_sym")
        st.write(f"**Ergebnis:** {dd.verdict(data, sym)}")
        rk = dd.ranking_table(data, sym)
        tfs = st.multiselect("Zeitrahmen", sorted(rk["TF"].unique(), key=dd.TF_ORDER.index), key="bt_tfs")
        only_q = st.checkbox("nur qualifizierte", key="bt_q")
        view = rk[rk["TF"].isin(tfs)] if tfs else rk
        view = view[view["qualifiziert"]] if only_q else view
        st.subheader("Rangliste")
        table(view)
        st.caption("PF 99 = Profitfaktor ohne Verlusttrades (nicht berechenbar). Leere Werte = nicht vorhanden.")
        na_note(rk, ["Val DD €", "Test DD €"])

        st.subheader("Einzelne Kombination")
        c1, c2 = st.columns(2)
        tf_sel = c1.selectbox("Zeitrahmen", sorted(rk["TF"].unique(), key=dd.TF_ORDER.index), key="bt_tf")
        strat = c2.selectbox("Strategie", sorted(rk[rk["TF"] == tf_sel]["Strategie"].unique()), key="bt_strat")
        job, res = dd.find_result(data, sym, tf_sel, strat)
        if res is None:
            st.info("Für diese Kombination liegt kein Ergebnis vor.")
        else:
            st.write(f"Parameter (nur im Training gewählt): `{res['params']}` · Signale: {res.get('signals')}")
            if res.get("rules"):
                st.caption(res["rules"])
            if job and job.get("periods"):
                p = job["periods"]
                st.caption(f"Training {p['start']} – {p['train_end']} · Validierung bis {p['val_end']} · Test bis {p['end']}")
            mt = dd.metrics_table(res)
            table(mt)
            na_note(mt, ["Max. DD €"])
            st.caption("Spread-Kosten sind aus den historischen Kerzen-Spreads geschätzt; Kommission und Slippage sind "
                       "Annahmen (Kostenszenario), da sie für das Konto historisch nicht bekannt sind.")
            folds = dd.folds_table(res)
            if not folds.empty:
                st.write("Walk-Forward: Ergebnis je Out-of-Sample-Abschnitt (Training)")
                st.bar_chart(folds.dropna(subset=["Netto €"]), x="Out-of-Sample von", y="Netto €")
                table(folds)
            scen = st.radio("Kostenszenario für Jahresergebnisse", ["konservativ", "basis"], horizontal=True, key="bt_scen")
            table(dd.yearly_table(res, scen))
            st.write("Risikovarianten (nur Information – freigegeben ist 0,25 %)")
            table(dd.risk_variants_table(res))
        st.subheader("Kosten und Ergebnis je Zeitrahmen (Test, Summe aller Strategien)")
        table(dd.cost_scenarios_by_tf(data))

# ---------------------------------------------------------------------- Live-Beobachtung
with tab_live:
    st.warning("**Live-Beobachtung (Paper-Trading)** – hypothetische Ein- und Ausstiege mit neuen Live-Kursen. "
               "Es werden keine Orders gesendet. Diese Zahlen sind von den Backtest-Ergebnissen getrennt.")
    trades_doc = paper["trades"]
    status = paper["status"]
    if not trades_doc:
        st.info("Noch keine Beobachtungsdaten. Start auf dem PC: `python beobachtung_ausfuehren.py`")
    else:
        st.write(f"Stand: {trades_doc['updated_utc'][:19]} UTC")
        if status:
            st.write(" · ".join(f"{s}: {m['zustand']} (Tick {str(m['tick_utc'])[:19]})" for s, m in status["maerkte"].items()))
        st.subheader("Kandidaten")
        table(dd.paper_candidates_table(trades_doc))
        st.caption("Ergebnisse je Mindestlot (0,01) zeigen die Strategie unabhängig von der Kontogröße. Das "
                   "Referenzkonto (1.000 €, 0,25 %) zeigt, welche Trades mit den freigegebenen Risikoregeln überhaupt "
                   "ausführbar wären – bei zu großem Stop verhindert die Mindestlotgröße den Trade.")
        eq = dd.paper_equity(trades_doc)
        if not eq.empty:
            st.write("Kumuliertes Nettoergebnis je 0,01 Lot (abgeschlossene Trades)")
            st.line_chart(eq, x="Zeit", y="kumuliert €", color="Kandidat")
        st.subheader("Trades")
        table(dd.paper_trades_table(trades_doc), "Noch keine Trades.")
        st.subheader("Signale")
        table(dd.paper_signals_table(trades_doc), "Noch keine Signale.")
        sc = trades_doc.get("kostenszenario") or {}
        if sc:
            st.caption("Kostenannahmen: " + "; ".join(
                f"{s}: Spread × {c['spread_factor']}, Slippage {c['slippage_frac']:.3%} je Ausführung, Kommission "
                f"{c['commission_frac_rt']:.2%} vom Nominalwert hin+zurück (Annahme)" for s, c in sc.items()))
    st.subheader("Ereignisse (neueste zuerst)")
    table(dd.events_table(paper["events"]), "Noch keine Ereignisse.")

# ---------------------------------------------------------------------- Daten
with tab_data:
    st.subheader("Aktualität der Daten")
    table(dd.freshness_table(cfg.data_root, now), "Keine Kursdaten gefunden (data/market).")
    st.caption("Aus dem Importstatus (data/market/<Symbol>/_meta.json). Aktualisieren mit `python daten_aktualisieren.py`. "
               "Gold handelt nicht am Wochenende – dann sind ältere Kerzen normal.")
    st.subheader("Datenqualität")
    qp = dd.latest_quality_report(cfg.report_dir)
    q = quality(str(qp) if qp else None, dd.mtime(qp) if qp else 0.0)
    if q:
        st.write(f"Quelle: {qp.name} (erstellt {str(q.get('created_utc'))[:19]} UTC)")
    table(dd.quality_table(q), "Kein Datenbericht gefunden (reports/phase2_datenbericht_*.json).")

# ---------------------------------------------------------------------- Kontrakt
with tab_contract:
    st.info("Punkt 3 (Kapital und Risiko) ist **offen**: Hier wird nur dokumentiert, bis zu welchem Stop-Abstand "
            "das geplante Risiko (0,25 % von 1.000 € = 2,50 €) mit der kleinsten Lotgröße eingehalten wird. "
            "Kapital und Risikogrenzen werden nicht verändert.")
    doc = paper["contract"]
    if doc:
        st.write(f"Quelle: {doc['source']} · {doc['created_utc'][:19]} UTC")
    else:
        doc = offline_contract(str(cfg.data_root), dd.mtime(Path(cfg.data_root) / "specs.json"))
        if doc:
            st.write(f"Quelle: {doc['source']}. Live-Prüfung: beim Start von `python beobachtung_ausfuehren.py`.")
    specs_df, tf_df = dd.contract_rows(doc)
    table(specs_df, "Keine Kontraktdaten (data/specs.json fehlt).")
    st.write("Stop-Abstände je Zeitrahmen")
    table(tf_df)
    st.caption("Der Stop-Abstand wird ab dem tatsächlichen Einstiegskurs gemessen (Spread enthalten). "
               "„max. Stop in ATR“ < 1,5 heißt: übliche Stops sind mit 0,01 Lot bei 2,50 € Risiko nicht möglich.")
