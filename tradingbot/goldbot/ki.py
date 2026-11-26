"""KI-Prüfung jedes Setups vor der Order (Claude API, optional).

Die Regeln (strategie.py) finden das Setup; Claude bekommt die letzten M15-Kerzen, Trend, Order Block, Liquidität,
Stop und Ziele und antwortet nur mit Freigabe ja/nein und einer kurzen Begründung. Die KI erfindet keine Trades und
ändert keine Preise – sie kann nur ablehnen. Ohne API-Schlüssel oder mit [ki] aktiv = false handelt der Bot nur nach
den Regeln.

Schlüssel: Umgebungsvariable ANTHROPIC_API_KEY oder Datei ki_zugang.toml (api_key = "sk-ant-..."), die nur auf
deinem PC liegt (steht in .gitignore). Kosten: grob 1-3 US-Cent je Prüfung.
"""

from __future__ import annotations

import json

import pandas as pd

from .config import GoldConfig
from .strategie import Analysis, Setup

SYSTEM = """Du prüfst Gold-Trades (XAUUSD) einer regelbasierten Order-Block-Strategie auf dem M15-Chart, bevor eine \
Order gesendet wird. Die Strategie:
- Nur mit dem M15-Trend handeln (abwärts = tiefere Hochs und tiefere Tiefs, aufwärts umgekehrt). Nicht nach einem \
starken Abverkauf/Anstieg hinterherlaufen.
- Plan A: Limit-Einstieg beim Rücksetzer in den nächsten Order Block gegen die Bewegung, Stop knapp hinter dem Order \
Block, Ziel 1 = letzte Liquidität (letztes Tief/Hoch), Ziel 2 weiter. Bei Ziel 1 halbe Position schließen, Stop auf \
Einstieg.
- Plan B: Fortsetzung, wenn eine M15-Kerze jenseits eines Schlüsseltiefs/-hochs schließt; Einstieg beim Retest, Stop \
hinter dem Retest.
- Kein Gegentrend-Trade, bis die Struktur bricht.
Deine Aufgabe: Prüfe, ob das gefundene Setup zu diesen Regeln passt und der Chart es stützt (saubere Struktur, Order \
Block nicht schon verbraucht, kein Einstieg mitten in eine Gegenbewegung, Ziel realistisch, Stop nicht absurd eng oder \
weit). Sei kritisch, aber lehne nicht grundlos ab: ein regelkonformes Setup mit sauberem Chart bekommt die Freigabe. \
Du kannst Preise nicht ändern. Antworte auf Deutsch, Begründung höchstens zwei kurze Sätze."""

SCHEMA = {
    "type": "object",
    "properties": {"freigabe": {"type": "boolean"}, "begruendung": {"type": "string"}},
    "required": ["freigabe", "begruendung"],
    "additionalProperties": False,
}


def prompt(s: Setup, df: pd.DataFrame, an: Analysis, n_bars: int = 80) -> str:
    tail = df.tail(n_bars)
    rows = "\n".join(f"{pd.Timestamp(int(r.time), unit='s'):%m-%d %H:%M},{r.open:.2f},{r.high:.2f},{r.low:.2f},"
                     f"{r.close:.2f}" for r in tail.itertuples())
    return "\n".join([
        f"SETUP Plan {s.plan}: {s.side} " + (f"Limit {s.entry:.2f}" if s.entry else f"zum Markt (ca. {s.ref_price:.2f})"),
        f"Stop {s.stop:.2f} | Ziel 1 {s.ziel1:.2f} | Ziel 2 {s.ziel2:.2f} | Chance/Risiko bis Ziel 1: {s.rr1:.2f}",
        f"Begründung der Regeln: {s.reason}",
        "",
        "ANALYSE:",
        *an.lines(),
        "",
        f"LETZTE {len(tail)} M15-KERZEN (Serverzeit, abgeschlossen) – zeit,open,high,low,close:",
        rows,
        "",
        "Soll diese Order gesendet werden?",
    ])


def make_checker(cfg: GoldConfig, log=print):
    """Gibt check(setup, df, analysis) -> (freigabe, text) zurück, oder None wenn die KI aus ist."""
    if not cfg.ai_enabled:
        return None
    key = cfg.api_key()
    if not key:
        log("KI-Prüfung eingeschaltet, aber kein API-Schlüssel (ki_zugang.toml oder ANTHROPIC_API_KEY) – "
            + ("es wird NICHT gehandelt, bis einer da ist" if cfg.ai_on_error == "ablehnen" else
               "es wird nur nach den Regeln gehandelt"))
    try:
        import anthropic
    except ImportError:
        anthropic = None
        log("Paket anthropic fehlt: .venv\\Scripts\\python.exe -m pip install anthropic")
    client = anthropic.Anthropic(api_key=key, timeout=cfg.ai_timeout_s, max_retries=2) if (anthropic and key) else None

    def fail(why: str) -> tuple[bool, str]:
        if cfg.ai_on_error == "handeln":
            return True, f"KI nicht erreichbar ({why}) – nach den Regeln gehandelt"
        return False, f"KI nicht erreichbar ({why})"

    def check(s: Setup, df: pd.DataFrame, an: Analysis) -> tuple[bool, str]:
        if client is None:
            return fail("kein API-Schlüssel" if not key else "Paket anthropic fehlt")
        try:
            resp = client.beta.messages.create(
                model=cfg.ai_model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config={"effort": cfg.ai_effort,
                               "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": prompt(s, df, an)}],
            )
        except anthropic.AuthenticationError:
            return fail("API-Schlüssel ungültig")
        except anthropic.RateLimitError:
            return fail("zu viele Anfragen")
        except anthropic.APIStatusError as e:
            return fail(f"Fehler {e.status_code}")
        except anthropic.APIConnectionError:
            return fail("keine Verbindung")
        if resp.stop_reason == "refusal":
            return fail("Anfrage abgelehnt")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return fail("Antwort unlesbar")
        return bool(data.get("freigabe")), str(data.get("begruendung", "")).strip()[:300]

    return check
