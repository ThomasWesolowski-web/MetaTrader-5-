# Phase 2 – Datenbasis: Ergebnis

**Stand:** 18.10.2026 · **Trades ausgeführt:** keine (weder Demo noch Echtgeld)
**Status:** Software fertig und mit 71 automatisierten Tests geprüft (simulierter MT5-Markt).
**Ausstehend:** erster Lauf auf dem Windows-PC mit VantageMarkets-Demo – erst dieser zeigt, wie weit die
echten Daten zurückreichen. Die Ergebnisse werden im Abschnitt [Echte Daten](#echte-daten-vantagemarkets-demo) ergänzt.

## Ehrliche Einordnung

Die Entwicklung lief in einer Linux-Cloud-Umgebung ohne Zugriff auf das MT5-Terminal. Alle Funktionen
wurden gegen einen simulierten Markt getestet, der die in Phase 1 beobachteten Eigenheiten nachbildet
(Serverzeit mit Sommerzeitwechsel, Goldpausen, Feiertage, Broker-Wartung, begrenzte Historie,
abgelehnte Großabfragen). Wie weit die Historie bei Vantage tatsächlich reicht, welches Zeitschema der
Server nutzt und wie sauber die echten Daten sind, ist **noch nicht gemessen** – dafür ist der Lauf
`python daten_aktualisieren.py` auf dem PC nötig. Es wurden keine Kursdaten erfunden oder angenommen.

## Umsetzung der Anforderungen

| # | Anforderung | Umsetzung |
|---|---|---|
| 1 | XAUUSD, BTCUSD in M1, M5, M15, M30, H1, H4, D1 | `config.toml`; zusätzlich automatisch **EURUSD** als Umrechnungssymbol (Konto in EUR) |
| 2 | Import in Blöcken | Blöcke à 40 000 Kerzen über `copy_rates_range`; lehnt MT5 einen Block ab, wird er halbiert. Rückwärtssuche bis 3 Blöcke hintereinander leer sind = tatsächliches Historienende |
| 3 | Lokale Speicherung, Parquet | `data/market/<Symbol>/<TF>/<Jahr>.parquet`, atomare Schreibvorgänge, nur das betroffene Jahr wird neu geschrieben |
| 4 | Inkrementelle Aktualisierung | Vorwärts ab der letzten gespeicherten Kerze; die laufende Kerze wird nie gespeichert; abgebrochene Importe setzen beim nächsten Start fort; wird später mehr Historie verfügbar (z. B. „Max. Balken“ erhöht), wird sie nachgeladen |
| 5 | Intern UTC, echte MT5-Semantik | siehe [Zeitbasis](#zeitbasis-utc) – keine pauschale GMT+3-Korrektur, Schema wird aus den Daten erkannt |
| 6 | Qualitätsprüfung | Duplikate, Reihenfolge, fehlende Werte, Preise ≤ 0, ungültige OHLC, negativer Spread, Lücken mit Einordnung, Ausreißer/Fehlticks, Konsistenz höherer Zeitrahmen mit M1 |
| 7 | Handelszeiten Gold vs. Bitcoin | Kalender `cme_metals` (Gold, New Yorker Zeit, Tagespause, US-Feiertage), `24x7` (Bitcoin), `fx_24x5` (EURUSD) |
| 8 | Gewinn/Verlust/Risiko in EUR | `instruments.py`: MT5-Kontraktdaten, Umrechnung über historische EURUSD-Kurse, Positionsgröße, Margin, Spreadkosten; Abgleich mit den Rechenfunktionen des Terminals |
| 9 | Modulare Architektur | Paket `tradingbot/`, Strategie-Schnittstelle mit Registrierung (`strategies/base.py`) |
| 10 | Automatisierte Tests | 71 Tests (pytest), siehe [Tests](#tests) |

## Architektur

```
daten_aktualisieren.py        Startskript Phase 2 (Import → Validierung → Qualitätsprüfung → Bericht)
config.toml                   Einstellungen
tradingbot/
  mt5_client.py               rein lesender MT5-Zugriff (keine order_send/order_check)
  server_time.py              Serverzeit ↔ UTC, Erkennung des Zeitschemas
  sessions.py                 Handelskalender und US-Feiertage
  storage.py                  Parquet-Datenbank (MarketStore)
  importer.py                 blockweiser + inkrementeller Import
  quality.py                  Qualitätsprüfung
  instruments.py              Kontraktdaten, Währungsumrechnung, Gewinn/Verlust/Risiko/Margin
  pipeline.py                 Ablaufsteuerung
  report.py                   Datenbericht (Markdown + JSON)
  strategies/base.py          Strategie-Schnittstelle (für Phase 3)
tests/                        71 Tests + simulierter MT5-Markt (fake_market.py)
```

Die Strategien sehen nur Kerzendaten aus der lokalen Datenbank (`MarketData`) und liefern eine
Zielposition (−1/0/+1) je Kerze. Sie kennen weder MT5 noch Orders – dadurch sind sie unabhängig
voneinander testbar. Konvention gegen Look-ahead-Bias: Signal auf Basis des Schlusskurses, Ausführung
frühestens zur Eröffnung der Folgekerze.

## Zeitbasis UTC

**MT5-Semantik:** Das Feld `time` von Kerzen und Ticks enthält Sekunden seit 1970 – aber gemessen in der
Ortszeit des Handelsservers, nicht in UTC. Auch Zeitangaben für `copy_rates_range` werden so interpretiert.
Gespeichert werden deshalb beide Werte: `time_server` (unverändert von MT5) und `time` (UTC). Ändert sich
das erkannte Schema, werden die UTC-Zeiten automatisch neu berechnet.

**Erkennung statt Annahme:** Die tägliche Goldpause ist an New Yorker Zeit gebunden (17:00–18:00 ET).
Für jedes Kandidatenschema (`ny_close` = New York + 7 h, `eu_dst` = EU-Sommerzeitregel, feste Abstände
UTC−1 … UTC+4) wird geprüft, ob die Pause das ganze Jahr über zur gleichen New Yorker Uhrzeit liegt.
Zusätzlich muss das Schema zum aktuell gemessenen Abstand passen (frischer BTCUSD-Tick vs. PC-Uhr).
Die beiden üblichen Schemata unterscheiden sich nur in den Wochen zwischen US- und EU-Umstellung
(März: 3 Wochen, Okt./Nov.: 1 Woche) – genau dort entscheidet die Goldpause. In der Simulation wurde jedes
Schema korrekt erkannt (`ny_close` 100 % Konstanz gegenüber `eu_dst` 84 % und `fixed:+03:00` 70 %).
Falls nötig, lässt sich das Schema in `config.toml` fest vorgeben.

## Lücken: reguläre Pausen vs. Datenfehler

| Kategorie | Bedeutung |
|---|---|
| Wochenende / Tagespause | laut Handelskalender geschlossen (Toleranz 20 Min. für Broker-Abweichungen) |
| Feiertag | Lücke berührt einen US-Feiertag (Gold) – geschlossen oder verkürzter Handel |
| ohne Ticks | kurze Lücke (≤ 5 Min.) in M1/M5 innerhalb der Handelszeit |
| regelmäßige Pause | gleiche Serverzeit und Dauer in ≥ 3 Wochen (z. B. Broker-Wartung) |
| **unerklärt** | möglicher Datenfehler – wird im Bericht mit Zeitraum aufgelistet |

Ausreißer: robuster z-Wert der Rendite (gleitender Median/MAD), Kursspitzen mit sofortiger Rückkehr
(Fehltick-Verdacht) und extreme Kerzenspannen. Renditen über Lücken (z. B. Wochenend-Gap) werden nicht
bewertet. **Daten werden nur markiert, nie verändert, gelöscht oder aufgefüllt.**

## Gewinn, Verlust und Risiko in EUR

- Kontraktdaten (Kontraktgröße, Tickgröße/-wert, Volumenraster, Berechnungsmodus, Swap) werden bei jedem
  Lauf aus MT5 gelesen und in `data/specs.json` gespeichert – Backtests laufen dadurch auch offline.
- Gewinn in USD = Richtung × Kursdifferenz × Kontraktgröße × Volumen, umgerechnet mit dem EURUSD-Schlusskurs
  des letzten **abgeschlossenen** Bars (kein Blick in die Zukunft; vor dem ersten verfügbaren Kurs: kein Wert).
- Positionsgröße aus Risikobetrag (EUR) und Stop-Abstand, immer **abgerundet** auf das Volumenraster;
  unter dem Mindestvolumen wird 0 zurückgegeben (Trade nicht möglich).
- Margin nach MT5-Berechnungsmodus. Der symbolabhängige Marginsatz ist über die Python-API nicht lesbar
  und wird mit `order_calc_margin` kalibriert (z. B. Bitcoin mit niedrigerem effektivem Hebel als 1:500).
- Validierung bei jedem Lauf: eigene Berechnung vs. `order_calc_profit` und Tickwert des Terminals.
  `order_calc_profit`/`order_calc_margin` sind reine Rechenfunktionen und erzeugen keine Order.
  Simulation: Abweichung < 0,01 %.

## Tests

`python -m pytest` – 71 Tests, alle bestanden (Laufzeit ca. 30 s):

| Datei | Tests | Prüft |
|---|---|---|
| test_importer.py | 12 | gespeicherte Daten = MT5-Daten (nichts erfunden), Blockgrößen, Halbierung abgelehnter Abfragen, echte UTC-Zeiten bei zwei Serverschemata, inkrementelles Update lädt exakt die neuen Kerzen, laufende Kerze wird nicht gespeichert, Nachladen erweiterter Historie, Fortsetzen nach Abbruch, Neuberechnung bei Schemawechsel |
| test_server_time.py | 21 | Abstände inkl. Sommerzeit, Hin-/Rückrechnung, MT5-Semantik, Live-Abstand, Erkennung von `ny_close`, `eu_dst` und festem Abstand mit/ohne Live-Messung |
| test_quality.py | 7 | nur die eingebaute echte Lücke ist unerklärt, Wochenenden/Tagespausen/5 Feiertage erkannt, Bitcoin-Wartung und Minuten ohne Ticks, Fehltick gefunden, Wochenend-Gap kein Ausreißer, Integritätsfehler, manipulierte Kerze in der Konsistenzprüfung |
| test_instruments.py | 10 | Gewinn in EUR, Positionsgröße/Abrundung, Margin-Modi, Umrechnung nur mit vergangenen Kursen, Wahl des Umrechnungssymbols, Abgleich mit Terminal-Rechenfunktionen |
| test_sessions.py | 9 | Ostern/US-Feiertage, Gold-Sitzungen in Sommer- und Winterzeit, Handelszeit-Berechnung |
| test_storage.py | 5 | Jahrespartitionen, Duplikate, Laden nach Zeitraum, Neuberechnung UTC, Metadaten |
| test_strategies.py | 3 | Registrierung, Parameterprüfung, Signalprüfung, Datenzugriff |
| test_pipeline.py | 1 | Gesamtablauf inkl. zweitem Lauf (0 neue Kerzen) und Prüfung ohne MT5 |
| test_safety.py | 1 | kein `order_send`/`order_check` im Code |
| test_phase1.py | 2 | Phase-1-Skript |

Bei der Entwicklung gefundene und behobene Fehler: pandas 3 speichert Zeitstempel teils in Mikrosekunden
statt Nanosekunden (alle Umrechnungen sind jetzt auflösungsunabhängig); 1-Minuten-Lücken bei Bitcoin wurden
fälschlich als „Tagespause“ eingestuft; die Gewinnvalidierung wurde durch die Cent-Rundung von MT5 verfälscht.

## Bekannte Einschränkungen

1. **Historientiefe** hängt vom Broker und von „Max. Balken im Chart“ (aktuell 100 000) ab. Für tiefere
   M1/M5-Historie in MT5 unter Extras → Optionen → Charts auf „Unbegrenzt“ stellen, Terminal neu starten,
   `daten_aktualisieren.py` erneut starten – fehlende ältere Daten werden dann nachgeladen.
2. **Sommerzeit-Rückstellung:** Bei der Umstellung auf Winterzeit ist eine Stunde Serverzeit doppelt belegt;
   MT5 führt die Kerzen dieser Stunde zusammen. Bei Bitcoin (24/7) erscheint dadurch einmal im Jahr eine
   1-Stunden-Lücke in UTC. Gold ist nicht betroffen (Markt zu dieser Zeit geschlossen).
3. **Feiertage** folgen dem US-Kalender; abweichende Broker-Handelszeiten erscheinen als „unerklärt“ und
   werden im Bericht aufgelistet.
4. **Umrechnung** mit Schlusskursen; MT5 rechnet live mit Bid/Ask (Unterschied ≈ EURUSD-Spread, ~0,01 %).
5. **Swap und Kommission** werden erfasst, aber noch nicht in Gewinn/Verlust eingerechnet (Phase 3).
6. Die Rückwärtssuche endet nach 3 leeren Blöcken. Eine Historienlücke von mehr als 3 Blöcken
   (bei M1 ≈ 83 Tage) würde als Historienende gewertet.
7. Bei unbegrenzter Historie kann M1 mehrere Millionen Kerzen umfassen (Speicherbedarf beim Prüfen
   einige hundert MB).

## Echte Daten (VantageMarkets-Demo)

### Erster Lauf (18.10.2026) – Import

„Max. Balken im Chart“ wurde vorher auf „Unbegrenzt“ gestellt. Alle Reihen wurden bis zum tatsächlichen
Historienende geladen, ohne Fehler.

| Symbol | M1 | M5 | M15 | M30 | H1 | H4 | D1 | Historie ab (UTC) |
|---|---|---|---|---|---|---|---|---|
| XAUUSD | 2 867 052 | 578 980 | 196 930 | 101 421 | 53 669 | 16 158 | 5 104 | 22.06.2007 |
| BTCUSD | 3 620 637 | 734 410 | 247 833 | 125 552 | 64 309 | 16 207 | 2 802 | 01.01.2018 |
| EURUSD | 9 780 005 | 1 991 445 | 664 883 | 332 644 | 166 449 | 41 723 | 6 962 | 02.01.2000 |

- **Zeitschema:** `ny_close` (New York + 7 h; aktuell UTC+3, ab US-Winterzeit UTC+2), erkannt über
  Goldpause + Live-Abstand, Sicherheit **hoch**. Damit ist die Phase-1-Beobachtung „GMT+3“ bestätigt –
  aber nur für die US-Sommerzeit; im Winter gilt UTC+2.
- Gegenüber Phase 1 (50 000 Kerzen pro Abfrage) reicht M1 jetzt 19 Jahre (Gold) bzw. fast 9 Jahre (Bitcoin) zurück.

### Im ersten Lauf gefundene Fehler (behoben)

1. **BTCEUR wurde unnötig als Umrechnungssymbol geladen.** Ursache: Für die Margin wurde die von MT5
   gemeldete Marginwährung (BTC) umgerechnet. Bei Preis-CFDs liefert die Margin-Formel aber Preiseinheiten
   (USD). Behoben; der Ordner `data/market/BTCEUR` kann gelöscht werden.
2. **Speicherfehler (MemoryError) in der Qualitätsprüfung.** Ursache: Die komplette Zeitspalte wurde in
   Python-Objekte umgewandelt (bei Zeitzonen-Daten erzeugt pandas dabei Millionen Einzelobjekte).
   Gemessen bei 3,6 Mio. Kerzen: zusätzlicher Speicher 450 MB → 30 MB, Laufzeit halbiert.
3. **Umrechnungssymbole** werden nur noch in M5/H1/D1 geladen und geprüft (`conversion_timeframes`);
   M5 genügt für die EUR-Umrechnung (Fehler ≈ 0,02 %). Die bereits geladenen EURUSD-Ordner M1, M15, M30
   und H4 werden nicht mehr verwendet und können gelöscht werden.

### Qualitätsprüfung und Berechnungsabgleich

*Ausstehend – folgt aus dem zweiten Lauf mit der korrigierten Version.*
