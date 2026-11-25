"""Ergebnis der Zeitmaschine als Text (Konsole) und als HTML-Seite mit Diagrammen (ohne Internet, ohne Zusatzpakete)."""

from __future__ import annotations

import html
import json
from pathlib import Path

NAMES = {"goldbot": "Gold-Bot (Order Blocks)", "orb": "Opening Range Breakout"}


def _pct(x) -> str:
    return "–" if x is None else f"{100 * x:.1f} %".replace(".", ",")


def _usd(x) -> str:
    return "–" if x is None else f"{x:+,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(x, nd=2) -> str:
    return "–" if x is None else f"{x:.{nd}f}".replace(".", ",")


def titel(o: dict) -> str:
    return f"{NAMES.get(o['strategie'], o['strategie'])} – {o['symbol']} {o['zeitrahmen']}"


# ============================================================================ Text
def text(o: dict) -> str:
    k = o["kennzahlen"][o["szenario"]]
    L = [f"ZEITMASCHINE  {titel(o)}", f"Konto {o['konto']:.0f} $, Lot {o['lot']:g}, Kosten '{o['szenario']}'"]
    if not k.get("trades"):
        L.append("Keine Trades im Zeitraum.")
        L += [f"  * {u}" for u in o["urteil"]]
        return "\n".join(L)
    L += [f"Zeitraum {k['von']} bis {k['bis']}: {k['trades']} Trades ({_num(k['trades_pro_monat'], 1)} pro Monat)",
          "",
          "1) So wäre es wirklich gelaufen (echte Kerzen, nach Kosten)",
          f"   Gewinnrate {_pct(k['gewinnrate'])}  |  CRV {_num(k['crv'])}  |  Profit-Faktor {_num(k['profit_faktor'])}",
          f"   Ergebnis {_usd(k['summe_usd'])} ({_num(k['rendite_pct'], 1)} %)  |  je Trade {_usd(k['erwartung_usd'])} "
          f"= {_num(k['erwartung_r'])} R",
          f"   Größter Rückgang {_num(k['max_drawdown_usd'])} $ = {_num(k['max_drawdown_pct'], 1)} % vom Konto  |  "
          f"längste Verlustserie {k['laengste_verlustserie']}",
          f"   Risiko je Trade (Median) {_num(k['risiko_usd_median'])} $ = {_num(k['risiko_pct_median'], 1)} % vom Konto",
          f"   Kosten (Kommission, Swap) {_num(k['kosten_usd'])} $; Spread und Slippage stecken in den Kursen"]
    if k.get("dd_stopp_am"):
        L.append(f"   ! Der Bot wäre am {k['dd_stopp_am']} in den Handelsstopp gegangen "
                 f"({_num(k['dd_stopp_pct'], 0)} % Rückgang)")
    if k.get("konto_leer_am"):
        L.append(f"   ! Das Konto wäre am {k['konto_leer_am']} leer gewesen")
    L += ["", "2) Kostenszenarien (gleiche Trades, andere Kosten)"]
    for n, kk in o["kennzahlen"].items():
        if kk.get("trades"):
            L.append(f"   {n:<12} {kk['trades']:>4} Trades  Gewinnrate {_pct(kk['gewinnrate']):>8}  "
                     f"Ergebnis {_usd(kk['summe_usd']):>12}  Rückgang {_num(kk['max_drawdown_pct'], 1):>6} %")
    if o.get("halbjahre"):
        L += ["", "3) Je Halbjahr"]
        for h in o["halbjahre"]:
            L.append(f"   {h['zeitraum']}  {h['trades']:>4} Trades  Gewinnrate {_pct(h['gewinnrate']):>8}  "
                     f"{_usd(h['summe_usd']):>12}")
    mc, mi = o["monte_carlo"], o["monte_carlo_einzeln"]
    if mc.get("laeufe"):
        e = mc["endkonto"]
        L += ["", f"4) Was hätte auch passieren können? {mc['laeufe']:,} Durchläufe über {mc['wochen']} Wochen "
                  f"(Blöcke von ~{mc['block_wochen']:g} Wochen)".replace(",", "."),
              f"   Konto nach {mc['wochen']} Wochen: schlecht (5 %) {e['5']:.0f} $ | eher schlecht {e['25']:.0f} $ | "
              f"Mitte {e['50']:.0f} $ | eher gut {e['75']:.0f} $ | gut (95 %) {e['95']:.0f} $",
              f"   Im Plus: {_pct(mc['im_plus'])}  |  verdoppelt: {_pct(mc['verdoppelt'])}",
              f"   Rückgang mind. 15 %: {_pct(mc['p_rueckgang']['15'])}  |  25 %: {_pct(mc['p_rueckgang']['25'])}  |  "
              f"50 %: {_pct(mc['p_rueckgang']['50'])}",
              f"   RUIN (Konto unter {mc['ruin_konto']:.0f} $): {_pct(mc['p_ruin'])}   "
              f"(einzeln gezogene Trades zum Vergleich: {_pct(mi.get('p_ruin'))})"]
    L += ["", "Urteil:"] + [f"  * {u}" for u in o["urteil"]]
    return "\n".join(L)


