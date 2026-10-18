"""Trading-Software für Gold (XAUUSD) und Bitcoin (BTCUSD) mit MetaTrader 5.

Module:
    config        – Einstellungen aus config.toml
    timeframes    – Zeitrahmen und ihre Länge
    mt5_client    – rein lesender Zugriff auf das MT5-Terminal
    server_time   – MT5-Serverzeit <-> UTC, automatische Erkennung des Zeitschemas
    sessions      – Handelszeiten und Feiertage je Instrument
    storage       – lokale Parquet-Datenbank
    importer      – blockweiser und inkrementeller Datenimport
    quality       – Qualitätsprüfung (Lücken, Duplikate, Ausreißer, fehlerhafte Kurse)
    instruments   – Kontraktspezifikationen, Währungsumrechnung, Gewinn/Verlust und Risiko in Kontowährung
    strategies    – Schnittstelle für austauschbare Strategien
"""

__version__ = "0.2.0"
