"""Kerzen und Kontraktdaten für die Zeitmaschine – NUR LESEND.

Quelle 1: die lokale Datenbank data/market (gefüllt von daten_aktualisieren.py, echte MT5-Kerzen des Brokers).
Quelle 2 (wenn das Symbol/der Zeitrahmen dort fehlt oder --mt5 angegeben ist): direkt aus dem offenen MT5-Terminal
über MT5Client.rates_from_pos – das ist eine reine Kursabfrage, es wird nichts gehandelt.
Fehlt M15, aber M5 ist da, wird M15 aus M5 zusammengesetzt.
"""

from __future__ import annotations

import pandas as pd

from ..instruments import InstrumentSpec
from ..server_time import parse_scheme
from ..storage import MarketStore, rates_to_frame
from ..timeframes import seconds

COLS = ["time", "time_server", "open", "high", "low", "close", "spread"]

# Ersatz, wenn weder MT5 noch specs.json Kontraktdaten liefern (Werte wie bei Vantage/STARTRADER üblich)
FALLBACK_SPECS = {
    "EURUSD": dict(symbol="EURUSD", description="Euro vs US Dollar", currency_base="EUR", currency_profit="USD",
                   currency_margin="EUR", digits=5, point=0.00001, contract_size=100000.0, tick_size=0.00001,
                   tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=0,
                   freeze_level=0, calc_mode="FOREX", swap_long=-7.0, swap_short=2.0, swap_mode=1, margin_rate=1.0,
                   swap_rollover3days=3),
}


class DataSource:
    def __init__(self, data_root, client=None, use_mt5: bool = False, log=print):
        self.store = MarketStore(data_root)
        self.doc = self.store.read_json("specs.json", {}) or {}
        self.client = client
        self.use_mt5 = use_mt5
        self.log = log
        ts = (self.doc.get("time_scheme") or {}).get("scheme")
        self.scheme = parse_scheme(ts) if ts else None
        self.source = {}

    # ------------------------------------------------------------------ Kerzen
    def bars(self, symbol: str, tf: str, start=None, end=None) -> pd.DataFrame:
        df = self._load(symbol, tf)
        if df.empty and tf != "M5" and seconds(tf) % 300 == 0:
            m5 = self._load(symbol, "M5")
            if not m5.empty:
                self.log(f"  {symbol} {tf}: nicht in der Datenbank – aus M5-Kerzen zusammengesetzt")
                df = resample(m5, tf)
                self.source[(symbol, tf)] = self.source.get((symbol, "M5"), "") + " (aus M5)"
        if df.empty:
            raise RuntimeError(f"Keine Kerzen für {symbol} {tf}: MT5 öffnen und eingeloggt lassen "
                               f"(oder python daten_aktualisieren.py ausführen)")
        t = pd.DatetimeIndex(df["time"])
        if start is not None:
            df = df[t >= pd.Timestamp(start)]
            t = pd.DatetimeIndex(df["time"])
        if end is not None:
            df = df[t < pd.Timestamp(end)]
        return df.reset_index(drop=True)

    def _load(self, symbol: str, tf: str) -> pd.DataFrame:
        parts, src = [], []
        try:
            df = self.store.load(symbol, tf)
        except Exception:                                            # noqa: BLE001 – fehlender Ordner
            df = pd.DataFrame()
        if not df.empty:
            parts.append(df[[c for c in COLS if c in df.columns]])
            src.append("Datenbank")
        if self.client is not None and (self.use_mt5 or df.empty):
            live = self._from_mt5(symbol, tf)
            if live is not None and not live.empty:
                parts.append(live)
                src.append("MT5")
        if not parts:
            return pd.DataFrame(columns=COLS)
        out = pd.concat(parts, ignore_index=True)
        out["time"] = pd.DatetimeIndex(out["time"]).as_unit("ns")
        out = out.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
        if "spread" not in out:
            out["spread"] = 0
        # laufende (noch nicht abgeschlossene) Kerze weglassen
        end = pd.DatetimeIndex(out["time"]) + pd.Timedelta(seconds=seconds(tf))
        out = out[end <= pd.Timestamp.now(tz="UTC")].reset_index(drop=True)
        self.source[(symbol, tf)] = " + ".join(src)
        return out

    def _from_mt5(self, symbol: str, tf: str) -> pd.DataFrame | None:
        if self.scheme is None:
            self.log("  MT5: Zeitschema unbekannt – bitte einmal python daten_aktualisieren.py ausführen")
            return None
        try:
            self.client.connect()
            self.client.symbol_info(symbol)                       # Symbol in der Marktübersicht einblenden
            maxbars = int((self.client.terminal_info() or {}).get("maxbars") or 100_000)
            r = self.client.rates_from_pos(symbol, tf, 1, min(maxbars, 100_000))
        except Exception as e:                                       # noqa: BLE001
            self.log(f"  MT5 nicht erreichbar ({e}) – nur Datenbank")
            return None
        if r is None or not len(r):
            self.log(f"  MT5 liefert keine Kerzen für {symbol} {tf} (Name beim Broker prüfen, z. B. XAUUSD.r)")
            return None
        return rates_to_frame(r, self.scheme)[COLS]

    # ------------------------------------------------------------------ Kontraktdaten
    def spec(self, symbol: str) -> InstrumentSpec:
        if self.client is not None:
            try:
                self.client.connect()
                info = self.client.symbol_info(symbol)
                if info:
                    return InstrumentSpec.from_mt5(info)
            except Exception:                                        # noqa: BLE001
                pass
        d = (self.doc.get("instruments") or {}).get(symbol) or FALLBACK_SPECS.get(symbol)
        if not d:
            raise RuntimeError(f"Keine Kontraktdaten für {symbol}: MT5 öffnen oder daten_aktualisieren.py ausführen")
        return InstrumentSpec.from_dict(d)

    @property
    def account_currency(self) -> str:
        return (self.doc.get("account") or {}).get("currency", "USD")

    @property
    def leverage(self) -> float:
        return float((self.doc.get("account") or {}).get("leverage") or 100)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Kerzen zu einem größeren Zeitrahmen zusammenfassen (nur vollständige Zeitfenster mit mind. einer Kerze)."""
    rule = f"{seconds(tf)}s"
    x = df.set_index(pd.DatetimeIndex(df["time"]))
    g = x.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"time_server": g["time_server"].first(), "open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(), "spread": g["spread"].min()})
    out = out.dropna(subset=["open"])
    out["time_server"] = out["time_server"] - (out["time_server"] % seconds(tf))
    out = out.reset_index().rename(columns={"index": "time"})
    out["time"] = pd.DatetimeIndex(out["time"]).as_unit("ns")
    out["time_server"] = out["time_server"].astype("int64")
    return out[COLS]
