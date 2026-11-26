"""Gemeinsamer Briefkasten für Telegram-Meldungen.

Nur ein Programm darf die Telegram-Anmeldung gleichzeitig nutzen (die Telegram-Signale). Andere Programmteile – z. B.
der Gold-Bot – legen ihre Meldungen deshalb als kleine Textdateien unter <data>/meldungen/ ab; die Telegram-Signale
schicken sie bei jedem Durchlauf in die "Gespeicherten Nachrichten" und löschen sie danach. Laufen die
Telegram-Signale nicht, bleiben höchstens die letzten KEEP Meldungen liegen.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

KEEP = 50


def outbox(data_root: str | Path) -> Path:
    return Path(data_root) / "meldungen"


def write(data_root: str | Path, sender: str, text: str) -> Path | None:
    d = outbox(data_root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.time_ns()}_{sender}.txt"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text.strip() + "\n", encoding="utf-8")
        os.replace(tmp, path)
        old = sorted(d.glob("*.txt"))[:-KEEP]
        for p in old:
            p.unlink(missing_ok=True)
        return path
    except OSError:
        return None


def collect(data_root: str | Path, limit: int = 20) -> list[str]:
    """Liest bis zu limit Meldungen (älteste zuerst) und löscht sie."""
    out = []
    for p in sorted(outbox(data_root).glob("*.txt"))[:limit]:
        try:
            out.append(p.read_text(encoding="utf-8").strip())
            p.unlink(missing_ok=True)
        except OSError:
            continue
    return [t for t in out if t]
