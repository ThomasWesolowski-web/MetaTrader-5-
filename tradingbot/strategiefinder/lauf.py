"""Ein Lauf des Strategie-Finders: Kerzen laden, alle Kandidaten rechnen, je Setup per Walk-Forward wählen,
Ampel, Zeitmaschinen-Auswertung (Monte Carlo) für den Sieger. NUR RECHNEN – keine Orders."""

from __future__ import annotations

import time
from collections import defaultdict

import numpy as np
import pandas as pd

from ..zeitmaschine import auswertung as zaw
from ..zeitmaschine.kosten import scenarios
from . import auswahl as aw
from .kandidaten import (GOLDBOT, GRIDS, RICHTUNGEN, SITZUNG_NAMEN, ZEITRAHMEN, Kandidat, alle_kandidaten,
                         anzahl_regeln)
from .rechnen import (Trades, goldbot_alle, goldbot_regel, kaufen_und_halten, kompakt, rechne_alle, trades_regel,
                      tstat)

SYMBOL = "XAUUSD"


def run(src, zeitrahmen=ZEITRAHMEN, sitzungen=None, richtungen=None, strategien=None, mit_goldbot: bool = True,
        konto: float = 357.0, lot: float = 0.01, gcfg=None, regeln: aw.Regeln | None = None, laeufe: int = 5000,
        kerne: int | None = None, log=print) -> dict:
    t0 = time.time()
    regeln = regeln or aw.Regeln()
    spec = src.spec(SYMBOL)
    kosten = scenarios(SYMBOL)
    if mit_goldbot and gcfg is None:
        from ..goldbot.config import GoldConfig
        gcfg = GoldConfig()
    from ..strategies.zusatz import SITZUNGEN
    sitzungen = tuple(sitzungen or SITZUNGEN)
    richtungen = tuple(richtungen if richtungen is not None else RICHTUNGEN)

    # ------------------------------------------------------------------ 1) Kerzen
    bars, spannen = {}, {}
    for tf in zeitrahmen:
        try:
            b = src.bars(SYMBOL, tf)
        except Exception as e:                                       # noqa: BLE001
            log(f"  {tf}: übersprungen ({e})")
            continue
        if len(b) < 2000:
            log(f"  {tf}: nur {len(b)} Kerzen – übersprungen")
            continue
        bars[tf] = b
        spannen[tf] = (pd.Timestamp(b["time"].iloc[0]), pd.Timestamp(b["time"].iloc[-1]))
        log(f"  {SYMBOL} {tf}: {len(b):,} Kerzen von {spannen[tf][0].date()} bis {spannen[tf][1].date()} "
            f"({src.source.get((SYMBOL, tf), '?')})".replace(",", "."))
    if not bars:
        raise RuntimeError("Keine Kerzen gefunden – MT5 öffnen oder python daten_aktualisieren.py ausführen")

    # ------------------------------------------------------------------ 2) alle Kandidaten rechnen (konservativ)
    kands = alle_kandidaten(tuple(bars), sitzungen, richtungen, strategien, mit_goldbot)
    log(f"\n  {len(kands):,} Varianten werden gerechnet (Kosten 'konservativ') ...".replace(",", "."))
    res, by_key, fehler, gold_kons = rechne_alle(kands, bars, spec, kosten["konservativ"], lot, gcfg, konto, kerne,
                                                 log)

    # ------------------------------------------------------------------ 3) Walk-Forward je Setup
    log("\n  Walk-Forward je Setup ...")
    je_setup: dict[tuple, dict[str, Trades]] = defaultdict(dict)
    for key, tr in res.items():
        je_setup[by_key[key].setup][key] = tr
    setups = []
    for tf in bars:
        for s in sitzungen:
            cands = je_setup.get((tf, s), {})
            if not cands:
                continue
            setups.append(aw.bewerte_setup(tf, s, cands, *spannen[tf], regeln))

    # ------------------------------------------------------------------ 4) harte Kosten für alle gewählten Regeln
    gewaehlt = {w.key for st in setups for w in st.fenster if w.key}
    log(f"  harte Kosten für {len(gewaehlt)} gewählte Regeln ..."
        + (" (Gold-Bot dabei: dauert einige Minuten)" if any(by_key[k].strategie == GOLDBOT for k in gewaehlt)
           else ""))
    hart = _rechne_einzeln(gewaehlt, by_key, bars, spec, kosten["hart"], lot, gcfg, konto)
    for st in setups:
        picks = [(aw._ns(w.a), aw._ns(w.b), w.key) for w in st.fenster]
        st.oos_hart, _ = aw._oos_sammeln(picks, hart)
        st.kz_hart = aw.kennzahlen(st.oos_hart)
        st.vergleich_halten = kaufen_und_halten(bars[st.tf], spec, aw._ns(st.fenster[0].a),
                                                aw._ns(spannen[st.tf][1]) + 1, lot) if st.fenster else {}
    aw.setup_dsr(setups)
    for st in setups:
        aw.ampel(st, regeln)
    rang = aw.rangliste(setups)

    # ------------------------------------------------------------------ 5) Sieger mit der Zeitmaschine auswerten
    sieger = rang[0] if rang else None
    zm = {}
    if sieger is not None and len(sieger.oos):
        log("  Zeitmaschine (Monte Carlo) für den Sieger ...")
        voll = _oos_voll(sieger, by_key, bars, spec, kosten["konservativ"], lot, gcfg, konto, gold_kons)
        if len(voll):
            zm = {"kennzahlen": zaw.kennzahlen(voll, konto, 20.0),
                  "monte_carlo": zaw.monte_carlo(voll, konto, laeufe, 52, 4.0),
                  "halbjahre": zaw.je_zeitraum(voll, "H")}

    # ------------------------------------------------------------------ 6) Rückblick: wie viele Varianten "glänzen"?
    rueck = _rueckblick(res, by_key)
    familien = _familien(setups, by_key)
    out = {
        "symbol": SYMBOL, "konto": konto, "lot": lot, "regeln": vars(regeln),
        "erstellt_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "zeitraeume": {tf: [str(a.date()), str(b.date())] for tf, (a, b) in spannen.items()},
        "varianten": len(kands), "varianten_mit_trades": len(res), "regeln_je_familie": anzahl_regeln(),
        "fehler": fehler,
        "setups": [_setup_dict(st, by_key) for st in rang],
        "sieger": _setup_dict(sieger, by_key, details=True, res=res) if sieger else None,
        "zeitmaschine": zm, "rueckblick": rueck, "familien": familien,
        "kosten": {n: c.to_dict() for n, c in kosten.items()},
        "dauer_s": round(time.time() - t0, 1),
    }
    out["urteil"] = urteil(out)
    if sieger is not None and sieger.ampel == "gruen" and sieger.aktuell:
        out["demo_kandidat"] = by_key[sieger.aktuell].demo_eintrag(SYMBOL)
    return out