# ============================================================================ HTML
def _svg_line(xs_labels, series: list[tuple[str, list, str, str]], w=760, h=260, ref=None, band=None) -> str:
    """Einfaches Liniendiagramm. series: (Name, Werte, Farbe, Strich)."""
    vals = [v for _, s, _, _ in series for v in s] + ([ref] if ref is not None else [])
    if band:
        vals += band[0] + band[1]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    m = max(len(s) for _, s, _, _ in series)
    L, R, T, B = 56, 12, 12, 26
    X = lambda i: L + (w - L - R) * (i / max(m - 1, 1))
    Y = lambda v: T + (h - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" class="ax" text-anchor="end">{v:,.0f}</text>'
                   .replace(",", "."))
    if band:
        a, b = band
        pts = [f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(a)] + \
              [f"{X(i):.1f},{Y(v):.1f}" for i, v in reversed(list(enumerate(b)))]
        out.append(f'<polygon points="{" ".join(pts)}" class="band"/>')
    if ref is not None:
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}" class="ref"/>')
    for name, s, col, dash in series:
        if not s:
            continue
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2" '
                   f'stroke-dasharray="{dash}"><title>{html.escape(name)}</title></polyline>')
    if xs_labels:
        for i, anchor in ((0, "start"), (len(xs_labels) // 2, "middle"), (len(xs_labels) - 1, "end")):
            out.append(f'<text x="{X(i * (m - 1) / max(len(xs_labels) - 1, 1)):.1f}" y="{h - 6}" class="ax" '
                       f'text-anchor="{anchor}">{html.escape(str(xs_labels[i]))}</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_hist(values: list[float], start: float, ruin: float, w=760, h=220, bins=30) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        hi = lo + 1
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / step))] += 1
    L, R, T, B = 40, 12, 10, 26
    bw = (w - L - R) / bins
    mx = max(counts)
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    for k, cnt in enumerate(counts):
        x0 = lo + k * step
        cls = "bar-bad" if x0 + step <= ruin * 1.0001 else ("bar-up" if x0 >= start else "bar-dn")
        bh = (h - T - B) * cnt / mx
        out.append(f'<rect x="{L + k * bw + 1:.1f}" y="{h - B - bh:.1f}" width="{bw - 2:.1f}" height="{bh:.1f}" '
                   f'class="{cls}"><title>{x0:.0f}–{x0 + step:.0f} $: {cnt}</title></rect>')
    sx = L + (w - L - R) * (start - lo) / (hi - lo)
    out.append(f'<line x1="{sx:.1f}" x2="{sx:.1f}" y1="{T}" y2="{h - B}" class="ref"/>'
               f'<text x="{sx:.1f}" y="{h - 8}" class="ax" text-anchor="middle">Start {start:.0f} $</text>')
    out.append(f'<text x="{L}" y="{h - 8}" class="ax">{lo:.0f} $</text>'
               f'<text x="{w - R}" y="{h - 8}" class="ax" text-anchor="end">{hi:.0f} $</text></svg>')
    return "".join(out)


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--pos:#1f7a4d;--neg:#b3261e;--acc:#2f5d8a;--band:#2f5d8a22}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe7;--mut:#a3a19b;--line:#34332f;--pos:#5cc08d;
--neg:#ef7b72;--acc:#7fb0e0;--band:#7fb0e026}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;
padding:0 1rem}h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.1rem;margin:2rem 0 .5rem;border-bottom:1px solid
var(--line);padding-bottom:.3rem}.sub{color:var(--mut)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,
minmax(150px,1fr));gap:.6rem;margin:1rem 0}.tile{border:1px solid var(--line);border-radius:10px;padding:.6rem .8rem}
.tile b{display:block;font-size:1.35rem}.tile span{color:var(--mut);font-size:.85rem}.pos{color:var(--pos)}
.neg{color:var(--neg)}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{padding:.3rem
.5rem;border-bottom:1px solid var(--line);text-align:right}td:first-child,th:first-child{text-align:left}
.chart{width:100%;height:auto}.grid{stroke:var(--line)}.ref{stroke:var(--mut);stroke-dasharray:4 4}
.ax{fill:var(--mut);font-size:11px}.band{fill:var(--band)}.bar-up{fill:var(--pos)}.bar-dn{fill:var(--acc)}
.bar-bad{fill:var(--neg)}.verdict{border-left:4px solid var(--acc);padding:.4rem .9rem;background:var(--band);
border-radius:6px}details{margin-top:1rem}.small{font-size:.85rem;color:var(--mut)}
"""


def html_page(o: dict) -> str:
    k = o["kennzahlen"][o["szenario"]]
    mc, mi = o["monte_carlo"], o["monte_carlo_einzeln"]
    esc = html.escape
    P = [f"<!DOCTYPE html><html lang='de'><head><meta charset='utf-8'><title>Zeitmaschine {esc(titel(o))}</title>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style></head><body>",
         f"<h1>Zeitmaschine: {esc(titel(o))}</h1>",
         f"<p class='sub'>Konto {o['konto']:.0f} $, Lot {o['lot']:g}, Kosten „{esc(o['szenario'])}“, "
         f"Daten: {esc(o.get('datenquelle', ''))}. Erstellt {esc(o['erstellt_utc'][:16].replace('T', ' '))} UTC. "
         f"Nur Rechnung auf vergangenen Kursen – es wurde nichts gehandelt.</p>"]
    P.append("<div class='verdict'><b>Urteil</b><ul>" + "".join(f"<li>{esc(u)}</li>" for u in o["urteil"]) +
             "</ul></div>")
    if not k.get("trades"):
        P.append("<p>Keine Trades im Zeitraum.</p></body></html>")
        return "".join(P)
    cls = lambda v: "pos" if (v or 0) > 0 else "neg"
    tiles = [("Trades", f"{k['trades']}", f"{k['von']} – {k['bis']}"),
             ("Gewinnrate", _pct(k["gewinnrate"]), f"{k['gewinner']} Gewinner, {k['verlierer']} Verlierer"),
             ("CRV", _num(k["crv"]), "Ø Gewinn / Ø Verlust"),
             ("Ergebnis", f"<span class='{cls(k['summe_usd'])}'>{_usd(k['summe_usd'])}</span>",
              f"{_num(k['erwartung_r'])} R je Trade"),
             ("Größter Rückgang", f"{_num(k['max_drawdown_pct'], 1)} %", f"{k['max_drawdown_usd']:.0f} $"),
             ("Ruin-Risiko", _pct(mc.get("p_ruin")), f"in {mc.get('wochen', '–')} Wochen")]
    P.append("<div class='tiles'>" + "".join(f"<div class='tile'><span>{a}</span><b>{b}</b><span>{c}</span></div>"
                                             for a, b, c in tiles) + "</div>")
    P.append("<h2>1. So wäre es wirklich gelaufen</h2><p class='small'>Kontostand nach jedem Trade, echte Kerzen des "
             "Brokers, nach Spread, Slippage, Kommission und Swap. Gestrichelt: Startkonto.</p>")
    P.append(_svg_line(k["kurve_zeit"], [("Konto", k["kurve"], "var(--acc)", "")], ref=o["konto"]))
    notes = [f"Risiko je Trade (Median): {_num(k['risiko_usd_median'])} $ = {_num(k['risiko_pct_median'], 1)} % vom Konto",
             f"Längste Verlustserie: {k['laengste_verlustserie']} Trades in Folge",
             f"Schlechtester Trade: {_num(k['schlechtester_trade_r'])} R"
             + (f" (Kurslücke über den Stop: {k['luecken_ueber_stop']}-mal)" if k["luecken_ueber_stop"] else "")]
    if k.get("dd_stopp_am"):
        notes.append(f"<b>Der Bot wäre am {k['dd_stopp_am']} in den Handelsstopp gegangen</b> "
                     f"({_num(k['dd_stopp_pct'], 0)} % Rückgang vom Höchststand).")
    if k.get("konto_leer_am"):
        notes.append(f"<b class='neg'>Das Konto wäre am {k['konto_leer_am']} leer gewesen.</b>")
    P.append("<ul>" + "".join(f"<li>{n}</li>" for n in notes) + "</ul>")

    P.append("<h2>2. Kosten: basis, konservativ, hart</h2><table><tr><th>Szenario</th><th>Trades</th>"
             "<th>Gewinnrate</th><th>CRV</th><th>Ergebnis</th><th>Rückgang</th></tr>")
    for n, kk in o["kennzahlen"].items():
        if kk.get("trades"):
            P.append(f"<tr><td>{esc(n)}</td><td>{kk['trades']}</td><td>{_pct(kk['gewinnrate'])}</td>"
                     f"<td>{_num(kk['crv'])}</td><td class='{cls(kk['summe_usd'])}'>{_usd(kk['summe_usd'])}</td>"
                     f"<td>{_num(kk['max_drawdown_pct'], 1)} %</td></tr>")
    P.append("</table><p class='small'>basis = echter Kerzen-Spread, kaum Slippage; konservativ = Spread × 1,5, "
             "Kommission wie Raw-Konto; hart = Spread × 2,5, viel Slippage (Nachrichten, hektische Märkte).</p>")
    if o.get("halbjahre"):
        P.append("<h2>3. Je Halbjahr</h2><table><tr><th>Zeitraum</th><th>Trades</th><th>Gewinnrate</th>"
                 "<th>Ergebnis</th></tr>")
        for h in o["halbjahre"]:
            P.append(f"<tr><td>{esc(h['zeitraum'])}</td><td>{h['trades']}</td><td>{_pct(h['gewinnrate'])}</td>"
                     f"<td class='{cls(h['summe_usd'])}'>{_usd(h['summe_usd'])}</td></tr>")
        P.append("</table>")
    if mc.get("laeufe"):
        f = mc["faecher"]
        P.append(f"<h2>4. Was hätte auch passieren können?</h2><p class='small'>{mc['laeufe']:,} neu zusammengesetzte "
                 f"Geschichten über {mc['wochen']} Wochen aus Blöcken von im Schnitt {mc['block_wochen']:g} "
                 f"aufeinanderfolgenden echten Wochen – ruhige und wilde Phasen bleiben zusammen. Linie = Mitte, "
                 f"Fläche = mittlere 50 %, gestrichelt = schlechte und gute 5 %.</p>".replace(",", "."))
        P.append(_svg_line([f"Trade {x}" for x in f["trade_nr"]],
                           [("Mitte", f["p50"], "var(--acc)", ""), ("schlecht 5 %", f["p5"], "var(--neg)", "5 4"),
                            ("gut 95 %", f["p95"], "var(--pos)", "5 4")], ref=o["konto"], band=(f["p25"], f["p75"])))
        e = mc["endkonto"]
        P.append("<p class='small'>Verteilung des Kontos nach "
                 f"{mc['wochen']} Wochen (rot = ruiniert, unter {mc['ruin_konto']:.0f} $):</p>")
        P.append(_svg_hist(mc["endkonten"], o["konto"], mc["ruin_konto"]))
        P.append("<table><tr><th></th><th>Wochen-Blöcke</th><th>einzelne Trades (zum Vergleich)</th></tr>")
        rows = [("Konto schlecht (5 %)", f"{e['5']:.0f} $", f"{mi['endkonto']['5']:.0f} $"),
                ("Konto Mitte", f"{e['50']:.0f} $", f"{mi['endkonto']['50']:.0f} $"),
                ("Konto gut (95 %)", f"{e['95']:.0f} $", f"{mi['endkonto']['95']:.0f} $"),
                ("im Plus", _pct(mc["im_plus"]), _pct(mi["im_plus"])),
                ("Rückgang ≥ 15 %", _pct(mc["p_rueckgang"]["15"]), _pct(mi["p_rueckgang"]["15"])),
                ("Rückgang ≥ 25 %", _pct(mc["p_rueckgang"]["25"]), _pct(mi["p_rueckgang"]["25"])),
                ("Rückgang ≥ 50 %", _pct(mc["p_rueckgang"]["50"]), _pct(mi["p_rueckgang"]["50"])),
                (f"Ruin (unter {mc['ruin_konto']:.0f} $)", _pct(mc["p_ruin"]), _pct(mi["p_ruin"]))]
        P.append("".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rows) + "</table>")
    if o.get("trades"):
        P.append("<details><summary>Alle Trades</summary><table><tr><th>Einstieg (UTC)</th><th>Richtung</th>"
                 "<th>Setup</th><th>Kurs</th><th>Stop</th><th>USD</th><th>R</th><th>Ausstieg</th></tr>")
        for t in o["trades"]:
            P.append(f"<tr><td>{esc(t['einstieg'])}</td><td>{t['richtung']}</td><td>{esc(t['setup'])}</td>"
                     f"<td>{t['kurs']}</td><td>{t['stop']}</td><td class='{cls(t['usd'])}'>{_num(t['usd'])}</td>"
                     f"<td>{_num(t['r'])}</td><td>{esc(t['grund'])}</td></tr>")
        P.append("</table></details>")
    P.append("<p class='small'>Einstellungen: " + esc(json.dumps(o["einstellungen"], ensure_ascii=False)) +
             ". Annahmen: Kerzen = Bid, Ask = Bid + Spread; berühren Stop und Ziel dieselbe Kerze, zählt der Stop; "
             "Lücken über den Stop werden zum Eröffnungskurs ausgeführt. Kein Finanzrat.</p></body></html>")
    return "".join(P)


def save(o: dict, folder: Path) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{o['strategie']}_{o['symbol']}_{o['erstellt_utc'][:10]}"
    p_html, p_json = folder / f"{stem}.html", folder / f"{stem}.json"
    p_html.write_text(html_page(o), encoding="utf-8")
    p_json.write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p_html, p_json
