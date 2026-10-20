# Phase 4 – Dashboard, Beobachtung (Paper-Trading) und Kontraktprüfung

**Stand:** 18.10.2026 · **Freigabe:** Punkt 1 und 2 umgesetzt, Punkt 3 nur geprüft und dokumentiert (Entscheidung
offen), Punkt 4 nicht bearbeitet. Arbeitsplan: [phase4_arbeitsplan.md](phase4_arbeitsplan.md).

> **Ergänzung 20.10.2026:** zwei neue Strategien (EMA 20 + Wochen-VWAP, Opening Range Breakout) und neue
> Risikogrenzen (1 % je Trade, 3 % Tagesverlust, 20 % Drawdown) – siehe
> [phase4_ergaenzung_strategien.md](phase4_ergaenzung_strategien.md). Angaben zu 0,25 % / 5 % weiter unten
> beschreiben den Stand vom 18./19.10.2026.

**Sicherheit:** Es werden keine Orders an MetaTrader 5 gesendet, kein Demo- und kein Echtgeldhandel. Der MT5-Zugriff
ist rein lesend (`MT5Client` enthält keine Order-Funktion; ein Test durchsucht alle Programmdateien nach
`order_send`/`order_check`). Die Risikogrenzen sind unverändert und für die Beobachtung nicht konfigurierbar
(1.000 € Referenzkapital, 0,25 % je Trade, 1 % Tagesverlust, 5 % Drawdown-Grenze, max. 2 Positionen).

**Wichtig:** Alles wurde in der Entwicklungsumgebung mit einem simulierten MT5-Terminal getestet. Echte
Beobachtungsdaten gibt es erst, wenn `beobachtung_ausfuehren.py` auf dem PC mit dem MT5-Terminal läuft.

## 1. Streamlit-Dashboard (Punkt 1)

Start: `streamlit run dashboard.py` (öffnet sich im Browser, nur lokal erreichbar; Nutzungsstatistiken an Streamlit
sind in `.streamlit/config.toml` abgeschaltet).

| Bereich | Inhalt | Quelle |
|---|---|---|
| Übersicht | Ergebnis je Markt, Vollständigkeit (120/120), Zustand der Beobachtung (Warnung, wenn sie nicht läuft), Datenstand | wie unten |
| **Backtest (Phase 3)** – blau gekennzeichnet als „Historische Simulation“ | Rangliste je Markt (Filter Zeitrahmen, nur qualifizierte), je Kombination: Trades, Netto €, Rendite, Profitfaktor, Trefferquote, Erwartungswert, Drawdown % und €, Spread/Kommission/Swap, Mindestlot-Quote für Training/Validierung/Test in beiden Kostenszenarien; Walk-Forward-Abschnitte (Diagramm), Jahresergebnisse, Risikovarianten; Kosten je Zeitrahmen | `reports/phase3_ergebnisse.json`, sonst `docs/phase3_zusammenfassung.json` |
| **Live-Beobachtung** – gelb gekennzeichnet als „hypothetisch, keine Orders, getrennt vom Backtest“ | Kandidaten mit Ergebnis je 0,01 Lot und im Referenzkonto, kumuliertes Ergebnis (Diagramm), alle Trades mit Kosten, alle Signale inkl. nicht gehandelter, Ereignisprotokoll | `data/paper/*.json` |
| Daten | Aktualität je Symbol/Zeitrahmen (letzte Kerze, Alter, letzter Import), Datenqualität (Dichte, Lücken, Duplikate, ungültige Kerzen, Ausreißer, Abdeckung) | `data/market/*/_meta.json`, neuester `reports/phase2_datenbericht_*.json` |
| Kontrakt & Mindestlot | Spezifikationen und Stop-Grenzen (Punkt 3) | `data/paper/kontraktpruefung.json` (live) oder `data/specs.json` + gespeicherte Kurse |

**Keine erfundenen Kennzahlen:** Das Dashboard rechnet keine Ergebnisse nach und schätzt nichts. Fehlt ein Wert in
der Quelle, bleibt die Zelle leer. Das betrifft derzeit den **Drawdown in €** des Backtests: Phase 3 hat ihn bisher
nicht gespeichert. Er ist jetzt als Kennzahl `max_drawdown_eur` ergänzt (aus derselben Kontokurve wie der Drawdown in
%, mit Test) und erscheint nach dem nächsten Backtest-Lauf. Weil sich dadurch der Code-Fingerabdruck ändert, rechnet
dieser Lauf alle 120 Einheiten neu (wie Lauf 3 etwa 26 Minuten).

