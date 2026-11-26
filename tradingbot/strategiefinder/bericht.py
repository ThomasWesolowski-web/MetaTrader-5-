"""Ergebnis des Strategie-Finders als Text (Konsole), HTML-Seite (ohne Internet) und JSON."""

from __future__ import annotations

import html
import json
from pathlib import Path

from ..zeitmaschine.bericht import CSS, _num, _pct, _svg_hist, _svg_line, _usd

AMPEL = {"gruen": ("●", "grün", "var(--pos)"), "gelb": ("●", "gelb", "#c98a00"), "rot": ("●", "rot", "var(--neg)")}
EXTRA_CSS = """.amp{font-size:1.1rem}.mat td{text-align:center}.mat td:first-child{text-align:left}
.wrap{overflow-x:auto}code{font-size:.85rem}td{white-space:nowrap}td.l{white-space:normal;text-align:left}
.tile b span{font-size:1.35rem}"""


def _setup_name(s: dict) -> str:
    return f"{s['zeitrahmen']} · {s['sitzung_name']}"


# ============================================================================ Text
def text(o: dict) -> str:
    L = ["STRATEGIE-FINDER  XAUUSD – nur Rechnung, es wird NICHTS gehandelt",
         f"{o['varianten']:,} Varianten gerechnet ({o['varianten_mit_trades']:,} mit Trades)".replace(",", ".")
         + f", Konto {o['konto']:.0f} $, Lot {_num(o['lot'])}, Kosten 'konservativ' (Prüfung zusätzlich 'hart')",
         "", "Setups (Out-of-Sample = so wäre es dem Finder mit vierteljährlicher Neuwahl wirklich ergangen):",
         f"   {'Setup':<28} {'Ampel':<6} {'Trades':>6} {'Ergebnis':>12} {'hart':>12} {'R/Trade':>8} {'PF':>5} "
         f"{'DSR':>5}  {'Halten':>10}"]
    for s in o["setups"]:
        k, h = s["oos"], s["oos_hart"] or {}
        L.append(f"   {_setup_name(s):<28} {AMPEL[s['ampel']][1]:<6} {k['trades']:>6} {_usd(k['summe_usd']):>12} "
                 f"{_usd(h.get('summe_usd')):>12} {_num(k.get('erwartung_r')):>8} {_num(k.get('pf')):>5} "
                 f"{_num(s.get('dsr_oos')):>5}  {_usd((s.get('halten') or {}).get('usd')):>10}")
    s = o.get("sieger")
    if s:
        L += ["", f"Bestes Setup: {_setup_name(s)}  (Ampel {AMPEL[s['ampel']][1]})"]
        if s.get("gruende"):
            L += [f"   - {g}" for g in s["gruende"]]
        L.append(f"   Aktuelle Wahl des Finders: {s.get('aktuell') or 'keine (nichts überzeugt im Lernfenster)'}")
        zm = (o.get("zeitmaschine") or {})
        k, mc = zm.get("kennzahlen") or {}, zm.get("monte_carlo") or {}
        if k.get("trades"):
            L.append(f"   Zeitmaschine (Out-of-Sample-Trades, Konto {o['konto']:.0f} $): Gewinnrate "
                     f"{_pct(k['gewinnrate'])}, CRV {_num(k['crv'])}, größter Rückgang {_num(k['max_drawdown_pct'], 1)} %"
                     + (f", Handelsstopp 20 % am {k['dd_stopp_am']}" if k.get("dd_stopp_am") else ""))
        if mc.get("laeufe"):
            e = mc["endkonto"]
            L.append(f"   Monte Carlo 52 Wochen: schlecht {e['5']:.0f} $ | Mitte {e['50']:.0f} $ | gut {e['95']:.0f} $ | "
                     f"Ruin {_pct(mc['p_ruin'])}")
    L += ["", "Urteil:"] + [f"  * {u}" for u in o["urteil"]]
    if o.get("demo_kandidat"):
        L += ["", "Demo-Kandidat (nur Demokonto) – Eintrag für config.toml steht im Bericht."]
    return "\n".join(L)


