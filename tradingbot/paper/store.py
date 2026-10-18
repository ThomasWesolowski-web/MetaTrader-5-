"""Ablage der Beobachtungsdaten (Paper-Trading) unter <data>/paper/.

state.json          Fortschritt je Kandidat (Beobachtungsbeginn, letzte Kerze, angenommene Signale, eingefrorene Trades)
ereignisse.jsonl    Protokoll, nur angehängt (Signale, Ein-/Ausstiege, Lücken, Kursausfälle, Abweichungen, Fehler)
trades.json         aktueller Stand aller Signale und Trades mit Kosten und Kennzahlen (für das Dashboard)
status.json         letzter Durchlauf: Uhrzeit, Tick, Marktzustand, Arbeitsspeicher
kontraktpruefung.json  Kontraktspezifikationen und Stop-Grenzen bei Mindestlot (Punkt 3)

JSON-Dateien werden atomar geschrieben (temporäre Datei + Umbenennen); ein Abbruch hinterlässt keine halben Dateien.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

STATE, EVENTS, TRADES, STATUS, CONTRACT = ("state.json", "ereignisse.jsonl", "trades.json", "status.json",
                                           "kontraktpruefung.json")


class PaperStore:
    def __init__(self, data_root: str | Path):
        self.dir = Path(data_root) / "paper"

    def _write(self, name: str, obj) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def read(self, name: str, default=None):
        path = self.dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def state(self) -> dict:
        st = self.read(STATE)
        return st if isinstance(st, dict) else {"version": 1, "candidates": {}, "market": {}}

    def save_state(self, st: dict) -> None:
        self._write(STATE, st)

    def write_trades(self, doc: dict) -> None:
        self._write(TRADES, doc)

    def write_status(self, doc: dict) -> None:
        self._write(STATUS, doc)

    def write_contract(self, doc: dict) -> None:
        self._write(CONTRACT, doc)

    def event(self, kind: str, now_utc: pd.Timestamp, **data) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        ev = {"utc": pd.Timestamp(now_utc).isoformat(), "art": kind, **data}
        with open(self.dir / EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        return ev

    def events(self, limit: int | None = None) -> list[dict]:
        path = self.dir / EVENTS
        if not path.exists():
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        out.append({"art": "unlesbare_zeile", "inhalt": line[:200]})
        return out[-limit:] if limit else out
