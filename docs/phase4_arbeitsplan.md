# Phase 4 – Arbeitsplan

**Stand:** 18.10.2026 · **Freigabe:** Punkt 1 (Dashboard) und Punkt 2 (Paper-Trading) freigegeben, Punkt 3
(Kapital/Risiko) offen, Punkt 4 (Strategieweiterentwicklung) vorerst nicht.

## Verbindliche Regeln

- **Keine Orders an MT5**, kein Demo- und kein Echtgeldhandel. Der MT5-Zugriff bleibt rein lesend (`MT5Client` hat
  weiterhin keine Order-Funktion; der Sicherheitstest prüft alle Dateien auf `order_send`/`order_check`).
- **Risikogrenzen unverändert:** 1.000 € nur als Referenzkapital, 0,25 % Risiko je Trade, 1 % Tagesverlust,
  5 % Drawdown-Grenze, max. 2 Positionen (Werte aus `RiskRules`, nicht über die Konfiguration änderbar).
- **Keine neuen Strategieoptimierungen:** Die beobachteten Parameter sind exakt die aus Phase 3.
- **Nichts erfinden:** keine Kurse bei geschlossenem Markt oder Datenlücken, keine Kennzahlen, die nicht berechnet
  wurden (fehlende Werte werden als „nicht verfügbar“ angezeigt). Annahmen (Kommission, Slippage) sind als solche
  gekennzeichnet.
- **8 GB RAM:** 1 Prozess; das Dashboard lädt nur Ergebnisdateien und je Markt/Zeitrahmen nur das letzte Jahr der
  Kerzendaten; die Beobachtung lädt nur die für die Indikatoren nötigen Kerzen.

## Punkt 1 – Streamlit-Dashboard

| Schritt | Inhalt |
|---|---|
| 1.1 | Datenmodul `tradingbot/dashboard_data.py` ohne Streamlit-Abhängigkeit (testbar): Phase-3-Ergebnisse (`reports/phase3_ergebnisse.json`, sonst `docs/phase3_zusammenfassung.json`), Datenqualität (neuester `reports/phase2_datenbericht_*.json`), Aktualität aus dem Parquet-Speicher, Beobachtungsdaten |
| 1.2 | Oberfläche `dashboard.py` mit klar getrennten Bereichen: **Übersicht** · **Backtest (Phase 3, historische Simulation)** · **Live-Beobachtung (Paper-Trading, hypothetisch)** · **Daten** · **Kontrakt & Mindestlot** |
| 1.3 | Backtest-Bereich: Ranglisten je Markt, Gewinn/Verlust, Profitfaktor, Drawdown in % und €, Kosten (Spread, Kommission, Swap), Walk-Forward-Abschnitte, Jahresergebnisse, Kostenszenarien |
| 1.4 | Drawdown in €: wird in Phase 3 bisher nicht gespeichert → Kennzahl `max_drawdown_eur` in `metrics.compute` ergänzen (aus derselben Kontokurve wie der Drawdown in %). Bis zum nächsten Backtest-Lauf zeigt das Dashboard „nicht verfügbar“ statt eines geschätzten Werts |
| 1.5 | Tests für das Datenmodul und ein Starttest der Oberfläche (Streamlit `AppTest`) |

## Punkt 2 – Paper-Trading (Beobachtung)

**Beobachtete Kandidaten** (Forschungskandidat „Bitcoin-Trendfolge auf M30/H1“ aus Phase 3, Parameter unverändert):

| Zeitrahmen | Strategie | Parameter |
|---|---|---|
| H1 | `kombi_breakout_momentum` | n 55, rr 3,0 |
| H1 | `donchian_breakout` | n 55, atr_mult 2,5 |
| H1 | `ema_trend` | fast 50, slow 200, atr_mult 3,0 |
| M30 | `ema_trend` | fast 50, slow 200, atr_mult 3,0 |

