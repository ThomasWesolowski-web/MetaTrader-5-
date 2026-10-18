"""Lokale Datenbank für Kerzendaten im Parquet-Format.

Aufbau:
    <root>/market/<SYMBOL>/<TF>/<JAHR>.parquet   Kerzen, je UTC-Kalenderjahr eine Datei
    <root>/market/<SYMBOL>/_meta.json            Importstatus je Zeitrahmen
    <root>/specs.json                            Kontraktspezifikationen, Zeitschema, Validierung

Spalten: time (UTC, ns), time_server (MT5-Serverzeit, Epoch-Sekunden), open, high, low,
close, tick_volume, spread, real_volume. time_server wird unverändert aufbewahrt, damit UTC
bei einem anderen Zeitschema jederzeit neu berechnet werden kann.

Schreibvorgänge sind atomar (temporäre Datei + Umbenennen), damit ein Abbruch keine
halb geschriebenen Dateien hinterlässt. Nur das betroffene Jahr wird neu geschrieben.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

COLUMNS = ["time", "time_server", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
DTYPES = {"time_server": "int64", "open": "float64", "high": "float64", "low": "float64",
          "close": "float64", "tick_volume": "int64", "spread": "int32", "real_volume": "int64"}


def rates_to_frame(rates: np.ndarray, scheme) -> pd.DataFrame:
    """Strukturiertes MT5-Array -> DataFrame mit UTC-Zeit."""
    df = pd.DataFrame({
        "time_server": rates["time"].astype("int64"),
        "open": rates["open"], "high": rates["high"], "low": rates["low"], "close": rates["close"],
        "tick_volume": rates["tick_volume"].astype("int64"),
        "spread": rates["spread"].astype("int32"),
        "real_volume": rates["real_volume"].astype("int64"),
    })
    df.insert(0, "time", scheme.to_utc(df["time_server"].to_numpy()))
    return df


def _atomic_write_bytes(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    writer(tmp)
    os.replace(tmp, path)


class MarketStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.market = self.root / "market"

    # ------------------------------------------------------------------ Pfade
    def _dir(self, symbol: str, tf: str) -> Path:
        return self.market / symbol / tf

    def _files(self, symbol: str, tf: str) -> list[Path]:
        d = self._dir(symbol, tf)
        return sorted(d.glob("[0-9][0-9][0-9][0-9].parquet")) if d.exists() else []

    def symbols(self) -> list[str]:
        return sorted(p.name for p in self.market.iterdir() if p.is_dir()) if self.market.exists() else []

    def timeframes(self, symbol: str) -> list[str]:
        d = self.market / symbol
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []

    # ------------------------------------------------------------------ Schreiben
    def upsert(self, symbol: str, tf: str, df: pd.DataFrame) -> int:
        """Kerzen einfügen/aktualisieren (Schlüssel: time_server). Gibt Anzahl neuer Kerzen zurück."""
        if df is None or df.empty:
            return 0
        df = df[COLUMNS].astype(DTYPES)
        df["time"] = df["time"].dt.as_unit("ns")  # einheitliche Auflösung (pandas 3 nutzt sonst teils µs/s)
        added = 0
        for year, part in df.groupby(df["time"].dt.year):
            path = self._dir(symbol, tf) / f"{year}.parquet"
            if path.exists():
                old = pd.read_parquet(path)
                before = len(old)
                merged = pd.concat([old, part], ignore_index=True)
            else:
                before = 0
                merged = part
            merged = (merged.drop_duplicates("time_server", keep="last")
                      .sort_values("time_server").reset_index(drop=True))
            added += len(merged) - before
            _atomic_write_bytes(path, lambda p, m=merged: m.to_parquet(p, index=False))
        return added

    def rewrite_utc(self, symbol: str, tf: str, scheme) -> None:
        """UTC-Zeiten aus time_server mit neuem Zeitschema neu berechnen und neu partitionieren."""
        df = self.load(symbol, tf)
        if df.empty:
            return
        for f in self._files(symbol, tf):
            f.unlink()
        df["time"] = scheme.to_utc(df["time_server"].to_numpy())
        self.upsert(symbol, tf, df)

    # ------------------------------------------------------------------ Lesen
    def load(self, symbol: str, tf: str, start=None, end=None, columns: list[str] | None = None) -> pd.DataFrame:
        """Kerzen im UTC-Zeitraum [start, end) laden (sortiert nach Zeit).

        Speichersparend: Es werden nur die Jahresdateien im Zeitraum und nur die angegebenen Spalten gelesen
        ("time" und "time_server" werden für Sortierung und Filter immer mitgelesen)."""
        cols = None
        if columns is not None:
            cols = ["time", "time_server"] + [c for c in columns if c not in ("time", "time_server")]
        files = self._files(symbol, tf)
        if start is not None:
            start = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
            files = [f for f in files if int(f.stem) >= start.year]
        if end is not None:
            end = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)
            files = [f for f in files if int(f.stem) <= end.year]
        if not files:
            return pd.DataFrame({c: pd.Series(dtype=DTYPES.get(c, "datetime64[ns, UTC]")) for c in (cols or COLUMNS)})
        parts = [pd.read_parquet(f, columns=cols) for f in files]
        df = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)
        del parts
        df = df.sort_values("time_server").reset_index(drop=True)
        if start is not None:
            df = df[df["time"] >= start]
        if end is not None:
            df = df[df["time"] < end]
        return df.reset_index(drop=True)

    def bounds(self, symbol: str, tf: str) -> dict | None:
        """Erste/letzte Serverzeit und Anzahl Kerzen – ohne alle Daten zu laden."""
        files = self._files(symbol, tf)
        if not files:
            return None
        first = pd.read_parquet(files[0], columns=["time_server"])["time_server"]
        last = pd.read_parquet(files[-1], columns=["time_server"])["time_server"]
        rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        return {"first_server": int(first.min()), "last_server": int(last.max()), "rows": int(rows)}

    # ------------------------------------------------------------------ Metadaten
    def _meta_path(self, symbol: str) -> Path:
        return self.market / symbol / "_meta.json"

    def read_meta(self, symbol: str) -> dict:
        p = self._meta_path(symbol)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def write_meta(self, symbol: str, meta: dict) -> None:
        self.write_json(self._meta_path(symbol), meta)

    def write_json(self, path: str | Path, obj) -> None:
        path = Path(path) if Path(path).is_absolute() else self.root / path
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        _atomic_write_bytes(path, lambda p: p.write_text(text, encoding="utf-8"))

    def read_json(self, path: str | Path, default=None):
        path = Path(path) if Path(path).is_absolute() else self.root / path
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