# ============================================================================ Hilfen
def _rechne_einzeln(keys, by_key, bars, spec, cost, lot, gcfg, konto) -> dict[str, Trades]:
    out = {}
    gold = None
    for key in keys:
        k = by_key[key]
        if k.strategie == GOLDBOT:
            if gold is None:
                gold = goldbot_alle(bars["M15"], spec, cost, gcfg, konto)
            out[key] = kompakt(goldbot_regel(k, gold, bars["M15"]))
        else:
            out[key] = kompakt(trades_regel(k, bars[k.tf], spec, cost, lot))
    return out


def _oos_voll(st: aw.SetupErgebnis, by_key, bars, spec, cost, lot, gcfg, konto, gold=None) -> pd.DataFrame:
    """Out-of-Sample-Trades des Siegers mit allen Spalten (für Kennzahlen und Monte Carlo der Zeitmaschine)."""
    parts = []
    want = defaultdict(set)
    for e, key in zip(st.oos.entry, st.oos_keys):
        want[key].add(int(e))
    for key, entries in want.items():
        k = by_key[key]
        if k.strategie == GOLDBOT:
            if gold is None:
                gold = goldbot_alle(bars["M15"], spec, cost, gcfg, konto)
            df = goldbot_regel(k, gold, bars["M15"])
        else:
            df = trades_regel(k, bars[k.tf], spec, cost, lot)
        ns = pd.DatetimeIndex(df["entry_time"]).as_unit("ns").asi8
        sub = df[np.isin(ns, list(entries))].copy()
        sub["setup"] = k.name()
        parts.append(sub)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
    return df


