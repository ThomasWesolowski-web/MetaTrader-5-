# Phase 2 – Datenbasis: Ergebnis

**Stand:** 18.10.2026 · **Trades ausgeführt:** keine (weder Demo noch Echtgeld)
**Korrektur (Phase 3):** Die Gold-Intraday-Historie (M1–H1) ist **nicht** lückenlos ab 2007 – siehe
[Korrektur](#korrektur-gold-intraday-historie). **Status:** abgeschlossen. Echte Daten von VantageMarkets-Demo geladen und geprüft
(Gold ab 2007, Bitcoin ab 2018 in M1), 75 automatisierte Tests bestanden.

## Einordnung

Die Software wurde in einer Linux-Cloud-Umgebung ohne Zugriff auf das MT5-Terminal entwickelt und dort gegen
einen simulierten Markt getestet. Ausgeführt wurde sie auf dem Windows-PC gegen VantageMarkets-Demo; die
Ergebnisse im Abschnitt [Echte Daten](#echte-daten-vantagemarkets-demo) stammen aus diesen Läufen
(Datenbericht vom 18.10.2026, 12:59 UTC). Es wurden keine Kursdaten erfunden, aufgefüllt oder verändert.

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

`python -m pytest` – 75 Tests, alle bestanden (Laufzeit ca. 30 s):

| Datei | Tests | Prüft |
|---|---|---|
| test_importer.py | 12 | gespeicherte Daten = MT5-Daten (nichts erfunden), Blockgrößen, Halbierung abgelehnter Abfragen, echte UTC-Zeiten bei zwei Serverschemata, inkrementelles Update lädt exakt die neuen Kerzen, laufende Kerze wird nicht gespeichert, Nachladen erweiterter Historie, Fortsetzen nach Abbruch, Neuberechnung bei Schemawechsel |
| test_server_time.py | 21 | Abstände inkl. Sommerzeit, Hin-/Rückrechnung, MT5-Semantik, Live-Abstand, Erkennung von `ny_close`, `eu_dst` und festem Abstand mit/ohne Live-Messung |
| test_quality.py | 9 | nur die eingebaute echte Lücke ist unerklärt, Wochenenden/Tagespausen/5 Feiertage erkannt, Bitcoin-Wartung und Minuten ohne Ticks, Fehltick gefunden, Wochenend-Gap kein Ausreißer, ruhiger Markt bläht Ausreißer nicht auf, Broker-Schließzeiten bei Bitcoin, Integritätsfehler, manipulierte Kerze in der Konsistenzprüfung |
| test_instruments.py | 12 | Gewinn in EUR, Positionsgröße/Abrundung, Margin-Modi (inkl. FOREX_NO_LEVERAGE wie Vantage-BTCUSD), Umrechnung nur mit vergangenen Kursen, Kreuzkurs BTC→USD→EUR, Wahl des Umrechnungssymbols, Abgleich mit Terminal-Rechenfunktionen |
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

1. **Historientiefe** hängt vom Broker und von „Max. Balken im Chart“ ab (auf dem PC: „Unbegrenzt“).
   Wird die Einstellung zurückgesetzt, bleiben die lokal gespeicherten Daten erhalten.
2. **Sommerzeit-Rückstellung:** Bei der Umstellung auf Winterzeit ist eine Stunde Serverzeit doppelt belegt;
   MT5 führt die Kerzen dieser Stunde zusammen. Bei Bitcoin (24/7) erscheint dadurch einmal im Jahr eine
   1-Stunden-Lücke in UTC. Gold ist nicht betroffen (Markt zu dieser Zeit geschlossen).
3. **Feiertage** folgen dem US-Kalender (Gold) bzw. Weihnachten/Neujahr (Bitcoin). Abweichende
   Broker-Handelszeiten erscheinen als „regelmäßige Pause“ oder „unerklärt“ und werden im Bericht aufgelistet.
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

### Beim Lauf auf dem PC gefundene Fehler (behoben)

1. **Speicherfehler (MemoryError) in der Qualitätsprüfung.** Ursache: Die komplette Zeitspalte wurde in
   Python-Objekte umgewandelt (bei Zeitzonen-Daten erzeugt pandas dabei Millionen Einzelobjekte).
   Gemessen bei 3,6 Mio. Kerzen: zusätzlicher Speicher 450 MB → 30 MB, Laufzeit halbiert.
2. **Umrechnungssymbole** werden nur noch in M5/H1/D1 geladen und geprüft (`conversion_timeframes`);
   M5 genügt für die EUR-Umrechnung (Fehler ≈ 0,02 %). Nicht mehr benötigt: `data/market/EURUSD/M1, M15, M30, H4`.
3. **Margin bei Bitcoin:** Vantage rechnet BTCUSD im Modus `FOREX_NO_LEVERAGE` ab (Margin = Volumen ×
   1 BTC × Marginsatz, also in BTC). Dafür wurde BTCEUR geladen – dessen Historie beginnt aber erst im
   Juli 2021. Jetzt wird über den Kreuzkurs BTCUSD × EURUSD umgerechnet (ab 2018 verfügbar, kein
   Zusatzsymbol). Nicht mehr benötigt: `data/market/BTCEUR`.
4. **Aufgeblähte Ausreißer bei Bitcoin:** In ruhigen Phasen (z. B. Juli 2020) war mehr als die Hälfte der
   M1-Renditen 0, der Median damit 0 – Bewegungen von 0,2 % erhielten Scores um 20 000. Die Skala nutzt jetzt
   zusätzlich den Mittelwert. Die Zahl der Ausreißer bei BTCUSD M1 (vorher 10 313) sinkt dadurch deutlich.
5. **Broker-Schließzeiten bei Bitcoin** wurden als „unerklärt“ gezählt: jährlich Weihnachten und Neujahr
   (jeweils 24 h) sowie in den Jahren 2018–2020 viele Wochenenden (Fr ~22:00 bis So ~22:00 UTC). Diese
   werden jetzt als Feiertag bzw. Wochenende (Broker geschlossen) eingeordnet.

Nach diesen Korrekturen ändern sich nur die Zählungen bei Bitcoin-Lücken und -Ausreißern; der Datenbestand
selbst bleibt unverändert. Ein erneuter Lauf (`python daten_aktualisieren.py`) aktualisiert die Zahlen.

### Datenqualität (Bericht vom 18.10.2026)

| | XAUUSD | BTCUSD | EURUSD |
|---|---|---|---|
| Duplikate / ungültige OHLC / Preise ≤ 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Konsistenz M5…D1 mit M1 (Schluss, High/Low) | 100 % | 100 % | – |
| Abdeckung der Handelszeit M1 | 99,96 % | 98,46 %¹ | 99,94 % (M5) |
| unerklärte Lücken M1 | 12 (46,5 h) | 1 059 (1 188 h)¹ | 92 (M5, 106 h in 26 Jahren) |

¹ vor Korrektur 5 (Broker-Schließzeiten wurden noch als unerklärt gezählt).

**Echte Datenlücken (Broker-Historie):** Mehrere Lücken treten gleichzeitig bei verschiedenen Symbolen auf –
das spricht für Ausfälle in der Kurshistorie des Brokers, nicht für Marktschließungen:

| Zeitraum (UTC) | betroffen |
|---|---|
| 09.03.2020 21:00 – 11.03.2020 22:00 | XAUUSD (25 h), BTCUSD (48 h) – während des Corona-Crashs |
| 04.08.2021, 06.08.2021, 08./09.08.2021 | XAUUSD (insgesamt ca. 16 h) |
| 07./08.09.2023 | XAUUSD, BTCEUR (je 1–2 h) |
| 08.03.2018 – 11.03.2018 | BTCUSD (71 h, Beginn der Historie) |

**Gold – regelmäßige Pausen:** Viele tägliche Pausen in Golds Historie weichen um mehr als 20 Minuten vom
CME-Zeitplan ab und werden daher als „regelmäßige Pause“ statt „Tagespause“ erkannt (beide gelten als
erwartet). Die Broker-Handelszeiten für Gold entsprechen also nicht über die gesamte Historie exakt den CME-Zeiten.

**Ausreißer sind überwiegend echte Marktereignisse** und werden deshalb nur markiert, nicht entfernt:
US-Arbeitsmarktdaten (z. B. Gold 04.08.2023, 06.12.2019, 04.09.2026, jeweils 12:30/13:30 UTC),
Wochenöffnung nach der Iran-Eskalation (Gold 05.01.2020 23:01 UTC), Goldeinbruch −5,7 % am 29.01.2026,
Corona-Crash (BTCUSD 12./13.03.2020, −22 % in 4 h), Bitcoin-Crash 17.08.2023 (−9 % in 5 Min.).
Einzelne „spike“-Markierungen (M1: Gold 112, Bitcoin 771 vor Korrektur) können Fehlticks sein – für
Backtests auf M1 sollten Strategien keine Ausführung zu einem einzelnen Extremkurs annehmen.

### Abgleich der EUR-Berechnung mit dem Terminal

| | XAUUSD | BTCUSD |
|---|---|---|
| Gewinn 1 Lot bei +1 % Kurs (eigene / MT5) | 3 706,54 € / 3 706,04 € (+0,013 %) | 729,50 € / 729,60 € (−0,014 %) |
| Tickwert 1 Lot (eigene / MT5) | 0,88971 € / 0,88959 € (+0,013 %) | 0,008897 € / 0,008896 € (+0,013 %) |
| Margin 1 Lot (MT5) / effektiver Hebel | 741,39 € / 1:500 | 145,98 € / 1:500 |
| Berechnungsmodus | CFDLEVERAGE | FOREX_NO_LEVERAGE (Marginsatz 0,002) |
| Swap long / short (MT5-Angabe) | −77,44 / +36,70 | −20 / 0 |

Die Abweichung von ~0,013 % entspricht dem EURUSD-Spread (eigene Rechnung mit Mittelkurs, MT5 mit Bid/Ask)
und ist für Backtests vernachlässigbar.

### Konsequenzen für Phase 3

- Backtests können auf M1 (Gold ab 2007, Bitcoin ab 2018) oder höheren Zeitrahmen laufen; die Zeitrahmen
  sind untereinander zu 100 % konsistent.
- Während echter Datenlücken (Tabelle oben) und Broker-Schließzeiten darf der Backtester keine Ausführung
  annehmen; Stops über eine Lücke hinweg werden zum ersten Kurs danach ausgeführt (Gap-Risiko).
- Swap (Gold long −77,44 je Lot und Tag laut MT5) ist bei mehrtägigen Positionen ein relevanter Kostenfaktor
  und muss in Phase 3 eingerechnet werden.
- Bitcoin hatte 2018–2020 bei Vantage keinen Wochenendhandel – Ergebnisse aus dieser Zeit sind für
  Wochenendstrategien nicht repräsentativ.

## Korrektur: Gold-Intraday-Historie

**Stand nach dem Datenbericht vom 18.10.2026, 14:13 UTC.**

Die Gold-Intraday-Daten (M1–H1) beginnen zwar 2007 und haben keine Lücke länger als 4 Tage, sind aber nur
**zu 41–46 % dicht**: XAUUSD H1 hat 53 669 von 115 941 laut Handelskalender möglichen Kerzen (D1 dagegen vollständig).
Die fehlende Zeit lag in 2 670 wiederkehrenden Lücken mit zusammen 83 640 Stunden (Ø 31 h) – diese wurden als
„regelmäßige Pause“ eingestuft und damit als unbedenklich gezählt. Die Aussage „Gold M1 ab 2007“ war daher
irreführend, die frühere Abdeckung von 99,96 % zu optimistisch.

Verlauf der Korrektur (ehrlich dokumentiert):

1. Erste Vermutung (Phase 3, Lauf 1): ein etwa zehnjähriges Loch, verdeckt durch die Feiertagsregel. Die
   Feiertagsregel war tatsächlich fehlerhaft (lange Lücken berührten Feiertage) und wurde behoben – **ein solches
   Loch gibt es aber nicht**; diese Vermutung war falsch.
2. Tatsächliche Ursache: Die Regel „regelmäßige Pause“ akzeptierte wiederkehrende Lücken beliebiger Länge.
   Jetzt gilt sie nur bis 6 h (Wartungsfenster), Wochenendschließungen bei 24/7-Märkten erst ab 24 h; längere
   Lücken zählen als **unerklärt**. Regressionstests sichern beides ab.
3. Neu im Datenbericht: **Dichte je Jahr** und **dicht ab** (erster Monat, ab dem die monatliche Dichte durchgehend
   ≥ 60 % des Medians der letzten 24 Monate liegt). Backtests beginnen frühestens dort.

In welchen Jahren die Gold-Daten dünn sind, zeigt der nächste Lauf von `daten_aktualisieren.py` (Tabelle
„Datendichte je Jahr“). Bitcoin (Dichte 78–87 %, mit dokumentierten Schließzeiten) und EURUSD (99 %) sind unauffällig.
