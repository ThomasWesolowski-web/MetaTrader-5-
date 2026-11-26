"""Erkennt Handelssignale und Folgenachrichten in Telegram-Texten (z. B. Kanal "FX Culture | Premium FX").

Beispiel Signal:
    SIGNAL ALERT / XAUUSD / SELL / ENTRY AREA 3622-3626 / 🎯Target: 3560 / ❌Stop loss: 3642
Beispiele Folgenachrichten:
    "taking partials here"        -> Teilgewinn
    "and also moving my SL to BE" -> Stop auf den Einstieg
    "closing this one now"        -> alles schließen
    "move SL to 3610"             -> neuer Stop
    "cancel, don't enter"         -> noch nicht eröffnetes Signal verwerfen

Reine Textauswertung, kein Zugriff auf MT5 oder Telegram. Lieber ein Signal übersehen als eines falsch lesen:
ohne Richtung, Stop und Ziel oder mit widersprüchlichen Angaben gibt parse_signal None zurück.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM = r"(\d+(?:[.,]\d+)?)"
CCY = {"EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "XAU", "XAG", "BTC", "ETH", "SGD", "NOK", "SEK",
       "ZAR", "MXN", "TRY", "PLN", "HKD"}
ALIASES = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "BITCOIN": "BTCUSD", "BTC": "BTCUSD", "ETH": "ETHUSD",
           "DOW": "US30", "DJ30": "US30", "US30": "US30", "NASDAQ": "NAS100", "NAS100": "NAS100", "US100": "NAS100",
           "NQ": "NAS100", "SPX": "US500", "SPX500": "US500", "SP500": "US500", "US500": "US500",
           "DAX": "GER40", "GER40": "GER40", "DE40": "GER40", "GER30": "GER40", "UK100": "UK100", "OIL": "USOIL",
           "USOIL": "USOIL", "WTI": "USOIL"}


@dataclass
class Signal:
    symbol: str
    direction: int                 # 1 = Kauf, -1 = Verkauf
    entry_lo: float | None         # Einstiegszone (bei einem einzelnen Kurs lo == hi); None = sofort zum Marktpreis
    entry_hi: float | None
    stop: float
    targets: list[float] = field(default_factory=list)   # leer = "Target: open" (Ausstieg über Folgenachrichten)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "richtung": self.direction, "zone": [self.entry_lo, self.entry_hi],
                "stop": self.stop, "ziele": self.targets}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def find_symbol(text: str) -> str | None:
    up = text.upper()
    for m in re.finditer(r"\b([A-Z]{3})\s*/?\s*([A-Z]{3})\b", up):
        if m.group(1) in CCY and m.group(2) in CCY and m.group(1) != m.group(2):
            return m.group(1) + m.group(2)
    for w in re.findall(r"\b[A-Z][A-Z0-9]{1,6}\b", up):
        if w in ALIASES:
            return ALIASES[w]
    return None


def parse_signal(text: str) -> Signal | None:
    up = text.upper().replace("–", "-").replace("—", "-")
    dirs = set(re.findall(r"\b(BUY|SELL|LONG|SHORT)\b", up))
    if len(dirs) != 1 and dirs not in ({"BUY", "LONG"}, {"SELL", "SHORT"}):
        return None
    direction = 1 if dirs & {"BUY", "LONG"} else -1
    symbol = find_symbol(text)
    m_sl = re.search(r"(?:STOP\s*-?\s*LOSS|\bSL\b|\bS/L\b|\bSTOP\b)\s*[:@=\-]?\s*" + NUM, up)
    targets = [_num(t) for t in re.findall(
        r"(?:\bTARGETS?\b|\bTP\b|\bTP\d\b|TAKE\s*-?\s*PROFIT)\s*(?:\d\b)?\s*[:@=\-]?\s*" + NUM, up)]
    open_target = bool(re.search(r"(?:\bTARGETS?\b|\bTP\b|TAKE\s*-?\s*PROFIT)\s*[:@=\-]?\s*OPEN\b", up))
    if not symbol or not m_sl or not (targets or open_target):
        return None
    stop = _num(m_sl.group(1))
    m_e = re.search(r"\bENTRY(?:\s*(?:AREA|ZONE|PRICE|POINT|LEVEL))?\s*[:@=]?\s*" + NUM + r"(?:\s*(?:-|/|TO)\s*" + NUM + ")?", up)
    if not m_e:
        m_e = re.search(r"\b(?:BUY|SELL)\b\s*(?:NOW|LIMIT|STOP)?\s*(?:@|AT)?\s*" + NUM + r"(?:\s*(?:-|/|TO)\s*" + NUM + ")?", up)
    if not m_e:                                                      # Zeile nur mit Kurs(en): "4054.0-4050"
        m_e = re.search(r"^[^\S\n]*" + NUM + r"(?:[^\S\n]*(?:-|/|TO)[^\S\n]*" + NUM + r")?[^\S\n]*$", up, re.M)
    lo = hi = None
    if m_e:
        a = _num(m_e.group(1))
        b = _num(m_e.group(2)) if m_e.group(2) else a
        lo, hi = min(a, b), max(a, b)
    # Plausibilität: Stop auf der Verlustseite, Ziele auf der Gewinnseite, alles in derselben Größenordnung
    ref = (lo + hi) / 2 if lo is not None else None
    if ref is not None:
        if direction * (ref - stop) <= 0 or (lo is not None and direction * ((lo if direction == 1 else hi) - stop) <= 0):
            return None
        targets = [t for t in targets if direction * (t - ref) > 0]
    else:
        targets = [t for t in targets if direction * (t - stop) > 0]
    if not targets and not open_target:
        return None
    base = ref or stop
    if any(abs(x - base) > 0.2 * base for x in [stop, *targets]):
        return None
    targets = sorted(set(targets), key=lambda t: direction * t)        # nächstes Ziel zuerst
    return Signal(symbol, direction, lo, hi, stop, targets)


# ---------------------------------------------------------------------- Folgenachrichten
PARTIAL, BREAKEVEN, NEW_STOP, CLOSE, CANCEL, INFO = "teilgewinn", "stop_auf_einstieg", "neuer_stop", "schliessen", \
    "verwerfen", "info"

_SL = r"\b(?:sl|s/l|stop\s*-?\s*loss|stop|stops)\b"
_BE = r"(?:\bbe\b|\bb\.e\.?|break\s*-?\s*even|\bentry\b|\bentry\s+price\b|\bopen(?:ing)?\s+price\b)"


MAX_FOLLOWUP_LEN = 300          # längere Texte sind Werbung, Wochenrückblicke usw. – nie als Handelsanweisung lesen
_PROMO = re.compile(r"t\.me/|@\w{3,}|message\s+me|best\s+trader|promotion|referr?al|bonus|deal\s+for|giveaway|"
                    r"premium\s+members?|join\s+(?:now|here)|link\s+in\s+bio|dm\s+me")


def parse_followup(text: str) -> list[tuple[str, float | None]]:
    """Liste der Aktionen in einer Folgenachricht, in sinnvoller Reihenfolge (Teilgewinn vor Stopänderung).
    Teilgewinn hat als Wert den genannten Prozentsatz ("closing out 80%" -> 80) oder None (= Standard)."""
    t = text.lower().replace("’", "'").strip()
    if not t or len(t) > MAX_FOLLOWUP_LEN or _PROMO.search(t):
        return []
    acts: list[tuple[str, float | None]] = []
    if re.search(r"\b(cancel(?:l?ed)?|invalidated?|delete[ds]?|void|don'?t\s+(?:enter|take)|no\s+entry|"
                 r"not\s+(?:entering|valid))\b", t):
        return [(CANCEL, None)]
    full_close = False
    partial = False
    m = re.search(r"\b(?:clos(?:e|ing|ed)(?:\s+out)?|secur(?:e|ed|ing)|tak(?:e|ing)|bank(?:ed|ing)?|book(?:ed|ing)?|"
                  r"lock(?:ed|ing)?)\b[^.\n!?%+\-]{0,20}?(\d{1,3})\s*%", t)
    if m:
        pct = float(m.group(1))
        if pct >= 100:
            full_close = True
        elif pct > 0:
            acts.append((PARTIAL, pct))
            partial = True
    elif re.search(r"\bpartials?\b|\bpartially\b|\bclos(?:e|ing|ed)\s+(?:out\s+)?(?:half|some)\b|"
                   r"\b(?:secur|book|tak|lock|bank)(?:e|ed|ing)?\s+(?:some|half|partial)", t):
        acts.append((PARTIAL, None))
        partial = True
    be = (re.search(_SL + r"[^.\n!?]{0,25}?" + _BE, t)
          or re.search(r"\bbreak\s*-?\s*even\b|\brisk\s*-?\s*free\b|\bremov(?:e|ed|ing)\s+(?:all\s+|the\s+)?risk\b", t))
    if be:
        acts.append((BREAKEVEN, None))
    else:
        m = re.search(r"\b(?:move|moving|moved|set|setting|adjust|adjusting|trail|trailing|new)\b[^.\n!?]{0,20}?"
                      + _SL + r"[^.\n!?\d]{0,15}?" + NUM, t)
        if m:
            acts.append((NEW_STOP, _num(m.group(1))))
    if full_close or (not partial and re.search(
            r"\b(?:clos(?:e|ing|ed)|exit(?:ing|ed)?|get\s+out|calling\s+it\s+a\s+day)\b"
            r"(?!\s+(?:to|above|below|under|over|near|the\s+gap|in\s+on))", t)
            and not re.search(r"\b(?:tp|target)s?\s*\d?\s*(?:hit|reached|smashed|done)\b|\bsl\s+hit\b|"
                              r"\bstopped\s+out\b", t)):
        acts.append((CLOSE, None))
    if not acts and re.search(r"\b(?:tp|target)s?\s*\d?\s*(?:hit|reached|smashed|done)\b|\bsl\s+hit\b|"
                              r"\bstopped\s+out\b|\bpips?\b", t):
        acts.append((INFO, None))
    return acts