def _setup_dict(st: aw.SetupErgebnis, by_key, details: bool = False, res: dict | None = None) -> dict:
    k_akt = by_key.get(st.aktuell) if st.aktuell else None
    d = {"zeitrahmen": st.tf, "sitzung": st.sitzung, "sitzung_name": SITZUNG_NAMEN.get(st.sitzung, st.sitzung),
         "varianten": st.varianten, "ampel": st.ampel, "gruende": st.gruende,
         "oos": st.kz, "oos_hart": st.kz_hart, "dsr_oos": st.dsr_oos,
         "oos_von": st.fenster[0].a if st.fenster else None, "oos_bis": st.fenster[-1].b if st.fenster else None,
         "abschnitte": len(st.fenster),
         "abschnitte_mit_handel": sum(1 for w in st.fenster if w.oos.get("trades")),
         "abschnitte_im_plus": sum(1 for w in st.fenster if w.oos.get("summe_usd", 0) > 0),
         "wechsel": len({w.key for w in st.fenster if w.key}),
         "aktuell": k_akt.beschreibung() if k_akt else None, "aktuell_key": st.aktuell,
         "aktuell_richtung": k_akt.richtung if k_akt else None,
         "aktuell_kz": st.aktuell_kz, "aktuell_dsr": st.aktuell_dsr,
         "halten": st.vergleich_halten}
    if details:
        from ..strategies import base as sb
        d["fenster"] = [{"von": w.a, "bis": w.b, "regel": by_key[w.key].beschreibung() if w.key else None,
                         "lern_t": w.lern_t, "lern_trades": w.lern_trades, "oos": w.oos} for w in st.fenster]
        d["kurve"] = [round(float(x), 2) for x in np.cumsum(st.oos.pnl)]
        d["kurve_zeit"] = [str(pd.Timestamp(x, tz="UTC").date()) for x in st.oos.exit]
        d["kurve_hart"] = [round(float(x), 2) for x in np.cumsum(st.oos_hart.pnl)] if st.oos_hart is not None else []
        if k_akt is not None:
            base = k_akt.strategie
            d["aktuell_regeln"] = (sb._REGISTRY[base].rules if base in sb._REGISTRY
                                   else "Gold-Bot-Regeln wie live (tradingbot/goldbot/strategie.py).")
            d["demo_eintrag"] = k_akt.demo_eintrag(SYMBOL)
            d["nachbarn"] = _nachbarn(k_akt, res or {}, by_key, st.aktuell_kz.get("lern_von"))
    return d


def _nachbarn(k: Kandidat, res: dict, by_key, lern_von: str | None) -> list[dict]:
    """Robustheit: dieselbe Strategie mit den anderen Parametern (gleicher Zeitrahmen, Handelszeit, Richtung) im
    selben Lernfenster. Glänzt nur genau ein Parametersatz, ist das ein Warnzeichen für Kurvenanpassung."""
    a = aw._ns(pd.Timestamp(lern_von, tz="UTC")) if lern_von else 0
    out = []
    for key, tr in res.items():
        o = by_key[key]
        if (o.strategie, o.tf, o.sitzung, o.richtung) != (k.strategie, k.tf, k.sitzung, k.richtung):
            continue
        sub = tr.nimm(tr.entry >= a)
        kz = aw.kennzahlen(sub)
        out.append({"params": ", ".join(f"{n} {v}" for n, v in o.params) or "–", "gewaehlt": o == k,
                    "trades": kz["trades"], "summe_usd": kz["summe_usd"], "erwartung_r": kz["erwartung_r"],
                    "t": kz["t"]})
    return sorted(out, key=lambda x: -(x["t"] or 0))


def _rueckblick(res: dict[str, Trades], by_key) -> dict:
    """Wie sähe die naive Auswahl aus (beste Variante über die ganze Historie)? Und wie viele sind im Plus?"""
    rows = []
    for key, tr in res.items():
        if len(tr) < 30:
            continue
        rows.append((tstat(tr.r), key, len(tr), float(tr.pnl.sum()), float(tr.r.mean())))
    rows.sort(reverse=True)
    n_plus = sum(1 for r in rows if r[3] > 0)
    return {"bewertet": len(rows), "im_plus": n_plus,
            "anteil_im_plus": round(n_plus / len(rows), 3) if rows else None,
            "top": [{"regel": by_key[k].beschreibung(), "t": round(t, 2), "trades": n, "summe_usd": round(s, 2),
                     "erwartung_r": round(m, 3)} for t, k, n, s, m in rows[:15]]}