**Speicher:** Das Dashboard liest nur die Ergebnisdateien und den Importstatus. Nur die Kontraktprüfung ohne
Live-Daten liest die letzten Kerzen je Zeitrahmen. Gemessen: ca. **180 MB** Arbeitsspeicher (mit Testdaten).

## 2. Beobachtung / Paper-Trading (Punkt 2)

Start: `python beobachtung_ausfuehren.py` (Durchlauf jede Minute, Strg+C beendet). Optionen laut `--help`:
`--einmal`, `--intervall SEKUNDEN` (mindestens 10), `--status` (Stand ohne MT5 anzeigen), `--config`.

### Beobachtete Kandidaten (Parameter unverändert aus Phase 3)

| Zeitrahmen | Strategie | Parameter |
|---|---|---|
| H1 | `kombi_breakout_momentum` | n 55, rr 3,0 |
| H1 | `donchian_breakout` | n 55, atr_mult 2,5 |
| H1 | `ema_trend` | fast 50, slow 200, atr_mult 3,0 |
| M30 | `ema_trend` | fast 50, slow 200, atr_mult 3,0 |

Das sind die vier Parametersätze, die Phase 3 als Forschungskandidat „Bitcoin-Trendfolge auf M30/H1“ nennt. Die Liste
steht in `config.toml` unter `[paper]` und lässt sich dort verkleinern. Werden die Parameter eines laufenden Kandidaten
geändert, wird die Beobachtung nicht vermischt, sondern ein Fehler protokolliert.

### Umgesetzte Funktionen

| Funktion | Umsetzung |
|---|---|
| Neue Live-Kurse | Kerzen nur lesend aus MT5 (`copy_rates_range`), Umrechnung EURUSD M5 ebenso |
| Nur abgeschlossene Kerzen | Kerzenende ≤ Uhrzeit **und** ein Tick nach dem Kerzenende (bzw. keine aktuellen Ticks). Die laufende Kerze wird nie verwendet |
| Signale | dieselbe Strategie-Klasse wie im Backtest; 3.000 Kerzen Vorlauf für die Indikatoren |
| Kein Look-ahead | nur Signale auf Kerzen ab Beobachtungsbeginn; jedes Signal wird beim ersten Erkennen mit Zeitstempel und Verzögerung festgehalten; Signale, die erst nachträglich auftauchen (Kursdaten geändert), oder die später als 5 Minuten nach Kerzenschluss erkannt werden (Programm lief nicht), werden **nicht gehandelt**, sondern als Abweichung bzw. „verspätet“ protokolliert |
| Ein-/Ausstiege | dieselbe Engine wie Phase 3: Einstieg zur Eröffnung der Folgekerze (Long zum Ask + Slippage), Stop vor Ziel in derselben Kerze, Ausführung zum Eröffnungskurs bei Kurslücken, kein Einstieg über Datenlücken, max. 2 Positionen. Abgeschlossene Trades werden eingefroren |
| Kosten je Trade | Spread (MT5-Kerzenspread × 1,5 wie Szenario „konservativ“), Slippage (Annahme 0,015 % je Ausführung), Kommission (Annahme 0,04 % vom Nominalwert hin und zurück), Swap (aktuelle MT5-Werte, Nächte nach MT5-Regel). Annahmen sind im Dashboard als solche bezeichnet |
| Ergebnis | je Trade: Brutto, Spread, Slippage, Kommission, Swap, Netto in € je **0,01 Lot**, Risiko in € und R-Vielfaches, „innerhalb 0,25 %“ ja/nein; je Kandidat: Summe, Profitfaktor, Ø R, Drawdown in € und % |
| Referenzkonto 1.000 € | dieselbe Kontosimulation wie Phase 3 mit den unveränderten Regeln; je Trade „ausgeführt (Volumen, Netto)“ oder Grund (z. B. Mindestlot) |
| Markt geschlossen / Datenlücken | Tick älter als 5 Minuten → Ereignis „keine aktuellen Kurse“, Rückkehr → „Kurse aktuell“; fehlende Kerzen → Ereignis „Datenlücke“ mit Anzahl. Es werden keine Kurse erzeugt; in Lücken wird nichts ausgeführt |
| Ablage | `data/paper/`: `state.json`, `ereignisse.jsonl` (nur angehängt), `trades.json`, `status.json`, `kontraktpruefung.json`. Atomar geschrieben, gespeichert nach jedem Kandidaten; ein Neustart setzt ohne doppelte Ereignisse fort |
| Verbindung | ohne Ticks: Ereignis und neuer Verbindungsaufbau; Fehler eines Kandidaten halten die anderen nicht auf |
| Speicher | ca. **16 MB** zusätzlich zum Python-Grundbedarf, ca. 65 ms je Durchlauf (gemessen mit 4 Kandidaten und 3.000 Kerzen Vorlauf) |

