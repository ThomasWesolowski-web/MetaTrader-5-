# Trading-Software Gold & Bitcoin (MetaTrader 5)

Automatisiertes Handelssystem für Gold und Bitcoin über MetaTrader 5, das schrittweise entwickelt wird.
**Aktueller Stand: Phase 1 – technische Bestandsaufnahme. Es werden keine Trades ausgeführt.**

## Phasen

| Phase | Inhalt | Status |
|---|---|---|
| 1 | System prüfen, MT5-Verbindung, Symbole, Bid/Ask, Historie, Prüfbericht | ✅ abgeschlossen – [Ergebnis](docs/phase1_ergebnis.md) |
| 2+ | Daten, Backtests, Strategieauswahl, Demo-Handel, Streamlit-Dashboard | ⏸ wartet auf Freigabe |

## Phase 1 auf dem Windows-PC ausführen

Voraussetzungen:
- Windows 10/11, **64-bit Python 3.9 oder neuer** (python.org; bei der Installation „Add to PATH“ anhaken)
- MetaTrader-5-Terminal installiert, **geöffnet und mit dem Demokonto eingeloggt**

In der Eingabeaufforderung (cmd) im Projektordner:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python phase1_bestandsaufnahme.py
```

Optionen:

```bat
:: Wenn mehrere MT5-Terminals installiert sind oder die Verbindung fehlschlägt
python phase1_bestandsaufnahme.py --terminal "C:\Program Files\MetaTrader 5\terminal64.exe"

:: Wenn die automatische Symbolerkennung das falsche Symbol wählt
python phase1_bestandsaufnahme.py --gold XAUUSD --btc BTCUSD

:: Alle Symbole auflisten, die "BTC" im Namen oder in der Beschreibung haben (mit Basiswährung, Kurs, Pfad)
python phase1_bestandsaufnahme.py --suche BTC

:: Längere Live-Kursprüfung (Standard: 10 Sekunden je Symbol)
python phase1_bestandsaufnahme.py --sample-seconds 30
```

Ergebnis: `reports\phase1_bericht_<Datum>_<Zeit>.md` (lesbarer Bericht) und `.json` (Rohdaten).
Die Kontonummer wird im Bericht maskiert.

### Was das Skript prüft

1. **System:** Betriebssystem, Python-Version und -Architektur, installierte Pakete
2. **MT5-Verbindung:** `initialize()`, Terminal-Build, Serververbindung, Ping, „Max. Balken im Chart“,
   Kontotyp (warnt, wenn es **kein** Demokonto ist), Währung, Hebel, Hedging/Netting
3. **Symbole:** durchsucht alle Broker-Symbole nach Gold (XAUUSD/GOLD …) und Bitcoin (BTCUSD/XBT …),
   bewertet die Kandidaten und listet sie im Bericht auf; zeigt Kontraktgröße, Volumen, Stops-Level, Füllarten
4. **Kurse:** Bid, Ask, Spread (absolut, in Points, in %) und ob in einem Messfenster neue Ticks eintreffen
5. **Historie:** M1, M5, M15, H1, H4, D1 – Anzahl Kerzen, Zeitraum, Lücken, ungültige OHLC-Werte,
   außerdem die Tickdaten der letzten 24 Stunden

### Sicherheit

Das Skript ist rein lesend: Es ruft weder `order_send` noch `order_check` auf (ein Test prüft das).
Die einzige Änderung am Terminal: Gefundene Symbole werden in die Marktübersicht aufgenommen (`symbol_select`).

## Tests (ohne MT5, auch unter Linux)

```bash
python -m unittest discover tests
```

`tests/fake_mt5/MetaTrader5.py` simuliert die MT5-API mit Fake-Daten und bricht ab, sobald eine
Order-Funktion aufgerufen wird.
