"""Zeiträume, Walk-Forward-Optimierung und Parameterauswahl – ohne Data Leakage.

Aufteilung je Markt (chronologisch, für alle Zeitrahmen identisch):
    Training  (erste 60 %)  – Walk-Forward und Parameterwahl
    Validierung (20 %)      – Vergleich und Auswahl der Strategien
    Test (letzte 20 %)      – nur zur Bestätigung; wird für keine Auswahl genutzt

Walk-Forward im Trainingszeitraum: rollierend 2 Jahre In-Sample (Parameterwahl), danach 6 Monate
Out-of-Sample. Die OOS-Abschnitte werden aneinandergereiht = ehrliche Schätzung der Parameterwahl.

Leakage-Schutz:
  * Signale an Kerze i nutzen nur Daten bis i (Indikatoren + Test test_no_lookahead).
  * Für die Parameterwahl zählen nur Trades, die im Auswahlfenster eröffnet UND geschlossen wurden.
  * Auswertungsfenster zählen Trades, die im Fenster eröffnet wurden.
  * Validierungs- und Testdaten fließen nie in die Parameterwahl.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

MIN_TRADES_SELECT = 30


@dataclass
class Periods:
    start: pd.Timestamp
    train_end: pd.Timestamp
    val_end: pd.Timestamp
    end: pd.Timestamp

    def to_dict(self) -> dict:
        return {k: str(v)[:10] for k, v in vars(self).items()}

    def window(self, name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        return {"train": (self.start, self.train_end), "validation": (self.train_end, self.val_end),
                "test": (self.val_end, self.end)}[name]


def split_periods(start, end, train=0.6, val=0.2) -> Periods:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    span = end - start
    t_end = (start + span * train).normalize()
    v_end = (start + span * (train + val)).normalize()
    return Periods(start, t_end, v_end, end)


def wf_windows(start, end, is_years: float = 2.0, oos_months: int = 6) -> list[tuple]:
    out = []
    a = pd.Timestamp(start)
    while True:
        is_end = a + pd.DateOffset(months=int(is_years * 12))
        oos_end = is_end + pd.DateOffset(months=oos_months)
        if oos_end > end:
            break
        out.append((a, is_end, is_end, oos_end))
        a = a + pd.DateOffset(months=oos_months)
    return out


def slice_trades(raw: pd.DataFrame, a, b, closed_within: bool = False) -> pd.DataFrame:
    m = (raw["entry_time"] >= a) & (raw["entry_time"] < b)
    if closed_within:
        m &= raw["exit_time"] < b
    return raw[m]


def objective(m: dict) -> float:
    """Auswahlkriterium: t-Wert der Netto-R je Trade (stabil statt maximaler Gewinn),
    Abzug bei Drawdown-Überschreitung, ungültig bei zu wenigen Trades."""
    if m["trades"] < MIN_TRADES_SELECT or m.get("t_stat") is None:
        return -math.inf
    score = m["t_stat"]
    if m.get("dd_limit_breached"):
        score -= 1.0
    return score
