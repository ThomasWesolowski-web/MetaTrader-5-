# Phase 4 – Ergänzung: zwei neue Strategien und neue Risikogrenzen

**Stand:** 20.10.2026 · **Freigabe:** Auftrag des Nutzers vom 20.10.2026 (zwei Strategien aus einem Video
„Die Rangliste 2022–2026“ ergänzen, 1 % Risiko je Trade, Drawdown-Grenze 20 %). Die offenen Punkte wurden vorher
geklärt (siehe unten).

**Sicherheit unverändert:** keine Orders an MetaTrader 5, kein Demo- und kein Echtgeldhandel. Alles hier ist
Simulation (Backtest) bzw. Beobachtung ohne Orders.

**Keine übernommenen Kennzahlen:** Die im Video genannten Ergebnisse (+174,9 % / −16,3 % bzw. +12,0 % / −21,6 %) sind
fremde Angaben ohne Regeln, Markt, Zeitrahmen oder Kosten (das Video nennt sie selbst „vorläufiger Forschungsstand“).
Sie werden nicht verwendet. Aussagekräftig sind erst die eigenen Backtest-Ergebnisse auf den Vantage-Daten mit
Kosten.

## 1. Risikogrenzen (gilt für Backtest und Beobachtung)

| Grenze | bisher (Phase 3) | neu |
|---|---|---|
| Referenzkapital | 1.000 € | 1.000 € (unverändert) |
| Risiko je Trade | 0,25 % (2,50 €) | **1 % (10 €)** |
| Tagesverlust | 1 % | **3 %** (Antwort des Nutzers; bei 1 % wäre nach einem Verlusttrade Schluss) |
| Drawdown-Grenze | 5 % | **20 %** |
| gleichzeitige Positionen | 2 | 2 (unverändert) |

- Die Werte stehen fest im Programm (`RiskRules` in `tradingbot/backtest/account.py`), nicht in `config.toml`. Ein
  Test prüft genau diese Werte, damit sie nicht unbemerkt geändert werden.
- Die Auswahlkriterien „geeignet für den Demohandel“ prüfen jetzt Drawdown ≤ **20 %** (Validierung und Test) statt
  5 %. Alle anderen Kriterien bleiben gleich. Der Abzug im Rangwert für hohen Drawdown beginnt ab 20 %.
- Die Risikovarianten im Bericht (0,25 % / 0,5 % / 1 %) bleiben. 0,25 % dient damit als Vergleich zum alten Stand.
- Beobachtung: Das Referenzkonto der laufenden Beobachtung wird ab dem nächsten Start mit den neuen Regeln berechnet,
  auch für bereits abgeschlossene Trades. Jeder Programmstart schreibt die Regeln ins Ereignisprotokoll.
- Mindestlot: Bei 10 € Risiko ist der zulässige Stop-Abstand mit 0,01 Lot viermal so groß wie bisher. Die
  Kontraktprüfung im Dashboard zeigt das, sobald die Beobachtung neu startet.

## 2. Neue Strategien

### `ema_vwap` – EMA 20 + Wochen-VWAP

| | |
|---|---|
| Long | Schlusskurs über der EMA 20 **und** über dem Wochen-VWAP; Einstieg auf der ersten Kerze, auf der beides zutrifft (Ausführung zur Eröffnung der Folgekerze) |
| Short | spiegelbildlich (unter EMA 20 und unter Wochen-VWAP) |
| Stop | Schluss − `atr_mult` × ATR(14) |
| Ausstieg | Schluss wieder unter der EMA 20 (Short: darüber), sonst Stop |
| Wochen-VWAP | (Hoch+Tief+Schluss)/3, gewichtet mit dem **Tick-Volumen** aus MT5 (echtes Handelsvolumen liefert MT5 für Gold und Bitcoin nicht); Neustart jeden Sonntag 17:00 New Yorker Zeit; die erste, unvollständige Woche in den Daten wird nicht verwendet |
| Parameter | `atr_mult` 1,5 oder 2,5 (Auswahl im Walk-Forward nur anhand der Trainingsdaten) |
| Zeitrahmen | M15, M30, H1, H4 |

### `opening_range_breakout` – Opening Range Breakout

| | |
|---|---|
| Range | Hoch und Tief der ersten `range_min` Minuten (15, 30 oder 60) ab der Eröffnung |
| Eröffnungen | London 08:00 (Londoner Zeit), New York 09:30 (New Yorker Zeit), jeweils mit Sommerzeit, oder 00:00 UTC |
| Einstieg | erster Schlusskurs über dem Range-Hoch → Long, unter dem Range-Tief → Short; höchstens **ein Trade je Tag** |
| Stop | Gegenseite der Range |
| Ziel | `rr` × Risiko, `rr` 1,5 oder 2,0 |
| Tagesende | geschlossen spätestens zum Handelsschluss: London 16:30, New York 16:00, UTC 24:00; danach keine neuen Einstiege |
| Datenlücken | Tage mit fehlenden Kerzen in der Range werden nicht gehandelt |
| Parameter | 3 Eröffnungen × 3 Range-Längen × 2 Ziele = 18 Sätze, Auswahl im Walk-Forward nur anhand der Trainingsdaten |
| Zeitrahmen | M1, M5, M15 (auf gröberen Zeitrahmen lässt sich eine 15-Minuten-Range nicht bilden) |