### Installation und automatischer Start (Ergänzung 19.10.2026)

| Datei | Funktion |
|---|---|
| `INSTALLIEREN.bat` | Installer: prüft Python 3.11+ (installiert es bei Bedarf über winget) und MetaTrader 5, fragt den Zielordner ab (Standard `C:\Trading\TradingBot`, jedes Laufwerk möglich, freier Speicher wird angezeigt), beendet eine laufende Beobachtung, kopiert die Programmdateien, übernimmt auf Wunsch Kursdaten, Berichte und Beobachtungsstand aus der bisherigen Installation (Verschieben, auf demselben Laufwerk ohne zusätzlichen Platz), richtet Python-Umgebung und Pakete ein, legt Desktop- und Startmenü-Symbole an, optional Autostart. Erneut ausführen = Aktualisierung |
| `START.bat` („Trading starten“) | aktualisiert die Kursdaten, startet Beobachtung und Dashboard in eigenen, minimierten Fenstern (nur wenn sie nicht schon laufen) und öffnet den Browser |
| `STOP.bat` („Trading stoppen“) | beendet die Beobachtung sauber (Stopp-Anforderung, Stand bleibt gespeichert) und schließt das Dashboard; MT5 bleibt offen |
| `DEINSTALLIEREN.bat` (Startmenü) | entfernt Symbole und Programm; Daten nur nach ausdrücklicher Bestätigung |

Für den Betrieb ohne Aufsicht wurde die Beobachtung robuster gemacht (mit Tests): Sie läuft nie doppelt
(Sperrdatei), wartet beim Start auf MT5 und versucht es jede Minute erneut, bricht bei einem Fehler in einem
Durchlauf nicht ab und reagiert innerhalb von Sekunden auf „Trading stoppen“. Die Installations- und Startdateien
konnten in der Entwicklungsumgebung nur auf Syntax und Aufbau geprüft werden (PowerShell-Parser, Zeilenenden,
Zeichensatz), nicht unter Windows ausgeführt.

## 3. Kapital und Risiko – Prüfung (Punkt 3, Entscheidung offen)

Beim Start liest `beobachtung_ausfuehren.py` die Spezifikationen von XAUUSD und BTCUSD **live** aus MT5, vergleicht
sie mit `data/specs.json` (Abweichungen werden protokolliert) und schreibt in `data/paper/kontraktpruefung.json`, bis
zu welchem Stop-Abstand das Risiko von 2,50 € (0,25 % von 1.000 €) mit der Mindestlotgröße eingehalten wird. Angegeben
werden der Abstand in USD, in % vom Kurs und als Vielfaches der aktuellen ATR(14) je Zeitrahmen. Die Kommission ist dabei
als Annahme eingerechnet. Der Abstand zählt ab dem tatsächlichen Einstiegskurs, der Spread ist also enthalten.

Bekannte Werte aus Phase 2/3 (Vantage-Demo; die Live-Prüfung auf dem PC aktualisiert sie):

| | XAUUSD | BTCUSD |
|---|---|---|
| Kontraktgröße | 100 | 1 |
| Mindestlot | 0,01 | 0,01 |
| Berechnungsmodus | CFDLEVERAGE | FOREX_NO_LEVERAGE (Marginsatz 0,002) |
| max. Stop-Abstand bei 0,01 Lot und 2,50 € (Phase 3, ohne Kommission) | **2,81 USD** | **280,95 USD** |

Als Vielfaches der ATR(14) (Median im Phase-3-Testzeitraum):

| Zeitrahmen | M1 | M5 | M15 | M30 | H1 |
|---|---|---|---|---|---|
| XAUUSD: max. Stop in ATR | 1,8 | 0,7 | 0,4 | 0,3 | 0,2 |
| BTCUSD: max. Stop in ATR | 6,3 | 2,3 | 1,2 | 0,8 | 0,6 |

Folge für die beobachteten Kandidaten: Ihre Stops liegen bei 2–3 ATR. Auf BTCUSD H1 entspricht das bei 0,01 Lot rund
8,85–13,30 € Risiko (0,9–1,3 % von 1.000 €), auf M30 (3 ATR) rund 9,10 €. Das ist mehr als die freigegebenen 2,50 €.
Im Referenzkonto werden diese Trades deshalb fast immer als „nicht ausführbar (Mindestlot)“ erscheinen. Die Beobachtung
bewertet sie trotzdem vollständig je 0,01 Lot, damit Erkenntnisse über die Strategie entstehen. **An Kapital und
Risikogrenzen wurde nichts geändert.**

