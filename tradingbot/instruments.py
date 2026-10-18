"""Kontraktspezifikationen, Währungsumrechnung sowie Gewinn-, Verlust- und Risikoberechnung.

Grundlagen (MT5-Berechnungsmodi, SYMBOL_CALC_MODE_*):
    FOREX / CFD / CFDLEVERAGE / CFDINDEX:
        Gewinn (Gewinnwährung) = Richtung × (Ausstieg − Einstieg) × Kontraktgröße × Volumen
    Margin (Marginwährung):
        FOREX               Volumen × Kontraktgröße / Hebel × Marginsatz
        FOREX_NO_LEVERAGE   Volumen × Kontraktgröße × Marginsatz
        CFD                 Volumen × Kontraktgröße × Preis × Marginsatz
        CFDLEVERAGE         Volumen × Kontraktgröße × Preis / Hebel × Marginsatz
        CFDINDEX            Volumen × Kontraktgröße × Preis × Tickpreis / Tickgröße × Marginsatz

Der Marginsatz je Symbol ist über die Python-API nicht direkt lesbar. Er wird beim
Datenabgleich mit dem Terminal kalibriert (order_calc_margin, reine Rechenfunktion).

Umrechnung Gewinnwährung -> Kontowährung: mit dem Schlusskurs des Umrechnungssymbols
(z. B. EURUSD) zum letzten *abgeschlossenen* Bar vor dem Zeitpunkt – kein Blick in die Zukunft.
MT5 rechnet live mit Bid/Ask; die Abweichung liegt in der Größenordnung des Spreads
des Umrechnungssymbols (EURUSD ~0,01 %).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .timeframes import seconds

CALC_MODES = {0: "FOREX", 1: "FUTURES", 2: "CFD", 3: "CFDINDEX", 4: "CFDLEVERAGE", 5: "FOREX_NO_LEVERAGE",
              32: "EXCH_STOCKS", 33: "EXCH_FUTURES", 34: "EXCH_FUTURES_FORTS"}
PRICE_DIFF_MODES = {"FOREX", "FOREX_NO_LEVERAGE", "CFD", "CFDINDEX", "CFDLEVERAGE", "EXCH_STOCKS"}


@dataclass
class InstrumentSpec:
    symbol: str
    description: str
    currency_base: str
    currency_profit: str
    currency_margin: str
    digits: int
    point: float
    contract_size: float
    tick_size: float
    tick_value: float          # Wert eines Ticks je 1 Lot in Kontowährung (MT5, zum Abrufzeitpunkt)
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int
    calc_mode: str
    swap_long: float
    swap_short: float
    swap_mode: int
    margin_rate: float = 1.0   # kalibriert gegen MT5 (order_calc_margin)
    path: str = ""
    swap_rollover3days: int = 3  # Wochentag (MT5: 0 = Sonntag) mit dreifachem Swap

    @classmethod
    def from_mt5(cls, info: dict) -> "InstrumentSpec":
        return cls(
            symbol=info["name"], description=info.get("description", ""),
            currency_base=info.get("currency_base", ""), currency_profit=info.get("currency_profit", ""),
            currency_margin=info.get("currency_margin", ""), digits=int(info["digits"]),
            point=float(info["point"]), contract_size=float(info["trade_contract_size"]),
            tick_size=float(info.get("trade_tick_size") or info["point"]),
            tick_value=float(info.get("trade_tick_value", 0.0)),
            volume_min=float(info["volume_min"]), volume_max=float(info["volume_max"]),
            volume_step=float(info["volume_step"]), stops_level=int(info.get("trade_stops_level", 0)),
            freeze_level=int(info.get("trade_freeze_level", 0)),
            calc_mode=CALC_MODES.get(int(info.get("trade_calc_mode", 2)), str(info.get("trade_calc_mode"))),
            swap_long=float(info.get("swap_long", 0.0)), swap_short=float(info.get("swap_short", 0.0)),
            swap_mode=int(info.get("swap_mode", 0)), path=info.get("path", ""),
            swap_rollover3days=int(info.get("swap_rollover3days", 3)),
        )

    @property
    def margin_result_currency(self) -> str:
        """Währung, in der die Margin-Formel ihr Ergebnis liefert.

        Formeln mit Preis (CFD, CFDLEVERAGE, CFDINDEX, Aktien) rechnen in Preiseinheiten,
        also in der Gewinnwährung (z. B. USD bei BTCUSD) – auch wenn MT5 als Marginwährung
        die Basiswährung (BTC) angibt. Nur Forex-Formeln rechnen in der Basis-/Marginwährung."""
        return self.currency_margin if self.calc_mode in ("FOREX", "FOREX_NO_LEVERAGE") else self.currency_profit

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "InstrumentSpec":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ---------------------------------------------------------------------- Volumen
def round_volume_down(spec: InstrumentSpec, volume: float) -> float:
    """Auf das Volumenraster abrunden (nie aufrunden – sonst steigt das Risiko). 0, wenn unter Minimum."""
    step = spec.volume_step
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    v = math.floor(volume / step + 1e-9) * step
    v = round(min(v, spec.volume_max), decimals)
    return v if v >= spec.volume_min - 1e-12 else 0.0


# ---------------------------------------------------------------------- Gewinn/Margin in Symbolwährung
def pnl_profit_currency(spec: InstrumentSpec, direction: int, volume: float, entry, exit_):
    if spec.calc_mode not in PRICE_DIFF_MODES:
        raise NotImplementedError(f"Berechnungsmodus {spec.calc_mode} wird nicht unterstützt")
    return direction * (np.asarray(exit_) - np.asarray(entry)) * spec.contract_size * volume


def margin_margin_currency(spec: InstrumentSpec, volume: float, price, leverage: float):
    m, cs, rate = spec.calc_mode, spec.contract_size, spec.margin_rate
    price = np.asarray(price, dtype="float64")
    if m == "FOREX":
        return volume * cs / leverage * rate + 0 * price
    if m == "FOREX_NO_LEVERAGE":
        return volume * cs * rate + 0 * price
    if m == "CFD":
        return volume * cs * price * rate
    if m == "CFDLEVERAGE":
        return volume * cs * price / leverage * rate
    if m == "CFDINDEX":
        tick_price = spec.tick_value  # Näherung; CFDINDEX kommt bei XAUUSD/BTCUSD nicht vor
        return volume * cs * price * tick_price / spec.tick_size * rate
    raise NotImplementedError(f"Berechnungsmodus {m} wird nicht unterstützt")


# ---------------------------------------------------------------------- Währungsumrechnung
class CurrencyConverter:
    """Umrechnung zwischen Währungen anhand historischer Kurse eines Umrechnungssymbols.

    rates: {symbol: (zeit_ns_utc, schlusskurs, basis, notiz)} – Zeitpunkt = Ende des Bars,
    an dem der Schlusskurs feststand."""

    def __init__(self, rates: dict | None = None):
        self.rates = rates or {}

    @classmethod
    def from_store(cls, store, symbols: list[dict], timeframes=("M5", "H1", "D1"), start=None) -> "CurrencyConverter":
        """Kurse aus der Datenbank; start begrenzt das Laden auf den benötigten Zeitraum (ein Monat Vorlauf,
        damit zu Beginn ein zurückliegender Kurs verfügbar ist)."""
        rates = {}
        frm = None if start is None else pd.Timestamp(start) - pd.Timedelta(days=31)
        for s in symbols:
            parts = []
            for tf in [t for t in store.timeframes(s["symbol"]) if t in timeframes]:
                df = store.load(s["symbol"], tf, start=frm, columns=["close"])
                if not df.empty:
                    end = pd.DatetimeIndex(df["time"]).as_unit("ns").asi8 + seconds(tf) * 10**9
                    parts.append(pd.DataFrame({"t": end, "c": df["close"].to_numpy()}))
            if parts:
                all_ = pd.concat(parts).drop_duplicates("t", keep="last").sort_values("t")
                rates[s["symbol"]] = (all_["t"].to_numpy(), all_["c"].to_numpy(), s["base"], s["quote"])
        return cls(rates)

    def add_live(self, symbol: str, base: str, quote: str, price: float):
        """Einzelner aktueller Kurs (z. B. für Live-Prüfungen)."""
        self.rates[symbol] = (np.array([0], dtype="int64"), np.array([price]), base, quote)

    def _direct(self, from_ccy: str, to_ccy: str, times_ns: np.ndarray) -> np.ndarray | None:
        for t, c, base, quote in self.rates.values():
            if (base, quote) in ((from_ccy, to_ccy), (to_ccy, from_ccy)):
                i = np.searchsorted(t, times_ns, side="right") - 1
                px = np.where(i >= 0, c[np.maximum(i, 0)], np.nan)
                return px if base == from_ccy else 1.0 / px
        return None

    def currencies(self) -> set[str]:
        return {x for _, _, b, q in self.rates.values() for x in (b, q)}

    def can_convert(self, from_ccy: str, to_ccy: str) -> bool:
        try:
            self.rate(from_ccy, to_ccy, [pd.Timestamp.max.tz_localize("UTC")])
            return True
        except KeyError:
            return False

    def rate(self, from_ccy: str, to_ccy: str, times=None) -> np.ndarray:
        """Faktor, mit dem ein Betrag in from_ccy multipliziert wird, um to_ccy zu erhalten.
        Direkt über ein Währungspaar oder über eine Zwischenwährung (z. B. BTC -> USD -> EUR
        mit BTCUSD und EURUSD). Vor dem ersten verfügbaren Kurs: NaN (es wird kein Kurs erfunden)."""
        times_ns = (np.array([np.iinfo("int64").max]) if times is None
                    else pd.DatetimeIndex(np.atleast_1d(times)).as_unit("ns").asi8)
        if from_ccy == to_ccy:
            return np.ones(len(times_ns))
        out = self._direct(from_ccy, to_ccy, times_ns)
        if out is not None and not np.isnan(out).any():
            return out
        # Direktkurs fehlt ganz oder für einen Teil der Zeit (z. B. BTCEUR erst ab 2021):
        # Lücken über Zwischenwährungen füllen (BTC -> USD -> EUR mit BTCUSD und EURUSD)
        for via in sorted(self.currencies() - {from_ccy, to_ccy}):
            a = self._direct(from_ccy, via, times_ns)
            if a is None:
                continue
            b = self._direct(via, to_ccy, times_ns)
            if b is None:
                continue
            cross = a * b
            out = cross if out is None else np.where(np.isnan(out), cross, out)
            if not np.isnan(out).any():
                break
        if out is None:
            raise KeyError(f"Kein Umrechnungskurs {from_ccy}->{to_ccy} verfügbar")
        return out


# ---------------------------------------------------------------------- Risiko in Kontowährung
class RiskCalculator:
    def __init__(self, spec: InstrumentSpec, converter: CurrencyConverter, account_currency: str,
                 leverage: float):
        self.spec, self.conv, self.acc, self.leverage = spec, converter, account_currency, leverage

    def _to_acc(self, amount, ccy, times):
        return np.asarray(amount) * self.conv.rate(ccy, self.acc, times)

    def pnl(self, direction: int, volume: float, entry, exit_, times=None):
        """Gewinn/Verlust in Kontowährung (ohne Swap und Kommission)."""
        p = pnl_profit_currency(self.spec, direction, volume, entry, exit_)
        return self._to_acc(p, self.spec.currency_profit, times)

    def value_per_price_unit(self, volume: float, times=None):
        """Kontowährung je 1,0 Kursänderung (z. B. 1 USD bei Gold) für das Volumen."""
        return self._to_acc(self.spec.contract_size * volume, self.spec.currency_profit, times)

    def spread_cost(self, volume: float, spread_points, times=None):
        return self.value_per_price_unit(volume, times) * np.asarray(spread_points) * self.spec.point

    def margin(self, volume: float, price, times=None):
        m = margin_margin_currency(self.spec, volume, price, self.leverage)
        return self._to_acc(m, self.spec.margin_result_currency, times)

    def position_size(self, risk_amount: float, stop_distance: float, time=None) -> float:
        """Volumen, bei dem ein Stop im Abstand stop_distance (Preiseinheiten) höchstens
        risk_amount (Kontowährung) kostet. Abgerundet auf das Volumenraster; 0 = nicht möglich."""
        if stop_distance <= 0 or risk_amount <= 0:
            return 0.0
        per_lot = float(np.atleast_1d(self.value_per_price_unit(1.0, None if time is None else [time]))[0])
        if not np.isfinite(per_lot) or per_lot <= 0:
            return 0.0
        return round_volume_down(self.spec, risk_amount / (stop_distance * per_lot))


def conversion_sources(specs_doc: dict) -> list[dict]:
    """Kursquellen für die Umrechnung: Umrechnungssymbole und die gehandelten Instrumente selbst
    (BTCUSD liefert BTC->USD, XAUUSD liefert XAU->USD)."""
    out = list(specs_doc.get("conversions", []))
    for sym, d in specs_doc.get("instruments", {}).items():
        if d.get("currency_base") and d.get("currency_profit") and d["currency_base"] != d["currency_profit"]:
            out.append({"symbol": sym, "base": d["currency_base"], "quote": d["currency_profit"]})
    return out


def find_conversion_symbol(symbols: list[dict], ccy_a: str, ccy_b: str) -> dict | None:
    """Devisensymbol für das Währungspaar ccy_a/ccy_b suchen (z. B. EURUSD für EUR und USD)."""
    best = None
    for s in symbols:
        pair = (s.get("currency_base"), s.get("currency_profit"))
        if pair not in ((ccy_a, ccy_b), (ccy_b, ccy_a)):
            continue
        path = (s.get("path") or "").upper()
        if any(w in path for w in ("STOCK", "ETF", "INDEX", "FUTURE")):
            continue
        score = (s["name"].upper() == pair[0] + pair[1], s.get("visible", False), -len(s["name"]))
        if best is None or score > best[0]:
            best = (score, s)
    if best is None:
        return None
    s = best[1]
    return {"symbol": s["name"], "base": s["currency_base"], "quote": s["currency_profit"]}
