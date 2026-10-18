"""Abgleich der Backtest-Berechnungen mit den Rechenfunktionen des MT5-Terminals.

order_calc_profit und order_calc_margin sind reine Rechenfunktionen – sie erzeugen keine Order.
Verglichen werden Gewinn (Kauf und Verkauf) und Margin für mehrere Volumina und Kursbewegungen
zum aktuellen Kurs, mit denselben Formeln wie im Backtest (instruments.RiskCalculator).
"""

from __future__ import annotations

from ..instruments import CurrencyConverter, InstrumentSpec, RiskCalculator, conversion_sources


def run(client, store) -> dict:
    doc = store.read_json("specs.json", {}) or {}
    acc = doc["account"]
    specs = {s: InstrumentSpec.from_dict(d) for s, d in doc.get("instruments", {}).items()}
    client.connect()
    try:
        conv = CurrencyConverter()
        for src in conversion_sources(doc):
            t = client.tick(src["symbol"])
            if t and t.get("bid"):
                conv.add_live(src["symbol"], src["base"], src["quote"], (t["bid"] + t["ask"]) / 2)
        out = {}
        for sym, spec in specs.items():
            tick = client.tick(sym) or {}
            ask, bid = tick.get("ask"), tick.get("bid")
            if not ask:
                out[sym] = []
                continue
            rc = RiskCalculator(spec, conv, acc["currency"], float(acc["leverage"]))
            rows = []
            for vol in sorted({spec.volume_min, 0.1, 1.0}):
                for d, move in ((1, 0.01), (-1, 0.0025), (1, -0.005)):
                    px0 = ask if d == 1 else bid
                    px1 = round(px0 * (1 + move), spec.digits)
                    own = float(rc.pnl(d, vol, px0, px1)[0])
                    mt5 = (client.calc_profit_buy if d == 1 else client.calc_profit_sell)(sym, vol, px0, px1)
                    m_own = float(rc.margin(vol, ask)[0])
                    m_mt5 = client.calc_margin_buy(sym, vol, ask)
                    rows.append({
                        "volume": vol, "move": f"{'Kauf' if d == 1 else 'Verkauf'} {move * 100:+.2f} %",
                        "profit_own": round(own, 2), "profit_mt5": mt5,
                        "profit_dev_pct": round(100 * (own / mt5 - 1), 3) if mt5 else None,
                        "margin_own": round(m_own, 2), "margin_mt5": m_mt5,
                        "margin_dev_pct": round(100 * (m_own / m_mt5 - 1), 3) if m_mt5 else None})
            out[sym] = rows
        return out
    finally:
        client.shutdown()
