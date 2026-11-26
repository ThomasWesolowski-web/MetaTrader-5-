"""Welche Strategien, Parameter, Zeitrahmen, Handelszeiten und Richtungen der Strategie-Finder durchrechnet.

Ein KANDIDAT ist genau eine Regel: Strategie + Parametersatz + Zeitrahmen + Handelszeit (UTC) + Richtung.
Ein SETUP ist das, wofür der Finder die beste Regel sucht: Zeitrahmen + Handelszeit (z. B. "M15 · London").
Je Setup wählt der Finder unter allen Strategien, Parametern und Richtungen aus – aber nur mit Daten, die zum
Zeitpunkt der Wahl schon vorlagen (Walk-Forward, siehe auswahl.py).

Die Parameterlisten sind bewusst grob (2–3 Werte je Stellschraube). Feinere Raster finden auf vergangenen Kursen
fast immer "bessere" Werte, die danach nicht mehr funktionieren (Kurvenanpassung).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from ..strategies.zusatz import SITZUNGEN

ZEITRAHMEN = ("M5", "M15", "M30", "H1", "H4")
RICHTUNGEN = {0: "beide", 1: "nur Kauf", -1: "nur Verkauf"}
SITZUNG_NAMEN = {"alle": "ganzer Tag", "asien": "Asien 0–7 UTC", "london": "London 7–12 UTC",
                 "new_york": "New York 12–17 UTC"}

_ALL = ZEITRAHMEN
_INTRA = ("M5", "M15", "M30", "H1")


def _grid(**axes) -> list[dict]:
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in product(*(axes[k] for k in keys))]


# Strategie -> (Zeitrahmen, Parameterliste, Kurzname, Familie)
GRIDS: dict[str, tuple[tuple[str, ...], list[dict], str, str]] = {
    # --- Trendfolge
    "ema_trend": (("M15", "M30", "H1", "H4"),
                  [{"fast": f, "slow": s, "atr_mult": m} for f, s in ((9, 21), (20, 50), (50, 200))
                   for m in (2.0, 3.0)], "EMA-Kreuzung", "Trend"),
    "macd_trend": (_ALL, [{"fast": f, "slow": s, "atr_mult": m} for f, s in ((12, 26), (24, 52)) for m in (1.5, 2.5)],
                   "MACD mit EMA-200-Filter", "Trend"),
    "supertrend": (("M15", "M30", "H1", "H4"), _grid(atr_n=[10, 20], mult=[2.0, 3.0]), "Supertrend", "Trend"),
    "sma_box_trend": (("M15", "M30", "H1"), [{}], "SMA-Box-Trend", "Trend"),
    "ema_vwap": (("M15", "M30", "H1", "H4"), _grid(atr_mult=[1.5, 2.5]), "EMA 20 + Wochen-VWAP", "Trend"),
    "ema_pullback": (_ALL, _grid(fast=[20, 50], rr=[1.5, 2.5]), "Pullback an die EMA im Trend", "Trend"),
    # --- Breakout
    "donchian_breakout": (("M15", "M30", "H1", "H4"), _grid(n=[20, 55, 100], atr_mult=[1.5, 2.5]),
                          "Donchian-/Turtle-Ausbruch", "Breakout"),
    "kombi_breakout_momentum": (("M15", "M30", "H1", "H4"), _grid(n=[20, 55], rr=[1.5, 3.0]),
                                "Ausbruch + Momentum", "Breakout"),
    "bollinger_squeeze": (_ALL, _grid(n=[20], lookback=[60, 120], rr=[1.5, 2.5]), "Bollinger-Squeeze",
                          "Breakout"),
    "vortag_breakout": (_INTRA, _grid(rr=[1.5, 3.0], atr_mult=[1.0, 2.0]), "Vortageshoch/-tief-Ausbruch",
                        "Breakout"),
    "inside_bar": (("M15", "M30", "H1", "H4"), _grid(rr=[1.5, 2.5], trend=[50, 200]), "Inside-Bar-Ausbruch",
                   "Breakout"),
    "momentum": (_ALL, _grid(n=[12, 48], z=[1.0, 1.75]), "Momentum-Schub", "Breakout"),
    "opening_range_breakout": (("M5", "M15"),
                               _grid(session=["london", "new_york"], range_min=[15, 30, 60], rr=[1.5, 2.0])
                               + _grid(session=["utc"], range_min=[420], rr=[1.5, 2.0]),
                               "Opening Range Breakout (auch Asien-Range)", "Breakout/ORB"),
    # --- Mean Reversion
    "rsi_mean_reversion": (_ALL, [{"rsi_n": n, "lower": lo, "upper": up, "atr_mult": m}
                                  for n, lo, up in ((2, 10.0, 90.0), (14, 30.0, 70.0)) for m in (1.5, 3.0)],
                           "RSI-Umkehr", "Mean Reversion"),
    "kombi_trend_rsi": (_ALL, _grid(lower=[5.0, 15.0], atr_mult=[1.5, 3.0]), "RSI(2)-Rücksetzer im Trend",
                        "Mean Reversion"),
    "bollinger_reversion": (_ALL, _grid(n=[20, 50], k=[2.0, 2.5]), "Bollinger-Umkehr", "Mean Reversion"),
    # --- Smart Money / Order Blocks
    "smc_orderblock": (_INTRA, _grid(k=[3, 5], rr=[1.5, 2.0, 3.0]), "Order Block nach Strukturbruch",
                       "Order Blocks/SMC"),
    "fvg_retrace": (_INTRA, _grid(min_fvg=[0.3, 0.6], rr=[1.5, 2.5]), "Fair Value Gap", "Order Blocks/SMC"),
    "ict_silver_bullet": (("M5", "M15"), _grid(rr=[1.5, 2.5], min_fvg=[0.1, 0.3]), "ICT Silver Bullet",
                          "Order Blocks/SMC"),
    "liquidity_sweep": (_ALL, _grid(k=[3, 5], rr=[1.5, 2.5]), "Liquidity Sweep", "Order Blocks/SMC"),
    "mss": (_ALL, _grid(k=[3, 5], rr=[2.0, 3.0]), "Market Structure Shift", "Order Blocks/SMC"),
    "kombi_sweep_mss": (_INTRA, _grid(k=[3, 5], rr=[2.0, 3.0]), "Sweep + Strukturwechsel", "Order Blocks/SMC"),
}

# Der Gold-Bot (eigene Order-Block-Regeln, tradingbot/goldbot) läuft gesondert: Kerze für Kerze wie live, M15
GOLDBOT = "goldbot"
GOLDBOT_NAME = "Gold-Bot (Order Blocks, wie live)"


@dataclass(frozen=True)
class Kandidat:
    strategie: str
    params: tuple                 # sortierte (Name, Wert)-Paare, damit hashbar
    tf: str
    sitzung: str = "alle"
    richtung: int = 0

    @property
    def p(self) -> dict:
        return dict(self.params)

    @property
    def setup(self) -> tuple[str, str]:
        return (self.tf, self.sitzung)

    @property
    def key(self) -> str:
        ps = ",".join(f"{k}={v}" for k, v in self.params)
        return f"{self.tf}|{self.sitzung}|{self.richtung:+d}|{self.strategie}({ps})"

    def name(self) -> str:
        kurz = GOLDBOT_NAME if self.strategie == GOLDBOT else GRIDS.get(self.strategie, (0, 0, self.strategie))[2]
        ps = ", ".join(f"{k} {v}" for k, v in self.params)
        return f"{kurz}" + (f" ({ps})" if ps else "")

    def beschreibung(self) -> str:
        return (f"{self.name()} – {self.tf}, {SITZUNG_NAMEN.get(self.sitzung, self.sitzung)}, "
                f"{RICHTUNGEN[self.richtung]}")

    def familie(self) -> str:
        return "Order Blocks/SMC" if self.strategie == GOLDBOT else GRIDS[self.strategie][3]

    def demo_eintrag(self, symbol: str = "XAUUSD") -> dict:
        """Eintrag für [[paper.candidates]] in config.toml (Strategie finder_gefiltert)."""
        von, bis = SITZUNGEN[self.sitzung]
        return {"symbol": symbol, "timeframe": self.tf, "strategy": "finder_gefiltert",
                "params": {"basis": self.strategie, "basis_params": self.p, "von_utc": von, "bis_utc": bis,
                           "richtung": self.richtung}}


def freeze(params: dict) -> tuple:
    return tuple(sorted(params.items()))


def alle_kandidaten(zeitrahmen=ZEITRAHMEN, sitzungen=tuple(SITZUNGEN), richtungen=tuple(RICHTUNGEN),
                    strategien=None, mit_goldbot: bool = True) -> list[Kandidat]:
    out = []
    for name, (tfs, grid, _, _) in GRIDS.items():
        if strategien and name not in strategien:
            continue
        for tf in tfs:
            if tf not in zeitrahmen:
                continue
            for params, s, r in product(grid, sitzungen, richtungen):
                out.append(Kandidat(name, freeze(params), tf, s, r))
    if mit_goldbot and "M15" in zeitrahmen and (not strategien or GOLDBOT in strategien):
        out += [Kandidat(GOLDBOT, (), "M15", s, r) for s, r in product(sitzungen, richtungen)]
    return out


def anzahl_regeln() -> dict:
    """Wie viele Grundregeln (ohne Zeit/Richtung) je Familie – für den Bericht."""
    fam: dict[str, int] = {}
    for name, (tfs, grid, _, f) in GRIDS.items():
        fam[f] = fam.get(f, 0) + len(tfs) * len(grid)
    fam["Order Blocks/SMC"] = fam.get("Order Blocks/SMC", 0) + 1
    return fam
