"""Blockweiser und inkrementeller Import von Kerzendaten aus MT5.

Ablauf je Symbol und Zeitrahmen:
  1. Vorwärts: von der letzten gespeicherten Kerze bis jetzt (nur fehlende Daten).
  2. Rückwärts: vor der ersten gespeicherten Kerze blockweise weitersuchen, bis mehrere
     Blöcke hintereinander leer sind – so wird die *tatsächlich* verfügbare Historie ermittelt.
     Ist die Historie bereits als erschöpft markiert, wird nur noch ein Block geprüft
     (falls der Broker oder die Terminal-Einstellung inzwischen mehr liefert).

Blockgröße = chunk_bars Kerzen (Standard 40 000; Phase 1: 100 000 abgelehnt, 50 000 ok).
Lehnt MT5 einen Block trotzdem ab, wird er halbiert. Jeder Block wird sofort gespeichert,
ein Abbruch kann also beim nächsten Start einfach fortgesetzt werden.

Die gerade laufende (unfertige) Kerze wird nie gespeichert. Es werden keine Kurse erzeugt
oder aufgefüllt – gespeichert wird ausschließlich, was MT5 liefert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .storage import MarketStore, rates_to_frame
from .timeframes import seconds

INVALID_PARAMS = -2


@dataclass
class ImportResult:
    symbol: str
    tf: str
    forward_rows: int = 0
    backfill_rows: int = 0
    requests: int = 0
    rows_total: int = 0
    first_utc: str | None = None
    last_utc: str | None = None
    history_exhausted: bool = False
    split_requests: int = 0
    errors: list = field(default_factory=list)

    @property
    def new_rows(self) -> int:
        return self.forward_rows + self.backfill_rows


class Importer:
    def __init__(self, client, store: MarketStore, scheme, chunk_bars: int = 40_000,
                 earliest_utc: str = "2000-01-01", max_empty_chunks: int = 3,
                 now_utc_fn=None, min_chunk_bars: int = 500, log=print):
        self.client = client
        self.store = store
        self.scheme = scheme
        self.chunk_bars = chunk_bars
        self.max_empty_chunks = max_empty_chunks
        self.min_chunk_bars = min_chunk_bars
        self.now_utc_fn = now_utc_fn or (lambda: pd.Timestamp.now(tz="UTC"))
        self.earliest_server = int(scheme.to_server(pd.DatetimeIndex([pd.Timestamp(earliest_utc, tz="UTC")]))[0])
        self.log = log

    # ------------------------------------------------------------------ Hilfsfunktionen
    def now_server(self) -> int:
        return int(self.scheme.to_server(pd.DatetimeIndex([self.now_utc_fn()]))[0])

    def _fetch(self, res: ImportResult, symbol: str, tf: str, frm: int, to: int, retry_empty: bool = True):
        """Kerzen in [frm, to] holen; bei 'Invalid params' den Bereich halbieren."""
        rates = self.client.rates_range(symbol, tf, frm, to, retry_empty=retry_empty)
        res.requests += 1
        if rates is not None and len(rates) > 0:
            return [rates]
        err = self.client.last_error()
        code = err[0] if isinstance(err, (tuple, list)) else None
        span_bars = (to - frm) / seconds(tf)
        if code == INVALID_PARAMS and span_bars > self.min_chunk_bars:
            res.split_requests += 1
            mid = frm + (to - frm) // 2
            return (self._fetch(res, symbol, tf, frm, mid, retry_empty) +
                    self._fetch(res, symbol, tf, mid + 1, to, retry_empty))
        if code not in (None, 1, 0, -1) and code != INVALID_PARAMS:
            res.errors.append(f"{tf} {frm}-{to}: {err}")
        return []

    def _store(self, symbol: str, tf: str, chunks, now_server: int) -> int:
        added = 0
        tf_s = seconds(tf)
        for rates in chunks:
            rates = rates[rates["time"] + tf_s <= now_server]  # unfertige Kerze verwerfen
            if len(rates):
                added += self.store.upsert(symbol, tf, rates_to_frame(rates, self.scheme))
        return added

    # ------------------------------------------------------------------ Import
    def update(self, symbol: str, tf: str) -> ImportResult:
        res = ImportResult(symbol, tf)
        tf_s = seconds(tf)
        span = self.chunk_bars * tf_s
        now = self.now_server()
        meta = self.store.read_meta(symbol)
        tfmeta = meta.setdefault("timeframes", {}).setdefault(tf, {})
        bounds = self.store.bounds(symbol, tf)

        # 1) Vorwärts: fehlende Kerzen seit der letzten gespeicherten (inkl. dieser, falls korrigiert)
        if bounds is not None:
            begin = bounds["last_server"]
            while begin <= now:
                end = min(begin + span, now)
                # vorwärts ohne Wiederholung: leer heißt hier einfach "keine neuen Kerzen" (z. B. Wochenende)
                chunks = self._fetch(res, symbol, tf, begin, end, retry_empty=False)
                res.forward_rows += self._store(symbol, tf, chunks, now)
                begin = end + 1
            back_from = bounds["first_server"]
        else:
            back_from = now + tf_s

        # 2) Rückwärts: tatsächliche Historie ermitteln
        max_empty = 1 if tfmeta.get("history_exhausted") else self.max_empty_chunks
        empty = 0
        end = back_from
        exhausted = False
        while True:
            if end <= self.earliest_server:
                exhausted = True
                break
            begin = max(end - span, self.earliest_server)
            chunks = self._fetch(res, symbol, tf, begin, end - 1)
            if not chunks:
                empty += 1
                if empty >= max_empty:
                    exhausted = True
                    break
            else:
                empty = 0
                res.backfill_rows += self._store(symbol, tf, chunks, now)
            end = begin

        # Metadaten
        bounds = self.store.bounds(symbol, tf)
        res.history_exhausted = exhausted
        if bounds:
            first_utc = self.scheme.to_utc([bounds["first_server"]])[0]
            last_utc = self.scheme.to_utc([bounds["last_server"]])[0]
            res.rows_total = bounds["rows"]
            res.first_utc, res.last_utc = first_utc.isoformat(), last_utc.isoformat()
        tfmeta.update({
            "history_exhausted": exhausted,
            "first_utc": res.first_utc, "last_utc": res.last_utc, "rows": res.rows_total,
            "last_update_utc": self.now_utc_fn().isoformat(),
            "last_new_rows": res.new_rows,
        })
        meta["time_scheme"] = self.scheme.name
        self.store.write_meta(symbol, meta)
        return res

    def ensure_scheme(self, symbol: str, tfs: list[str]) -> bool:
        """Wurde mit anderem Zeitschema gespeichert, UTC-Zeiten neu berechnen. True = umgerechnet."""
        meta = self.store.read_meta(symbol)
        old = meta.get("time_scheme")
        if old is None or old == self.scheme.name:
            return False
        self.log(f"  Zeitschema geändert ({old} -> {self.scheme.name}): UTC-Zeiten von {symbol} werden neu berechnet")
        for tf in tfs:
            self.store.rewrite_utc(symbol, tf, self.scheme)
        meta["time_scheme"] = self.scheme.name
        self.store.write_meta(symbol, meta)
        return True
