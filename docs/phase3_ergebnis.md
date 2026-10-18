# Phase 3 – Strategieentwicklung, Backtesting und Strategieauswahl

**Stand:** 18.10.2026 · **Nur Simulation:** keine Orders an MetaTrader 5, keine Demo- oder Echtgeld-Trades
**Status:** Strategiebibliothek, Backtester und Walk-Forward-Auswertung fertig und mit 131 Tests geprüft.
**Ausstehend:** Lauf auf dem Windows-PC mit den echten Vantage-Daten. Die Abschnitte
[Ergebnisse](#ergebnisse-echte-daten) und [Empfehlung](#empfehlung-für-phase-4) werden ausschließlich aus diesem
Lauf befüllt – es werden keine Ergebnisse angenommen oder geschätzt.

## 1. Vorbereitung

- **Bestehende Tests:** alle 75 Tests aus Phase 1/2 bestanden (vor Beginn der Strategieentwicklung).
- **Korrigierte Datenqualitätsprüfung:** muss auf dem PC laufen (`python daten_aktualisieren.py`), da die
  Kursdaten nur dort liegen. Ergebnisse folgen im Abschnitt [Datenqualität](#datenqualität-erneute-prüfung).

## 2. Strategiebibliothek

Alle Signale entstehen am Schlusskurs einer Kerze und nutzen nur Daten bis zu dieser Kerze; ausgeführt wird
zur Eröffnung der nächsten Kerze. Swing-Hochs/-Tiefs gelten erst nach Bestätigung durch k Folgekerzen
(keine nachträglich erkennbaren Muster). Ein Test entfernt die Zukunft aus den Daten und prüft für jede
Strategie, dass sich kein vergangenes Signal ändert (`tests/test_no_lookahead.py`).

| Strategie | Beschreibung | Regeln | Parameterraster (Auswahl nur im Training) |
|---|---|---|---|
| `donchian_breakout` | Breakout: Schluss über dem n-Kerzen-Hoch (Donchian-Kanal) | Long: Schluss > Hoch der vorherigen n Kerzen (erste Kerze des Ausbruchs). Stop: Schluss − atr_mult·ATR. Ausstieg: Schluss < Tief der vorherigen n/2 Kerzen oder nach 5·n Kerzen. Short spiegelbildlich. | {"n": 20, "atr_mult": 1.5}; {"n": 20, "atr_mult": 2.5}; {"n": 55, "atr_mult": 1.5}; {"n": 55, "atr_mult": 2.5} |
| `ema_trend` | Trendfolge: Kreuzung schnelle/langsame EMA | Long: schnelle EMA kreuzt über die langsame. Stop: Schluss − atr_mult·ATR. Ausstieg: Kreuzung in Gegenrichtung. Short spiegelbildlich. | {"fast": 20, "slow": 50, "atr_mult": 2.0}; {"fast": 20, "slow": 50, "atr_mult": 3.0}; {"fast": 50, "slow": 200, "atr_mult": 2.0}; {"fast": 50, "slow": 200, "atr_mult": 3.0} |
| `fvg_retrace` | Fair Value Gap: Rücklauf in eine Lücke in Trendrichtung (EMA 200) | Bullisches FVG (Größe >= min_fvg·ATR) bei Schluss über EMA 200. Einstieg beim ersten Rücklauf in die Lücke innerhalb von 20 Kerzen (ohne Schluss unter der Lücke). Stop: Lückenunterkante − 0,2·ATR. Ziel: rr × Risiko. Short spiegelbildlich unter EMA 200. | {"min_fvg": 0.3, "rr": 1.5}; {"min_fvg": 0.3, "rr": 2.5}; {"min_fvg": 0.6, "rr": 1.5}; {"min_fvg": 0.6, "rr": 2.5} |
| `ict_silver_bullet` | ICT: Fair Value Gap in den Killzones (London 3–4 Uhr, New York 10–11 und 14–15 Uhr NY-Zeit) | Ein FVG (Mindestgröße min_fvg·ATR) entsteht in einer Killzone. Einstieg, wenn der Kurs innerhalb von 12 Kerzen – ebenfalls in einer Killzone – erstmals in die Lücke zurückkehrt. Stop: jenseits der ersten Kerze des FVG (± 0,1·ATR). Ziel: rr × Risiko. | {"rr": 1.5, "min_fvg": 0.1}; {"rr": 1.5, "min_fvg": 0.3}; {"rr": 2.5, "min_fvg": 0.1}; {"rr": 2.5, "min_fvg": 0.3} |
| `kombi_breakout_momentum` | Kombination: Donchian-Breakout nur mit bestätigendem Momentum und steigender Volatilität | Long: Donchian-Breakout (n Kerzen) und Momentum(n) > 1 und ATR > EMA(ATR, 50). Stop: Schluss − 2·ATR. Ziel: rr × Risiko, Zeitstopp 3·n Kerzen. Short spiegelbildlich. | {"n": 20, "rr": 1.5}; {"n": 20, "rr": 3.0}; {"n": 55, "rr": 1.5}; {"n": 55, "rr": 3.0} |
| `kombi_sweep_mss` | Kombination: Liquidity Sweep, danach Market Structure Shift in dieselbe Richtung | Long: MSS aufwärts, und innerhalb der 20 Kerzen davor gab es einen bullischen Liquidity Sweep. Stop: tiefstes Tief seit dem Sweep − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich. | {"k": 3, "rr": 2.0}; {"k": 3, "rr": 3.0}; {"k": 5, "rr": 2.0}; {"k": 5, "rr": 3.0} |
| `kombi_trend_rsi` | Kombination: EMA-Trend (50/200) als Filter, RSI(2)-Rücksetzer als Einstieg | Long: EMA 50 > EMA 200 und RSI(2) fällt unter 'lower'. Stop: Schluss − atr_mult·ATR. Ausstieg: Schluss über EMA 20 oder nach 15 Kerzen. Short spiegelbildlich (EMA 50 < EMA 200, RSI über 100 − lower). | {"lower": 5.0, "atr_mult": 1.5}; {"lower": 5.0, "atr_mult": 3.0}; {"lower": 15.0, "atr_mult": 1.5}; {"lower": 15.0, "atr_mult": 3.0} |
| `liquidity_sweep` | Liquidity Sweep: Kurs sticht unter ein Swing-Tief (über ein Swing-Hoch) und schließt zurück | Long: Tief der Kerze < letztes bestätigtes Swing-Tief, Schluss > dieses Swing-Tief und Schluss in der oberen Hälfte der Kerze (nur erster Sweep je Niveau). Stop: Kerzentief − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich. | {"k": 3, "rr": 1.5}; {"k": 3, "rr": 2.5}; {"k": 5, "rr": 1.5}; {"k": 5, "rr": 2.5} |
| `momentum` | Momentum: volatilitätsnormierte n-Kerzen-Rendite über Schwelle, mit EMA-50-Filter | Momentum = (Schluss − Schluss vor n Kerzen) / (ATR·√n). Long: Momentum steigt über z und Schluss > EMA 50. Stop: Schluss − 1,5·ATR. Ziel: 2 × Risiko. Zeitstopp 2·n Kerzen. Short spiegelbildlich. | {"n": 12, "z": 1.0}; {"n": 12, "z": 1.75}; {"n": 48, "z": 1.0}; {"n": 48, "z": 1.75} |
| `mss` | Market Structure Shift: erster Bruch gegen die bisherige Struktur | Long: Abwärtsstruktur (letzter BOS abwärts) und Schlusskurs bricht über das letzte bestätigte Swing-Hoch. Stop: letztes bestätigtes Swing-Tief − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich. | {"k": 3, "rr": 2.0}; {"k": 3, "rr": 3.0}; {"k": 5, "rr": 2.0}; {"k": 5, "rr": 3.0} |
| `rsi_mean_reversion` | RSI Mean Reversion mit Trendfilter (EMA 200) | Long: RSI fällt unter 'lower' und Schluss > EMA 200. Stop: Schluss − atr_mult·ATR. Ausstieg: RSI > 50 oder nach 20 Kerzen. Short: RSI steigt über 'upper' und Schluss < EMA 200. | {"rsi_n": 2, "lower": 10.0, "upper": 90.0, "atr_mult": 1.5}; {"rsi_n": 2, "lower": 10.0, "upper": 90.0, "atr_mult": 3.0}; {"rsi_n": 14, "lower": 30.0, "upper": 70.0, "atr_mult": 1.5}; {"rsi_n": 14, "lower": 30.0, "upper": 70.0, "atr_mult": 3.0} |
| `smc_orderblock` | Smart Money Concepts: Trend per Break of Structure, Einstieg am Order Block | Long: Schlusskurs bricht das letzte bestätigte Swing-Hoch (BOS). Order Block = letzte bärische Kerze in den 10 Kerzen davor. Einstieg, wenn der Kurs innerhalb von 50 Kerzen erstmals in den Order Block zurückkommt (Tief <= OB-Hoch, Schluss >= OB-Tief), ohne vorher darunter zu schließen. Stop: OB-Tief − 0,1·ATR. Ziel: rr × Risiko. Short spiegelbildlich. | {"k": 3, "rr": 2.0}; {"k": 3, "rr": 3.0}; {"k": 5, "rr": 2.0}; {"k": 5, "rr": 3.0} |

Erweiterung: neue Klasse in `tradingbot/strategies/library.py` (oder eigener Datei) von `TradeStrategy` ableiten,
mit `@register` anmelden und `entries()` implementieren – sie wird automatisch von Backtest, Walk-Forward,
Rangliste und Tests (inkl. Look-ahead-Test) erfasst.

## 3. Zeitrahmen

Getestet werden M1, M5, M15, M30 und H1 für XAUUSD und BTCUSD (120 Kombinationen aus Markt × Strategie × Zeitrahmen).

## 4. Backtest-Annahmen

| Thema | Umsetzung |
|---|---|
| Kurse | MT5-Kerzen = Bid; Ask = Bid + Spread der Kerze |
| Spread | historischer Spread je Kerze (MT5-Feld, eher optimistisch) × Szenariofaktor |
| Einstieg | Eröffnung der Folgekerze (Long zum Ask, Short zum Bid) + Slippage; entfällt bei Lücke nach der Signalkerze |
| Stop / Ziel | innerhalb der Kerze über High/Low; Stop und Ziel in derselben Kerze → Stop; Kurslücke über den Stop → Ausführung zum Eröffnungskurs |
| Datenlücken | keine Ausführung in Lücken; offene Positionen werden zum ersten Kurs nach der Lücke bewertet |
| Kontraktgröße, Volumen | aus MT5 (`data/specs.json`), Volumen auf Raster abgerundet |
| EUR-Umrechnung | historischer EURUSD-Kurs (BTC-Margin über BTCUSD × EURUSD), nur vergangene Kurse |
| Margin | MT5-Berechnungsmodus mit kalibriertem Marginsatz; Margin aller offenen Positionen ≤ Kontostand |
| Swap | aktuelle MT5-Swapwerte für die gesamte Historie (historische unbekannt); Gold Mo–Fr mit dreifachem Swap am Rollover-Tag, Bitcoin jede Nacht |
| Kommission | nicht historisch bekannt → Szenarien (unten) |
| Prüfung gegen MT5 | Formeln identisch mit der in Phase 2 geprüften Rechenlogik (Test); zusätzlich `--mt5-abgleich` gegen `order_calc_profit`/`order_calc_margin` |

**Kostenszenarien** (Annahmen, keine bekannten Werte; Auswahl und Rangliste auf „konservativ“):

| Markt | Szenario | Spread | Slippage je Ausführung | Kommission |
|---|---|---|---|---|
| XAUUSD | basis | × 1 (mind. 1 Point) | 0.001 % | 0 USD/Lot + 0 % vom Nominalwert (Hin+Rück) |
| XAUUSD | konservativ | × 1.5 (mind. 1 Point) | 0.003 % | 7 USD/Lot + 0 % vom Nominalwert (Hin+Rück) |
| BTCUSD | basis | × 1 (mind. 1 Point) | 0.005 % | 0 USD/Lot + 0 % vom Nominalwert (Hin+Rück) |
| BTCUSD | konservativ | × 1.5 (mind. 1 Point) | 0.015 % | 0 USD/Lot + 0.04 % vom Nominalwert (Hin+Rück) |

## 5. Risikomanagement

Startkapital 1 000 €, 0,25 % Risiko je Trade (vom realisierten Kontostand), max. 1 % Tagesverlust
(danach keine neuen Positionen an diesem Broker-Tag), max. 2 gleichzeitige Positionen, 5 % Drawdown als
Testgrenze (Überschreitung disqualifiziert). Volumen wird abgerundet; **liegt es unter der Mindestlotgröße,
wird der Trade nicht ausgeführt** und als „min_lot“ gezählt. Vergleichsweise werden 0,5 % und 1 % Risiko
ausgewertet (Parameter bleiben die mit 0,25 % gewählten).

Rechnerische Grenze bei 1 000 € und 0,25 % (2,50 €) mit Mindestlot 0,01 und EURUSD ≈ 1,16:
Gold max. Stopabstand ≈ 2,90 USD, Bitcoin ≈ 290 USD. Ob das zu den tatsächlichen Schwankungen passt,
zeigt die Mindestlot-Prüfung im Ergebnis.

## 6. Auswertung, Walk-Forward und Leakage-Schutz

- Zeiträume je Markt: **Training 60 % / Validierung 20 % / Test 20 %** (chronologisch, für alle Zeitrahmen gleich).
- **Walk-Forward** im Training: 2 Jahre In-Sample (Parameterwahl), 6 Monate Out-of-Sample, rollierend.
- Parameterwahl: t-Wert der Netto-R je Trade (Stabilität statt Maximalgewinn), mindestens 30 Trades, Abzug
  bei Drawdown-Überschreitung; nur Trades, die im Auswahlfenster eröffnet **und** geschlossen wurden.
- Validierungs- und Testdaten fließen nie in die Parameterwahl; der Test dient nur zur Bestätigung.
- Kennzahlen je Kombination: Nettogewinn, max. Drawdown, Profitfaktor, Trefferquote, Erwartungswert (€ und R),
  t-Wert, Sharpe Ratio, Anzahl Trades, Haltedauer, Jahresergebnisse/Anteil positiver Jahre,
  Walk-Forward-Abschnitte, Kosten (Spread, Kommission, Swap), Mindestlot-Ausfälle.

## 7. Auswahlkriterien („geeignet für den Demohandel“)

| Kriterium | Wert |
|---|---|
| validation_min_trades | 50 |
| validation_min_pf | 1.2 |
| validation_max_dd_pct | 5.0 |
| wf_min_trades | 50 |
| wf_min_positive_folds_pct | 55.0 |
| test_min_trades | 30 |
| test_min_pf | 1.1 |
| test_max_dd_pct | 5.0 |
| max_min_lot_skip_pct | 50.0 |

Rangliste je Markt: qualifizierte Kombinationen zuerst, dann nach Score (Validierungs-t-Wert, Anteil positiver
Walk-Forward-Abschnitte, Test-Profitfaktor, Drawdown-Abzug). Erfüllt keine Kombination alle Kriterien, lautet
das Ergebnis ausdrücklich: **aktuell keine Strategie für den Demohandel geeignet.**

## 8. Bei der Entwicklung gefundene und behobene Fehler

1. **Positionen blieben offen (Kontosimulation):** Trades mit Stop in der Einstiegskerze hatten identische Ein-
   und Ausstiegszeit; der Ausstieg wurde vor dem Einstieg verarbeitet, die Position nie geschlossen und alle
   weiteren Trades als „max. Positionen“ blockiert. Reproduziert (531 statt 2 590 Trades), behoben, Regressionstest.
2. **Momentum-Schwellen** (z = 2,5) praktisch nie erreicht, weil die ATR Dochte enthält; Raster auf 1,0/1,75.
3. Zeitstempel werden für die Ereignissortierung als int64 verarbeitet (Objekt-Zeitstempel sind dafür ungeeignet).

## Datenqualität (erneute Prüfung)

*Ausstehend – aus `python daten_aktualisieren.py` auf dem PC.*

## Ergebnisse (echte Daten)

*Ausstehend – aus `python backtest_ausfuehren.py` auf dem PC (Bericht `reports/phase3_bericht_*.md`,
maschinenlesbar `reports/phase3_ergebnisse.json`).*

## Empfehlung für Phase 4

*Ausstehend – erst nach Vorliegen der echten Ergebnisse.*
