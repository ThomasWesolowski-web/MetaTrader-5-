"""Zeitrahmen (MT5-Bezeichnung -> Länge in Sekunden)."""

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def seconds(tf: str) -> int:
    try:
        return TIMEFRAME_SECONDS[tf]
    except KeyError:
        raise ValueError(f"Unbekannter Zeitrahmen '{tf}'. Erlaubt: {', '.join(TIMEFRAME_SECONDS)}") from None


def mt5_constant(mt5, tf: str) -> int:
    """MT5-Konstante, z. B. mt5.TIMEFRAME_H1."""
    seconds(tf)
    return getattr(mt5, f"TIMEFRAME_{tf}")
