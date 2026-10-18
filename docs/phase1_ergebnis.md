# Phase 1 – Ergebnis der technischen Bestandsaufnahme

**Stand:** 18.10.2026 (Sonntag) · **Prüfskript:** `phase1_bestandsaufnahme.py` · **Trades ausgeführt:** keine
**Gesamtergebnis:** ✅ bestanden – 30 OK, 5 Warnungen, 0 Fehler

## System

| Prüfung | Ergebnis |
|---|---|
| Betriebssystem | Windows 10 (10.0.19045) |
| Python | 3.13.5, 64-bit, virtuelle Umgebung `.venv` |
| Pakete | MetaTrader5 5.0.6231, numpy 2.5.4 (pandas/streamlit folgen in späteren Phasen) |
| MT5-Terminal | Build 6272, verbunden, Ping 105 ms |
| Max. Balken im Chart | 100 000 |

## Konto

| | |
|---|---|
| Server | **VantageMarkets-Demo** (Kontotyp **DEMO**) |
| Währung / Hebel | EUR / 1:500 |
| Kontostand | 1 000 EUR |
| Positionsmodus | Hedging |
| Algo-Trading im Terminal | aus (`trade_allowed=False`) – erst für die Handelsphase einschalten |

## Symbole und Kurse

| | Gold | Bitcoin |
|---|---|---|
| Symbol | `XAUUSD` | `BTCUSD` |
| Weitere Kandidaten | XAUUSD.crp, XAUEUR, XAUAUD, XAUJPY | BTCEUR, BTCETH, BTCLTC, BTCBCH |
| Bid / Ask | 4165.65 / 4165.93 | 81685.74 / 81702.68 |
| Spread | 0.28 USD (28 Points, 0.007 %) | 16.94 USD (1694 Points, 0.021 %) |
| Live-Ticks (10 s) | keine – Markt am Wochenende geschlossen (letzter Tick Fr. 23:56:59 Serverzeit) | 5 neue Ticks ✅ (Handel 24/7) |
| Kontraktgröße | 100 Unzen je Lot | 1 BTC je Lot |
| Volumen | 0.01 – 100, Schritt 0.01 | 0.01 – 100, Schritt 0.01 |
| Gegenwert Mindestvolumen 0.01 | ≈ 4 166 USD | ≈ 817 USD |
| Stops-Level | 20 Points (0.20 USD) | 0 |
| Füllart | IOC | IOC |

## Historische Kerzendaten

Pro Abfrage wurden höchstens 50 000 Kerzen geliefert (siehe Befund 1).

| Zeitrahmen | Gold ab | Gold Tage | Bitcoin ab | Bitcoin Tage |
|---|---|---|---|---|
| M1 | 27.08.2026 | 50 | 13.09.2026 | 35 |
| M5 | 04.02.2026 | 255 | 27.04.2026 | 174 |
| M15 | 05.09.2024 | 772 | 07.05.2025 | 529 |
| H1 | 02.05.2018 | 3 089 | 10.07.2020 | 2 291 |
| H4 | 22.06.2007 | 7 057 (vollständig) | 02.01.2018 | 3 211 (vollständig) |
| D1 | 22.06.2007 | 7 056 (vollständig) | 02.01.2018 | 3 211 (vollständig) |
| Ticks (24 h) | 435 047 | | 64 803 | |

Keine ungültigen OHLC-Werte. Lücken bei Gold sind durch Wochenenden und die tägliche Handelspause erklärbar.
Bitcoin zeigt vereinzelte Lücken (H1: 207, D1: 2) – vermutlich Wartungsfenster des Brokers, wird in Phase 2 geprüft.

## Befunde und Konsequenzen für die nächsten Phasen

1. **Historienlimit:** Eine Abfrage mit 100 000 Kerzen (= maxbars) wurde abgelehnt, 50 000 funktionierten.
   Für Backtests auf M1/M5 reicht das nicht. Phase 2 sollte die Daten blockweise über Zeiträume laden und
   lokal speichern; zusätzlich kann im Terminal „Max. Balken im Chart“ auf „Unbegrenzt“ gestellt werden.
2. **Serverzeit = GMT+3** (letzter BTC-Tick 10:18 Serverzeit bei 09:18 deutscher Sommerzeit).
   Alle Zeitstempel müssen für Auswertungen einheitlich umgerechnet werden.
3. **Kontowährung EUR, Symbole in USD:** Gewinne/Verluste müssen umgerechnet werden (EURUSD-Kurs).
4. **Positionsgrößen:** Bei 1 000 EUR Kapital und Hebel 1:500 ist striktes Risikomanagement nötig –
   0.01 Lot Gold bewegt sich um 1 USD je 1 USD Kursänderung.
5. **Hebel 1:500** deutet auf eine nicht-EU-Gesellschaft des Brokers hin (in der EU max. 1:20 für Gold,
   1:2 für Krypto bei Privatkunden). Für das Demokonto unerheblich, vor echtem Geld Regulierung prüfen.
6. **Erster Testlauf** lief auf MetaQuotes-Demo: dort gibt es nur Bitcoin-Aktien/ETFs (z. B. `BTC` zu 36 USD),
   keinen Bitcoin-CFD. Das Skript erkennt solche Symbole inzwischen und lehnt sie ab.