**Hinweis:** Der ORB hat mit 18 Parametersätzen deutlich mehr Auswahl als die übrigen Strategien (2–4). Mehr Auswahl
erhöht die Gefahr, dass ein Satz nur zufällig gut aussieht (Überoptimierung). Die Prüfung auf Validierungs- und
Testdaten, die bei der Auswahl nicht verwendet werden, bleibt dieselbe.

## 3. Änderungen im Backtest-Ablauf

- Jede Strategie läuft nur auf ihren eigenen Zeitrahmen. Die zwölf Phase-3-Strategien bleiben bei M1–H1.
- Standard-Zeitrahmen von `backtest_ausfuehren.py` jetzt M1, M5, M15, M30, H1 **und H4**. Auf H4 läuft nur `ema_vwap`.
- Testeinheiten bei vollem Lauf: 12 × 5 × 2 = 120 (wie bisher) + `ema_vwap` 4 × 2 = 8 + ORB 3 × 2 = 6 → **134**.
- Der Backtest lädt zusätzlich die Spalte `tick_volume` (für den VWAP).
- Blockweise Berechnung: Für den Wochen-VWAP wird der Vorlauf auf mindestens 10 Tage verlängert, damit die Woche am
  Blockanfang vollständig ist (sonst wäre der VWAP auf M1 an Blockgrenzen falsch). Ein Test prüft das.
- Weil sich Code und Risikoregeln geändert haben, werden **alle** Einheiten neu berechnet. Der Abgleich mit Lauf 2
  meldet deshalb Abweichungen. Das ist erwartet (anderes Risiko = andere Positionsgrößen). Die Laufzeit wurde auf
  dem PC noch nicht gemessen. Sie wird über den bisherigen ~26 Minuten liegen, vor allem wegen des ORB auf M1.
- Das Dashboard zeigt bis zum neuen Lauf weiterhin den alten Lauf mit 0,25 %; die Überschrift nennt das Risiko des
  jeweils angezeigten Laufs.

## 4. Tests

**204 Tests bestanden** (vorher 188).

| Neu/geändert | Prüft |
|---|---|
| `test_neue_strategien.py` (10, neu) | Wochen-VWAP von Hand nachgerechnet (Neustart So 17:00 NY, erste Woche leer, Kerze ohne Volumen); EMA/VWAP-Regeln Kerze für Kerze; ORB New York (Range, Einstieg, Stop, Ziel, Schließen 16:00 NY), London mit Sommerzeit, nur ein Trade je Tag, unvollständige Range und zu grober Zeitrahmen → kein Trade; Zeitrahmen je Strategie; Blockberechnung mit sehr kurzem Vorlauf = Gesamtberechnung; freigegebene Risikowerte |
| bestehende Prüfungen | Kein Blick in die Zukunft und Block = Gesamtberechnung laufen automatisch auch für beide neuen Strategien |
| `test_account.py` | Rechenbeispiele nutzen die alten Werte jetzt als ausdrückliche Testparameter (geprüft wird die Rechnung, nicht die Grenze) |
| `test_backtest_e2e.py` | Kostenvergleich nur bei identischen Trades (gleiche Anzahl **und** gleiche Sperrtage). Bei 1 % Risiko kann die Tagesverlustgrenze je Kostenszenario an anderen Tagen greifen |

Zusätzlich lief ein vollständiger Backtest mit beiden Strategien auf simulierten Kursen (M5, M15, H1, H4) ohne
Fehler. Die Ergebnisse dieses Laufs sind ohne Aussagekraft (Zufallskurse) und werden nicht berichtet.

## 5. Offen

1. **Backtest auf dem PC** mit den echten Daten ausführen; erst danach gibt es Ergebnisse zu den beiden Strategien.
2. **Beobachtung:** Die neuen Strategien sind noch **nicht** in der Beobachtung. Sinnvoll erst, wenn der Backtest
   einen Parametersatz liefert; dann werden sie nur auf Freigabe in `config.toml` unter `[paper]` ergänzt.
3. Punkt 3 (Kapital/Risiko) ist mit dieser Freigabe für das Risiko je Trade, den Tagesverlust und den Drawdown
   entschieden. Das Referenzkapital bleibt 1.000 €.