# ============================================================================ HTML
def html_page(o: dict) -> str:
    esc = html.escape
    cls = lambda v: "pos" if (v or 0) > 0 else "neg"
    P = [f"<!DOCTYPE html><html lang='de'><head><meta charset='utf-8'><title>Strategie-Finder XAUUSD</title>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}{EXTRA_CSS}</style></head>"
         "<body><h1>Strategie-Finder: Gold (XAUUSD)</h1>",
         f"<p class='sub'>{o['varianten']:,} Regeln durchgerechnet ".replace(",", ".") +
         f"(Strategie × Parameter × Zeitrahmen × Handelszeit × Richtung) auf deinen echten MT5-Kerzen, mit Spread, "
         f"Slippage, Kommission und Swap. Konto {o['konto']:.0f} $, Lot {_num(o['lot'])}. Erstellt "
         f"{esc(o['erstellt_utc'][:16].replace('T', ' '))} UTC. Nur Rechnung – es wurde nichts gehandelt.</p>"]
    P.append("<div class='verdict'><b>Urteil</b><ul>" + "".join(f"<li>{esc(u)}</li>" for u in o["urteil"]) +
             "</ul></div>")

    # --- Übersicht als Matrix
    tfs = list(o["zeitraeume"])
    sitz = []
    for s in o["setups"]:
        if s["sitzung"] not in sitz:
            sitz.append(s["sitzung"])
    order = [x for x in ("alle", "asien", "london", "new_york") if x in sitz]
    by = {(s["zeitrahmen"], s["sitzung"]): s for s in o["setups"]}
    names = {s["sitzung"]: s["sitzung_name"] for s in o["setups"]}
    P.append("<h2>1. Alle Setups auf einen Blick</h2><p class='small'>Je Feld: Ergebnis der Out-of-Sample-Trades "
             "(Finder wählt jedes Quartal neu, nur mit Daten davor). Grün = alle Prüfungen bestanden, gelb = im Plus, "
             "aber nicht überzeugend, rot = kein Vorteil.</p><div class='wrap'><table class='mat'><tr><th>Zeitrahmen"
             "</th>" + "".join(f"<th>{esc(names[x])}</th>" for x in order) + "</tr>")
    for tf in tfs:
        row = [f"<td><b>{tf}</b><br><span class='small'>{o['zeitraeume'][tf][0][:7]} – "
               f"{o['zeitraeume'][tf][1][:7]}</span></td>"]
        for x in order:
            s = by.get((tf, x))
            if not s:
                row.append("<td>–</td>")
                continue
            sym, _, col = AMPEL[s["ampel"]]
            row.append(f"<td><span class='amp' style='color:{col}'>{sym}</span> "
                       f"<span class='{cls(s['oos']['summe_usd'])}'>{_usd(s['oos']['summe_usd'])}</span><br>"
                       f"<span class='small'>{s['oos']['trades']} Trades</span></td>")
        P.append("<tr>" + "".join(row) + "</tr>")
    P.append("</table></div>")

    P.append("<h2>2. Rangliste</h2><div class='wrap'><table><tr><th>Setup</th><th>Ampel</th><th>Trades</th>"
             "<th>Ergebnis</th><th>hart</th><th>R/Trade</th><th>PF</th><th>DSR</th><th>Abschnitte im Plus</th>"
             "<th>Gold halten</th></tr>")
    for s in o["setups"]:
        k, h = s["oos"], s["oos_hart"] or {}
        sym, name, col = AMPEL[s["ampel"]]
        P.append(f"<tr><td class='l'>{esc(_setup_name(s))}</td><td style='color:{col}'>{sym} {name}</td><td>{k['trades']}</td>"
                 f"<td class='{cls(k['summe_usd'])}'>{_usd(k['summe_usd'])}</td>"
                 f"<td class='{cls(h.get('summe_usd'))}'>{_usd(h.get('summe_usd'))}</td>"
                 f"<td>{_num(k.get('erwartung_r'))}</td><td>{_num(k.get('pf'))}</td><td>{_num(s.get('dsr_oos'))}</td>"
                 f"<td>{s['abschnitte_im_plus']} / {s['abschnitte_mit_handel']}</td>"
                 f"<td>{_usd((s.get('halten') or {}).get('usd'))}</td></tr>")
    P.append("</table></div><p class='small'>hart = gleiche Wahl mit Spread × 2,5 und viel Slippage. DSR = Deflated "
             "Sharpe Ratio: Wahrscheinlichkeit, dass das Ergebnis kein Glück ist, obwohl so viele Setups verglichen "
             "wurden (ab 0,90 überzeugend). Gold halten = 0,01 Lot im selben Zeitraum kaufen und halten, mit Swap.</p>")

    # --- Sieger
    s = o.get("sieger")
    if s:
        sym, name, col = AMPEL[s["ampel"]]
        P.append(f"<h2>3. Bestes Setup: {esc(_setup_name(s))} <span style='color:{col}'>{sym} {name}</span></h2>")
        if s.get("gruende"):
            P.append("<p>Warum nicht grün:</p><ul>" + "".join(f"<li>{esc(g)}</li>" for g in s["gruende"]) + "</ul>")
        P.append(f"<p><b>Aktuelle Wahl:</b> {esc(s.get('aktuell') or 'keine – im Lernfenster hat nichts überzeugt')}"
                 "</p>")
        ak = s.get("aktuell_kz") or {}
        if ak.get("trades"):
            P.append(f"<p class='small'>Im Lernfenster seit {esc(ak.get('lern_von', ''))} (Rückblick, nicht "
                     f"Out-of-Sample): {ak['trades']} Trades, {_usd(ak['summe_usd'])}, {_num(ak.get('erwartung_r'))} R "
                     f"je Trade, Profit-Faktor {_num(ak.get('pf'))}. Deflated Sharpe über alle {s['varianten']} "
                     f"Varianten des Setups: {_num(s.get('aktuell_dsr'))}.</p>")
        if s.get("aktuell_regeln"):
            P.append(f"<p class='small'><b>Regeln:</b> {esc(s['aktuell_regeln'])}</p>")
        if s.get("nachbarn") and len(s["nachbarn"]) > 1:
            nb = s["nachbarn"]
            pos = sum(1 for x in nb if (x["erwartung_r"] or 0) > 0)
            P.append(f"<p class='small'><b>Nachbar-Test:</b> dieselbe Regel mit den anderen Parametern im selben "
                     f"Lernfenster – {pos} von {len(nb)} im Plus. Glänzt nur ein einziger Parametersatz, ist das ein "
                     "Warnzeichen.</p><table><tr><th>Parameter</th><th>Trades</th><th>Ergebnis</th><th>R/Trade</th>"
                     "<th>t</th></tr>")
            for x in nb:
                mark = " ◀ gewählt" if x["gewaehlt"] else ""
                P.append(f"<tr><td>{esc(x['params'])}{mark}</td><td>{x['trades']}</td>"
                         f"<td class='{cls(x['summe_usd'])}'>{_usd(x['summe_usd'])}</td>"
                         f"<td>{_num(x['erwartung_r'])}</td><td>{_num(x['t'])}</td></tr>")
            P.append("</table>")
        if s.get("kurve"):
            P.append("<p class='small'>Out-of-Sample-Ergebnis in $ (Summe nach jedem Trade). Durchgezogen: "
                     "konservative Kosten, gestrichelt: harte Kosten.</p>")
            P.append(_svg_line(s["kurve_zeit"], [("konservativ", s["kurve"], "var(--acc)", ""),
                                                 ("hart", s.get("kurve_hart") or [], "var(--neg)", "5 4")], ref=0))
        P.append("<details open><summary>Was der Finder in jedem Quartal gewählt hat</summary><div class='wrap'><table>"
                 "<tr><th>Quartal</th><th style='text-align:left'>Gewählte Regel</th><th>t im Lernfenster</th><th>Trades</th><th>Ergebnis</th>"
                 "</tr>")
        for w in s.get("fenster", []):
            ok = w["oos"] or {}
            P.append(f"<tr><td>{esc(w['von'][:7])}</td><td class='l'>{esc(w['regel'] or 'aussetzen')}</td>"
                     f"<td>{_num(w['lern_t'])}</td><td>{ok.get('trades', 0)}</td>"
                     f"<td class='{cls(ok.get('summe_usd'))}'>{_usd(ok.get('summe_usd'))}</td></tr>")
        P.append("</table></div></details>")

        zm = o.get("zeitmaschine") or {}
        k, mc = zm.get("kennzahlen") or {}, zm.get("monte_carlo") or {}
        if k.get("trades"):
            tiles = [("Trades", f"{k['trades']}", f"{k['von']} – {k['bis']}"),
                     ("Gewinnrate", _pct(k["gewinnrate"]), f"CRV {_num(k['crv'])}"),
                     ("Ergebnis", f"<span class='{cls(k['summe_usd'])}'>{_usd(k['summe_usd'])}</span>",
                      f"{_num(k['erwartung_r'])} R je Trade"),
                     ("Größter Rückgang", f"{_num(k['max_drawdown_pct'], 1)} %", f"{k['max_drawdown_usd']:.0f} $"),
                     ("Ruin-Risiko", _pct(mc.get("p_ruin")), "in 52 Wochen")]
            P.append("<h2>4. Zeitmaschine für das beste Setup</h2><p class='small'>Die Out-of-Sample-Trades auf deinem "
                     f"Konto ({o['konto']:.0f} $) und 52-Wochen-Monte-Carlo mit Wochen-Blöcken wie in der "
                     "Zeitmaschine.</p><div class='tiles'>" +
                     "".join(f"<div class='tile'><span>{a}</span><b>{b}</b><span>{c}</span></div>" for a, b, c in tiles)
                     + "</div>")
            if k.get("dd_stopp_am"):
                P.append(f"<p><b>Der Demo-Bot wäre am {k['dd_stopp_am']} in seinen 20-%-Handelsstopp gegangen.</b></p>")
            if mc.get("laeufe"):
                f = mc["faecher"]
                P.append(_svg_line([f"Trade {x}" for x in f["trade_nr"]],
                                   [("Mitte", f["p50"], "var(--acc)", ""),
                                    ("schlecht 5 %", f["p5"], "var(--neg)", "5 4"),
                                    ("gut 95 %", f["p95"], "var(--pos)", "5 4")], ref=o["konto"],
                                   band=(f["p25"], f["p75"])))
                P.append(_svg_hist(mc["endkonten"], o["konto"], mc["ruin_konto"]))
                e = mc["endkonto"]
                P.append(f"<p class='small'>Konto nach 52 Wochen: schlecht (5 %) {e['5']:.0f} $, Mitte {e['50']:.0f} $, "
                         f"gut (95 %) {e['95']:.0f} $. Im Plus in {_pct(mc['im_plus'])} der Durchläufe, Rückgang ≥ 25 % "
                         f"in {_pct(mc['p_rueckgang']['25'])}, Ruin (unter {mc['ruin_konto']:.0f} $) in "
                         f"{_pct(mc['p_ruin'])}.</p>")
        if o.get("demo_kandidat"):
            P.append("<h2>Demo-Kandidat</h2><p>Alle Prüfungen bestanden. So steht er in <code>config.toml</code> "
                     "(nur Demokonto; mit <code>--demo-eintragen</code> trägt der Finder ihn selbst ein):</p>"
                     f"<pre>{esc(toml_eintrag(o['demo_kandidat']))}</pre>")

    # --- Familien und Rückblick
    if o.get("familien"):
        P.append("<h2>5. Welche Strategie-Familien hat der Finder gewählt?</h2><p class='small'>Alle Out-of-Sample-"
                 "Trades aller Setups, nach Familie der jeweils gewählten Regel.</p><table><tr><th>Familie</th>"
                 "<th>Trades</th><th>Ergebnis</th><th>R je Trade</th></tr>")
        for f in o["familien"]:
            P.append(f"<tr><td>{esc(f['familie'])}</td><td>{f['trades']}</td>"
                     f"<td class='{cls(f['summe_usd'])}'>{_usd(f['summe_usd'])}</td><td>{_num(f['erwartung_r'])}</td>"
                     "</tr>")
        P.append("</table>")
    rb = o.get("rueckblick") or {}
    if rb.get("top"):
        P.append(f"<h2>6. Warnung: die Rückblick-Sieger</h2><p class='small'>So sähe die naive Auswahl aus – beste "
                 f"Varianten über die ganze Historie (mind. 30 Trades). {rb['im_plus']} von {rb['bewertet']} Varianten "
                 "sind im Rückblick im Plus. Mit so vielen Versuchen glänzen immer einige zufällig; diese Liste ist "
                 "KEINE Empfehlung.</p><div class='wrap'><table><tr><th>Regel</th><th>t</th><th>Trades</th>"
                 "<th>Ergebnis</th><th>R/Trade</th></tr>")
        for r in rb["top"]:
            P.append(f"<tr><td class='l'>{esc(r['regel'])}</td><td>{_num(r['t'])}</td><td>{r['trades']}</td>"
                     f"<td class='{cls(r['summe_usd'])}'>{_usd(r['summe_usd'])}</td><td>{_num(r['erwartung_r'])}</td>"
                     "</tr>")
        P.append("</table></div>")
    rg = o["regeln"]
    P.append("<h2>So prüft der Finder</h2><ul class='small'>"
             f"<li>Walk-Forward: Abschnitte von {rg['oos_monate']} Monaten; gewählt wird nur mit Trades aus den "
             f"{rg['lern_monate']} Monaten davor (mind. {rg['min_trades']} Trades, t-Wert ≥ {_num(rg['min_t'], 1)}, sonst "
             "setzt der Finder aus).</li><li>Kosten: konservativ (Spread × 1,5, Kommission wie Raw-Konto); Prüfung "
             "mit hart (Spread × 2,5, zehnfache Slippage). Swap mit den aktuellen MT5-Werten.</li>"
             f"<li>Grün nur bei: mind. {rg['gruen_trades']} Out-of-Sample-Trades, im Plus auch mit harten Kosten, "
             f"Profit-Faktor ≥ {_num(rg['gruen_pf'])}, mind. {100 * rg['gruen_fenster']:.0f} % der Abschnitte im Plus, "
             f"Deflated Sharpe ≥ {_num(rg['gruen_dsr'])}.</li>"
             "<li>Kerzen = Bid, Ask = Bid + Spread; Signal am Kerzenschluss, Einstieg zur nächsten Eröffnung; "
             "berühren Stop und Ziel dieselbe Kerze, zählt der Stop; Lücken zum Eröffnungskurs.</li>"
             "<li>Der Gold-Bot wird Kerze für Kerze wie live gerechnet (2 × 0,01 Lot, Risikogrenze aus goldbot.toml)."
             "</li></ul>")
    if o.get("fehler"):
        P.append("<p class='small'>Übersprungen: " + esc("; ".join(o["fehler"])) + "</p>")
    P.append("<p class='small'>Vergangenheit ist keine Garantie. Kein Finanzrat.</p></body></html>")
    return "".join(P)


def toml_eintrag(e: dict) -> str:
    p = e["params"]
    bp = ", ".join(f"{k} = {json.dumps(v)}" for k, v in p["basis_params"].items())
    return ("[[paper.candidates]]\n"
            f"symbol = \"{e['symbol']}\"\n"
            f"timeframe = \"{e['timeframe']}\"\n"
            f"strategy = \"{e['strategy']}\"\n"
            f"params = {{ basis = \"{p['basis']}\", basis_params = {{ {bp} }}, von_utc = {p['von_utc']}, "
            f"bis_utc = {p['bis_utc']}, richtung = {p['richtung']} }}\n")


def save(o: dict, folder: Path) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"strategiefinder_{o['symbol']}_{o['erstellt_utc'][:10]}"
    p_html, p_json = folder / f"{stem}.html", folder / f"{stem}.json"
    p_html.write_text(html_page(o), encoding="utf-8")
    p_json.write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p_html, p_json