## 4. Tests

**182 Tests bestanden**: 160 bisherige und 22 neue.

| Neu | Prüft |
|---|---|
| `test_paper.py` (13) | Live-Beobachtung Schritt für Schritt = nachträgliche Gesamtrechnung (identische Trades); laufende Kerze nie verwendet; Signale erst nach Kerzenschluss erkannt und kausal (entstehen auch nur mit Daten bis zur Signalkerze); in einer Programmpause entstandene Signale werden nicht gehandelt; veraltete Ticks werden gemeldet; Datenlücke wird mit Anzahl protokolliert, kein Einstieg in der Lücke; Neustart ohne doppelte Ereignisse; geänderte Parameter werden nicht vermischt; Kostenaufteilung (Netto = Brutto − Kommission + Swap); Referenzkonto-Zuordnung; Risikoregeln nicht konfigurierbar; fehlender Umrechnungskurs → keine €-Werte statt geschätzter; Kontraktprüfung rechnet richtig; Startdatei `--einmal` und `--status` ohne Order-Aufrufe |
| `test_dashboard.py` (8) | Datenquellen und Vorrang; Ranglisten und Kennzahlen stimmen mit den Phase-3-Dateien überein (u. a. Kosten XAUUSD M1 = Bericht); Drawdown in € ohne Quelle bleibt leer; Datenqualität und Aktualität; Kontraktprüfung aus gespeicherten Daten; Beobachtungstabellen; **Start der Oberfläche** (Streamlit AppTest) mit getrennten Bereichen, auch ganz ohne Daten |
| `test_account.py` (+1) | Drawdown in € passt zur Kontokurve und zum Drawdown in % |

Zusätzlich wurde das Dashboard mit simulierten Beobachtungsdaten im Browser geöffnet; alle fünf Bereiche wurden
angezeigt.

## 5. Offene Punkte und bekannte Einschränkungen

1. **Noch keine echten Beobachtungsdaten.** Die Beobachtung muss auf dem PC laufen (MT5 geöffnet und eingeloggt). Für
   belastbare Aussagen braucht es viele Wochen: Die H1-Kandidaten erzeugen nur wenige Signale pro Monat.
2. **Der PC muss laufen.** Signale, die später als 5 Minuten nach Kerzenschluss erkannt werden, werden bewusst nicht
   gehandelt. Ausstiege offener Positionen werden auch nach einer Pause aus den Kerzen bestimmt, als lägen Stop und Ziel
   beim Broker; solche Ausstiege sind als „verspätet erkannt“ markiert.
3. **Punkt 3 offen:** Mit 1.000 € und 0,25 % sind die beobachteten BTC-Trades mit 0,01 Lot fast nie zulässig (siehe
   Abschnitt 3). Das ist eine Entscheidung für dich. Ich ändere hier nichts.
4. **Kostenannahmen:** Kommission und Slippage für Vantage sind nicht bekannt und als Annahmen geführt. Der
   MT5-Kerzenspread ist typischerweise der kleinste Spread der Kerze (eher optimistisch), daher × 1,5. Zum Vergleich wird beim Erkennen
   eines Signals der aktuelle Bid/Ask-Kurs mitprotokolliert.
5. **Drawdown in € im Backtest** erscheint erst nach einem neuen Backtest-Lauf (siehe Abschnitt 1).
6. **Auslegung „die beiden Kandidaten“:** Beobachtet werden die vier Parametersätze der Kandidatengruppe auf H1 und
   M30 (siehe Tabelle). Sollen es nur zwei sein, genügt es, in `config.toml` zwei Einträge zu entfernen, **bevor** die
   Beobachtung startet.
7. **Kurze Broker-Pausen** (z. B. Wartung) erscheinen im Protokoll als Datenlücken oder „keine aktuellen Kurse“. Das
   ist gewollt: lieber protokollieren als Kurse annehmen.

## Nächste Schritte (nach deiner Freigabe)

1. Auf dem PC: `pip install -r requirements.txt` (neu: `streamlit`), `python daten_aktualisieren.py`, dann
   `python beobachtung_ausfuehren.py` dauerhaft laufen lassen und `streamlit run dashboard.py` öffnen.
2. Optional: `python backtest_ausfuehren.py` erneut ausführen, damit der Drawdown in € im Dashboard erscheint.
3. Nach einigen Wochen: erste Beobachtungsdaten gemeinsam auswerten; danach Entscheidung zu Punkt 3 und Punkt 4.