def _familien(setups: list[aw.SetupErgebnis], by_key) -> list[dict]:
    """Welche Strategie-Familien hat der Finder im Walk-Forward gewählt – und was brachten sie danach?"""
    agg: dict[str, dict] = {}
    for st in setups:
        for key, pnl, r in zip(st.oos_keys, st.oos.pnl, st.oos.r):
            f = by_key[key].familie()
            a = agg.setdefault(f, {"familie": f, "trades": 0, "summe_usd": 0.0, "summe_r": 0.0})
            a["trades"] += 1
            a["summe_usd"] += float(pnl)
            a["summe_r"] += float(r)
    out = sorted(agg.values(), key=lambda a: -a["summe_r"])
    for a in out:
        a["summe_usd"] = round(a["summe_usd"], 2)
        a["erwartung_r"] = round(a["summe_r"] / a["trades"], 3) if a["trades"] else None
        a["summe_r"] = round(a["summe_r"], 1)
    return out


def urteil(o: dict) -> list[str]:
    out = []
    st = o["setups"]
    g = [s for s in st if s["ampel"] == "gruen"]
    y = [s for s in st if s["ampel"] == "gelb"]
    rb = o["rueckblick"]
    if rb.get("bewertet"):
        out.append(f"Im Rückblick (ganze Historie, ohne Walk-Forward) sind {rb['im_plus']} von {rb['bewertet']} "
                   f"Varianten im Plus ({100 * (rb['anteil_im_plus'] or 0):.0f} %). Solche Rückblick-Sieger "
                   "sind meist Zufall – deshalb zählt nur das Out-of-Sample-Ergebnis.")
    if g:
        s = g[0]
        out.append(f"{len(g)} Setup(s) haben alle Prüfungen bestanden. Bestes: {s['zeitrahmen']} · "
                   f"{s['sitzung_name']} mit aktuell '{s['aktuell']}'. Erst auf dem Demokonto beobachten.")
    elif y:
        s = y[0]
        out.append(f"Kein Setup besteht alle Prüfungen. {len(y)} Setup(s) waren Out-of-Sample im Plus, aber nicht "
                   f"überzeugend (bestes: {s['zeitrahmen']} · {s['sitzung_name']}: {', '.join(s['gruende'][:2])}). "
                   "Beobachten, nicht handeln.")
    else:
        out.append("Kein Setup war Out-of-Sample im Plus: Nach Kosten hat der Finder auf diesen Daten keinen "
                   "verlässlichen Vorteil gefunden.")
    s0 = o.get("sieger")
    if s0 and s0.get("halten", {}).get("usd") is not None:
        out.append(f"Zum Vergleich: Gold im selben Zeitraum einfach kaufen und halten (0,01 Lot, mit Swap) hätte "
                   f"{s0['halten']['usd']:+.0f} $ gebracht, der Finder im besten Setup {s0['oos']['summe_usd']:+.0f} $.")
    if s0 and s0.get("aktuell_richtung") == 1:
        out.append("Die aktuelle Wahl kauft nur. Sie lebt vom starken Gold-Anstieg der letzten Jahre – dreht der "
                   "Trend, kann das schnell kippen.")
    zk = (o.get("zeitmaschine") or {}).get("kennzahlen") or {}
    if zk.get("trades") and zk.get("max_drawdown_pct", 0) >= 25:
        out.append(f"Auf {o['konto']:.0f} $ gerechnet wäre das Konto unterwegs um bis zu {zk['max_drawdown_pct']:.0f} % "
                   "gefallen" + (f"; der 20-%-Stopp des Demo-Bots hätte am {zk['dd_stopp_am']} ausgelöst."
                                 if zk.get("dd_stopp_am") else "."))
    nb = (s0 or {}).get("nachbarn") or []
    if len(nb) > 1:
        pos = sum(1 for x in nb if (x["erwartung_r"] or 0) > 0)
        out.append(f"Nachbar-Test: {pos} von {len(nb)} Parametersätzen derselben Regel sind im Lernfenster im Plus"
                   + (" – die Regel ist nicht nur ein Zufallstreffer eines einzelnen Werts." if pos >= len(nb) / 2
                      else " – Warnzeichen für Kurvenanpassung."))
    out.append("Vergangenheit, keine Garantie. Nur Rechnung – es wurde nichts gehandelt.")
    return out
