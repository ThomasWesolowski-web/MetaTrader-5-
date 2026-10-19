# Trading-Software Gold & Bitcoin (MetaTrader 5)

Automatisiertes Handelssystem für Gold und Bitcoin über MetaTrader 5, das schrittweise entwickelt wird.
**Aktueller Stand: Phase 4 – Dashboard und Beobachtung (Paper-Trading) umgesetzt. Es werden keine Trades ausgeführt und keine Orders gesendet.**

## Phasen

| Phase | Inhalt | Status |
|---|---|---|
| 1 | System prüfen, MT5-Verbindung, Symbole, Bid/Ask, Historie, Prüfbericht | ✅ abgeschlossen – [Ergebnis](docs/phase1_ergebnis.md) |
| 2 | Datenbasis: Import, Parquet-Speicher, UTC, Qualitätsprüfung, EUR-Risikorechnung | ✅ umgesetzt – [Ergebnis](docs/phase2_ergebnis.md) |
| 3 | Strategiebibliothek, Backtests, Walk-Forward, Strategieauswahl | ✅ abgeschlossen – 120/120 Testeinheiten, keine Strategie geeignet ([Ergebnis](docs/phase3_ergebnis.md)) |
| 4 | Streamlit-Dashboard, Beobachtung (Paper-Trading) der BTC-Trendfolge-Kandidaten, Kontrakt-/Mindestlotprüfung | 🔄 Punkte 1 und 2 umgesetzt – [Arbeitsplan](docs/phase4_arbeitsplan.md), [Ergebnis](docs/phase4_ergebnis.md); Punkt 3 offen, Punkt 4 zurückgestellt |
| 5+ | Demo-Handel | ⏸ nicht freigegeben |

## Phase 4: Dashboard und Beobachtung (keine Orders)

**Installation wie ein normales Programm** (MetaTrader 5 muss installiert sein):

1. Auf GitHub „Code“ → „Download ZIP“, ZIP entpacken.
2. `INSTALLIEREN.bat` doppelklicken. Der Installer prüft Python (installiert es bei Bedarf über winget) und
   MetaTrader 5, fragt nach dem Zielordner (Standard `C:\Trading\TradingBot`, jedes Laufwerk möglich), übernimmt
   auf Wunsch Kursdaten, Berichte und Beobachtungsstand aus `C:\Trading\MetaTrader-5--master`, richtet die
   Python-Umgebung ein und legt die Symbole „Trading starten“ / „Trading stoppen“ auf dem Desktop und im Startmenü
   (Ordner „Trading“) an; optional Autostart mit Windows.
3. Danach nur noch „Trading starten“: aktualisiert die Kursdaten, startet Beobachtung und Dashboard in eigenen
   (minimierten) Fenstern und öffnet den Browser. „Trading stoppen“ beendet beides sauber.

Neue Version: neue ZIP entpacken und erneut `INSTALLIEREN.bat` ausführen – Daten bleiben erhalten.
Entfernen: Startmenü → Trading → „Trading deinstallieren“ (Daten bleiben, außer die Löschung wird bestätigt).

Von Hand:

```bat
pip install -r requirements.txt
python daten_aktualisieren.py
python beobachtung_ausfuehren.py
```

Die Beobachtung läuft dauerhaft (ein Durchlauf pro Minute, Strg+C beendet) und protokolliert Signale,
hypothetische Ein- und Ausstiege, Kosten und Ergebnisse unter `data\paper\`. Nach einem Neustart setzt sie fort.
Optionen (`python beobachtung_ausfuehren.py --help`): `--einmal` (ein Durchlauf), `--intervall` (Sekunden),
`--status` (Stand anzeigen ohne MT5), `--config`.

Dashboard in einem zweiten Fenster (öffnet sich im Browser):

```bat
streamlit run dashboard.py
```

Bereiche: Übersicht · Backtest (Phase 3) · Live-Beobachtung (Paper-Trading) · Daten · Kontrakt & Mindestlot.

## Phase 3: Backtests (nur Simulation)

```bat
python daten_aktualisieren.py
python backtest_ausfuehren.py --testlauf
python backtest_ausfuehren.py --mt5-abgleich
```

Ausgelegt für 8 GB RAM: 1 Prozess, Märkte nacheinander, M1 in Blöcken. Der volle Lauf startet erst nach bestandenem
Testlauf (fehlt er, wird er automatisch zuerst ausgeführt). Nach einem Abbruch setzt derselbe Befehl fort.

Ergebnis: `reports\phase3_bericht_*.md`, maschinenlesbar `reports\phase3_ergebnisse.json`, Protokoll
`reports\phase3_log_*.txt`, Prüfpunkte in `reports\phase3_pruefpunkte\`.

Alle Optionen (`python backtest_ausfuehren.py --help`): `--symbole`, `--zeitrahmen`, `--strategien`,
`--prozesse` (Standard 1), `--blockgroesse` (Standard 100000, Minimum 10000), `--testlauf`, `--neu`
(Prüfpunkte verwerfen), `--mt5-abgleich`, `--config`.

## Phase 2: Datenbasis aufbauen und aktualisieren

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python daten_aktualisieren.py
```

Beim ersten Start wird die gesamte verfügbare Historie von XAUUSD, BTCUSD und dem Umrechnungssymbol
(EURUSD) in allen Zeitrahmen geladen, danach bei jedem Start nur das Fehlende. Die Daten liegen in
`data\market\<Symbol>\<Zeitrahmen>\<Jahr>.parquet`, der Bericht in `reports\phase2_datenbericht_*.md`.

```bat
:: nur bestimmte Symbole/Zeitrahmen
python daten_aktualisieren.py --symbole XAUUSD --zeitrahmen H1 D1
:: nur die lokalen Daten prüfen (ohne MT5)
python daten_aktualisieren.py --nur-pruefung
```

Einstellungen: `config.toml`. Aufbau des Pakets: siehe [docs/phase2_ergebnis.md](docs/phase2_ergebnis.md).

## Phase 1 auf dem Windows-PC ausführen

Voraussetzungen:
- Windows 10/11, **64-bit Python 3.11 oder neuer** (python.org; bei der Installation „Add to PATH“ anhaken)
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
python -m pytest
```

`tests/fake_market.py` simuliert einen MT5-Markt (Serverzeit mit Sommerzeit, Handelszeiten, Feiertage,
Lücken, Fehlticks, begrenzte Historie); `tests/fake_mt5/` ist das einfache Fake-Modul für Phase 1.
Beide brechen ab, sobald eine Order-Funktion aufgerufen wird.