Phase 3 nennt diese vier Parametersätze gemeinsam als Kandidatengruppe auf den beiden Zeitrahmen H1 und M30. Die
Liste steht in `config.toml` (`[paper]`) und kann dort verkleinert werden.

| Schritt | Inhalt |
|---|---|
| 2.1 | Modul `tradingbot/paper/` und Startdatei `beobachtung_ausfuehren.py` (einmalig oder in einer Schleife) |
| 2.2 | Kerzen nur lesend aus MT5 (`copy_rates_range`), **nur abgeschlossene Kerzen**; die laufende Kerze wird verworfen |
| 2.3 | Signale mit derselben Strategie-Klasse wie im Backtest; Ein-/Ausstiege mit derselben Engine (`engine.simulate`): Einstieg zur Eröffnung der Folgekerze (Ask + Spread + Slippage), Stop vor Ziel, kein Einstieg über Lücken |
| 2.4 | Schutz vor Look-ahead: nur Signale auf Kerzen, die **nach Beobachtungsbeginn** begonnen haben; jedes Signal wird beim ersten Erkennen mit Zeitstempel protokolliert; Signale, die erst nachträglich auftauchen oder nicht innerhalb einer Kerze nach Schluss erkannt wurden (Programm lief nicht), werden **nicht** gehandelt, sondern als Abweichung bzw. „verspätet“ protokolliert; abgeschlossene Trades werden eingefroren |
| 2.5 | Kosten je Trade: Spread (MT5-Kerzenspread, wie im Backtest × 1,5 = konservativ), Slippage (Annahme), Kommission (Annahme, gekennzeichnet), Swap (aktuelle MT5-Werte); Ergebnis je Mindestlot (0,01) in € und R |
| 2.6 | Referenzkonto 1.000 € mit den unveränderten Risikoregeln (gleiche Kontosimulation wie Phase 3): ausführbar oder „nicht ausführbar (Mindestlot)“ |
| 2.7 | Markt geschlossen / keine frischen Ticks / Datenlücken: als Ereignis protokolliert, keine Ausführung, keine erfundenen Kurse |
| 2.8 | Ablage in `data/paper/`: `state.json`, `ereignisse.jsonl` (nur anhängen), `trades.json`, `status.json` – atomar geschrieben, nach Neustart wird fortgesetzt |
| 2.9 | Tests mit simuliertem MT5 (Zeit läuft schrittweise): Ergebnisse identisch zu einer nachträglichen Gesamtrechnung, keine Nutzung der laufenden Kerze, Lücken, geschlossener Markt, Neustart, keine Order-Funktion |

## Punkt 3 – Kapital und Risiko (nur Prüfung und Dokumentation)

| Schritt | Inhalt |
|---|---|
| 3.1 | Beim Start der Beobachtung: aktuelle Kontraktspezifikationen live aus MT5 (`symbol_info`, nur lesend) für XAUUSD und BTCUSD, Vergleich mit `data/specs.json` |
| 3.2 | Maximaler Stop-Abstand, bei dem 0,25 % von 1.000 € (2,50 €) mit der Mindestlotgröße eingehalten werden (inkl. Kommissionsannahme), in USD, in % vom Kurs und als Vielfaches der aktuellen ATR(14) je Zeitrahmen |
| 3.3 | Ergebnis in `data/paper/kontraktpruefung.json`, im Dashboard und in `docs/phase4_ergebnis.md`. Es wird **nichts** am Kapital oder an den Risikogrenzen geändert |

## Punkt 4 – nicht Bestandteil

Keine neuen Strategien, Parameter oder Optimierungen bis zur Auswertung der ersten Beobachtungsdaten.

## Abnahme

- Alle Tests grün (bestehende 160 + neue).
- `docs/phase4_ergebnis.md` mit tatsächlich umgesetzten Funktionen, Testergebnissen und offenen Problemen.
- Danach Halt bis zur ausdrücklichen Freigabe.
