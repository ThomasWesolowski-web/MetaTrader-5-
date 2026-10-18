# Phase 3 – Strategieentwicklung, Backtesting und Strategieauswahl

**Stand:** 18.10.2026 · **Nur Simulation:** keine Orders an MetaTrader 5, keine Demo- oder Echtgeld-Trades
**Status:** Lauf 2 ausgewertet, **Lauf 3 (Hardware-Modus, alle 120 Einheiten) steht aus**. 160 Tests bestanden.
**Vorläufiges Ergebnis (Lauf 2, 118/120 Einheiten): Für XAUUSD und BTCUSD ist keine Strategie für den Demohandel
geeignet.** Fehlend: XAUUSD M1 `liquidity_sweep` und `smc_orderblock` (Speichermangel) – werden in Lauf 3 nachgeholt.
Maschinenlesbar: `docs/phase3_zusammenfassung.json` (kompakt, für das Dashboard) bzw. vollständig
`reports/phase3_ergebnisse.json` auf dem PC.

## 1. Vorbereitung

- **Bestehende Tests:** alle 75 Tests aus Phase 1/2 bestanden (vor Beginn der Strategieentwicklung).
- **Korrigierte Datenqualitätsprüfung:** auf dem PC ausgeführt (Datenbericht 18.10.2026, 14:29 UTC); Ergebnisse im
  Abschnitt [Datenqualität](#datenqualität-erneute-prüfung). Dabei wurden drei weitere Fehler der Prüfung gefunden und
  behoben (siehe Phase-2-Korrektur).

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

## 9. Hardware-Modus (8 GB RAM, Windows 10)

Seit Lauf 3 sind die Backtests verbindlich an den PC angepasst:

| Vorgabe | Umsetzung |
|---|---|
| 1 Prozess | `--prozesse` Standard 1; mehr nur, wenn je Prozess ≥ 2,5 GB RAM frei sind, sonst automatisch 1 |
| M1 in Blöcken | Signale in Blöcken zu 100.000 Kerzen (+10.000 Vorlauf, damit Indikatoren identisch sind); bei `MemoryError` oder < 1,5 GB freiem RAM wird der Block halbiert (Minimum 10.000) |
| Nur Nötiges laden | je Einheit nur ein Markt, ab dem dichten Datenbeginn, nur Spalten open/high/low/close/spread; Umrechnungskurse nur `close` |
| Märkte nacheinander | Gold, danach Bitcoin; ein Zeitrahmen nach dem anderen |
| Indikatoren einmal | gemeinsamer, schreibgeschützter Indikator-Speicher je Block (ATR, EMA, RSI, Swings, FVG …) |
| Fortsetzung | nach jeder Einheit (Markt × Zeitrahmen × Strategie) Prüfpunkt in `reports/phase3_pruefpunkte`; beim nächsten Start nur Fehlendes; Prüfpunkte werden bei geändertem Code, geänderten Daten oder Einstellungen verworfen |
| Protokoll | RAM (Prozess und frei), Laufzeit, Fortschritt mit Restzeit, Fehler in `reports/phase3_log_*.txt`; im Bericht Abschnitte „Vollständigkeit“ und „Ressourcen“ |
| Testlauf zuerst | ohne bestandenen Testlauf startet kein voller Lauf: (1) Blockberechnung = Gesamtberechnung auf den letzten 150.000 Kerzen des feinsten Zeitrahmens, alle Strategien und Parameter; (2) alle H1-Einheiten; (3) zweiter Durchgang liefert identische Ergebnisse |
| Nichts überspringen | alle 5 Zeitrahmen × 12 Strategien × 2 Märkte; fehlende Einheiten werden im Bericht einzeln genannt |

Nachweis der Gleichheit (Tests in `tests/test_blocks_and_cache.py`): Für alle 12 Strategien und alle Parameter sind
die Signale blockweise identisch zur Gesamtberechnung; die Signale aller 48 Parameterkombinationen sind mit dem
Indikator-Speicher identisch zum Code von Lauf 2 (97.864 Signale); Backtest-Ergebnisse mit und ohne Blöcke sowie nach
einem simulierten Abbruch mit Fortsetzung sind identisch. Lauf 3 vergleicht seine Ergebnisse zusätzlich mit Lauf 2
(Abschnitt „Abgleich mit Lauf 2“).

## Datenqualität (erneute Prüfung)

Datenbericht vom 18.10.2026, 14:29 UTC (mit allen Korrekturen):

| | XAUUSD | BTCUSD | EURUSD |
|---|---|---|---|
| Umrechnung | – | – | einziges Umrechnungssymbol (BTCEUR entfällt) |
| Intraday-Daten dicht ab | H1 04/2018, M1/M5 10/2018 | H1 04/2018, M1/M5 04/2019 | 2000 |
| Dichte 2019–2025 (M1) | 98 % | 61–98 % (bis 2020 kein Wochenendhandel) | 99 % (M5) |
| Duplikate / ungültige Kerzen | 0 / 0 | 0 / 0 | 0 / 0 |
| Konsistenz M5–D1 mit M1 | 100 % | 100 % | – |
| Ausreißer M1 | 238 (vorher 658) | 848 (vorher 10 313) | – |
| Abgleich EUR-Rechnung mit MT5 | +0,013 % | −0,013 % | – |

Die Gold-Intraday-Historie ist erst ab 2018 nutzbar (vorher 0,1–5 % Dichte); Details in der Phase-2-Korrektur.
Backtests beginnen deshalb je Markt ab „dicht ab“ – für Gold ab 2018, nicht ab 2007.

## Ergebnisse (echte Daten)

### Lauf 2 (18.10.2026, 15:00 UTC) – maßgeblich

120 Kombinationen (2 Märkte × 5 Zeitrahmen × 12 Strategien), Laufzeit 15,7 Minuten (2 Prozesse). Zeiträume ab
dichten Daten, für beide Märkte gleich aufgebaut:

| Markt | Training (Walk-Forward + Parameterwahl) | Validierung | Test (nur Bestätigung) |
|---|---|---|---|
| XAUUSD | 01.04.2018 – 17.05.2023 | 17.05.2023 – 30.01.2025 | 30.01.2025 – 16.10.2026 |
| BTCUSD | 01.04.2018 – 18.05.2023 | 18.05.2023 – 01.02.2025 | 01.02.2025 – 18.10.2026 |

**2 von 120 Kombinationen nicht ausgewertet:** XAUUSD M1 `liquidity_sweep` und `smc_orderblock` brachen mit
Speichermangel ab (2 parallele Prozesse). Beide gehörten in Lauf 1 zu den schlechtesten M1-Kombinationen
(−0,9 bzw. −1,1 R je Trade im Test); am Gesamtergebnis ändert das nichts.

#### Ergebnis je Markt und Zeitrahmen (konservative Kosten, 0,25 % Risiko)

| Markt | TF | Strategien mit ≥ 20 Test-Trades | davon profitabel | Median Erwartungswert je Trade (R) | dto. Basis-Kosten | Kerzen mit machbarem 1,5-ATR-Stop |
|---|---|---|---|---|---|---|
| XAUUSD | M1 | 10 | 0 | −0,27 | −0,07 | 62 % |
| XAUUSD | M5 | 4 | 0 | −0,36 | −0,15 | 11 % |
| XAUUSD | M15 | 3 | 0 | −0,34 | −0,11 | 0,1 % |
| XAUUSD | M30 | 1 | 0 | −0,56 | −0,29 | 0 % |
| XAUUSD | H1 | 0 | – | – | – | 0 % |
| BTCUSD | M1 | 12 | 0 | −0,82 | −0,40 | 99 % |
| BTCUSD | M5 | 12 | 0 | −0,50 | −0,15 | 79 % |
| BTCUSD | M15 | 9 | 0 | −0,42 | −0,06 | 34 % |
| BTCUSD | M30 | 3 | 0 | −0,45 | −0,16 | 12 % |
| BTCUSD | H1 | 3 | 0 | −0,34 | −0,16 | 2 % |

Walk-Forward (stitched Out-of-Sample im Training, 0,25 % Risiko): nur **4 von 88** Kombinationen mit ≥ 50 Trades
hatten einen positiven Erwartungswert (BTCUSD M30 EMA-Trend, BTCUSD M30 und H1 Donchian-Breakout, XAUUSD M5
EMA-Trend), keine mit mehr als 50 % positiven Abschnitten.

#### Ranglisten

Keine Kombination erfüllt die Qualitätsanforderungen. Die vorderen Plätze entstehen fast nur durch sehr wenige
Trades (Rang 1 Bitcoin: 2 Validierungs- und 0 Test-Trades) und sind **nicht aussagekräftig**:

| Markt | Rang | TF | Strategie | Validierung (Trades / € / PF) | Test (Trades / € / PF) | Hauptgrund |
|---|---|---|---|---|---|---|
| XAUUSD | 1 | H1 | fvg_retrace | 56 / +2,88 / 1,04 | 5 / +2,30 / 1,31 | PF < 1,2; Walk-Forward negativ |
| XAUUSD | 2 | M30 | fvg_retrace | 80 / −14,13 / 0,87 | 7 / −9,24 / 0,24 | Erwartungswert < 0 |
| XAUUSD | 3 | H1 | smc_orderblock | 79 / −25,78 / 0,78 | 7 / −0,31 / 0,97 | Erwartungswert < 0 |
| BTCUSD | 1 | H1 | kombi_breakout_momentum | 2 / +10,93 / – | 0 / – / – | zu wenige Trades (Mindestlot) |
| BTCUSD | 2 | M30 | rsi_mean_reversion | 1 / −2,65 / – | 1 / +0,69 / – | zu wenige Trades |
| BTCUSD | 3 | H1 | momentum | 5 / −4,72 / 0,34 | 1 / +3,69 / – | zu wenige Trades |

Vollständige Ranglisten, alle Kennzahlen (Nettogewinn, Drawdown, Profitfaktor, Trefferquote, Erwartungswert,
t-Wert, Sharpe, Haltedauer, Jahresergebnisse, Kosten) in `docs/phase3_zusammenfassung.json`.

#### Gold gegen Bitcoin

| | XAUUSD | BTCUSD |
|---|---|---|
| Kosten je Trade im Verhältnis zum Risiko | geringer (M1: −0,07 R schon ohne Kommission) | höher auf M1/M5 (Spread ≈ 17 USD) |
| Mindestlot-Grenze (2,50 € Risiko) | Stop max. 2,81 USD – ab M5 meist zu eng | Stop max. 281 USD – bis M5 meist machbar |
| bester Zeitrahmen (am wenigsten negativ) | M1 (−0,27 R) | H1 (−0,34 R) |
| einzige Gruppe mit positiven Vorzeichen | – | Trendfolge M30/H1 (siehe unten) |

#### Forschungskandidat (nicht qualifiziert): Bitcoin-Trendfolge auf M30/H1

Nur zum Vergleich wurden die mit 0,25 % gewählten Parameter auch mit 1 % Risiko gerechnet (bei 0,25 % verhindert der
Mindestlot fast alle Trades). Dabei zeigte eine Strategiegruppe als einzige positive Vorzeichen in Walk-Forward,
Validierung und Test:

| BTCUSD, 1 % Risiko | Validierung (Trades / € / PF / DD) | Test (Trades / € / PF / DD) | t-Wert Val / Test | Long-Anteil |
|---|---|---|---|---|
| H1 kombi_breakout_momentum (n 55, rr 3) | 57 / +276 / 1,97 / 5,6 % | 40 / +264 / 2,38 / 5,4 % | 2,4 / 2,4 | 81 % / 60 % |
| H1 donchian_breakout (n 55, 2,5 ATR) | 227 / +544 / 1,33 / 15,0 % | 142 / +298 / 1,33 / 17,5 % | 1,2 / 1,0 | 58 % / 55 % |
| H1 ema_trend (50/200, 3 ATR) | 49 / +311 / 1,90 / 7,5 % | 30 / +212 / 2,27 / 7,6 % | 1,1 / 1,0 | 53 % / 47 % |
| M30 ema_trend (50/200, 3 ATR) | 111 / +152 / 1,20 / 16,6 % | 97 / +282 / 1,47 / 10,5 % | 0,6 / 0,8 | 51 % / 49 % |

Warum das **kein** Nachweis ist: (1) nachträglich aus 120 Kombinationen und einem nicht vorgegebenen Risikomodell
herausgesucht (Mehrfachtest-Problem); (2) t-Werte überwiegend um 1 (statistisch nicht von Zufall unterscheidbar),
nur eine Kombination mit t ≈ 2,4 bei 40–57 Trades; (3) Drawdown über der 5-%-Grenze; (4) auch bei 1 % fallen
48–67 % der Test-Trades wegen der Mindestlotgröße weg – ausgeführt werden vor allem Trades mit kleinem Stop, was die
Ergebnisse verzerren kann; (5) Walk-Forward nur 40–50 % positive Abschnitte. Der Long-Anteil von 47–60 % spricht
immerhin gegen einen reinen Aufwärtstrend-Effekt.

#### Risikokennzahlen und Mindestlot

Kapitalbedarf, damit ein Stop von 1,5 ATR (Testzeitraum) bei 0,25 % Risiko mit 0,01 Lot möglich ist:

| | M15 | M30 | H1 |
|---|---|---|---|
| XAUUSD (ATR 7,1 / 10,5 / 15,5 USD) | ≈ 3 800 € | ≈ 5 600 € | ≈ 8 300 € |
| BTCUSD (ATR 232 / 340 / 498 USD) | ≈ 1 200 € | ≈ 1 800 € | ≈ 2 700 € |

Mit 1 000 € und 0,25 % Risiko sind nur M1/M5 überhaupt handelbar – dort zerstören Spread und Slippage den
Erwartungswert. Die höheren Zeitrahmen, auf denen die Kosten weniger ins Gewicht fallen, scheitern am Mindestlot.

#### Prüfung gegen MT5

`--mt5-abgleich` (Lauf 2): Gewinn und Margin für 0,01 / 0,1 / 1 Lot, Kauf und Verkauf, max. Abweichung 0,31 %
(Cent-Rundung von MT5 bei Kleinstbeträgen); bei 1 Lot ≤ 0,02 %.

### Lauf 1 (18.10.2026, 14:01 UTC) – überholt

Lauf 1 hatte zwei inzwischen behobene Fehler (Gold-Training in praktisch leeren Daten vor 2018, BTC-Margin vor 2021
ohne Kurs). Seine Testergebnisse (keine profitable Strategie) werden von Lauf 2 bestätigt.

### Erkannte Schwachstellen

1. **Daten:** Gold-Intraday erst ab 2018 dicht – Training nur ~5 Jahre, Test ~1,7 Jahre. Kerzen-Spreads aus MT5 sind
   eher optimistisch (geringster Spread je Kerze); Kommission/Slippage sind Annahmen.
2. **Kapital/Mindestlot:** dominiert alle Ergebnisse auf M15–H1; viele Signale werden gar nicht gehandelt.
3. **Strategien:** Alle Regeln arbeiten mit Market-Einstiegen zur nächsten Eröffnung; Limit-Einstiege (z. B. am FVG
   oder Order Block) würden Kosten senken, sind aber nicht umgesetzt. Stops auf Basis einzelner Kerzen sind auf
   M1/M5 im Verhältnis zum Spread zu eng.
4. **Statistik:** 120 Kombinationen bedeuten Mehrfachtests; einzelne gute Werte sind zu erwarten, auch ohne Kante.
5. **Technik:** Speichermangel bei M1 mit 2 Prozessen (2 Kombinationen fehlen); Swap mit aktuellen Werten für die
   gesamte Historie (historische unbekannt); Drawdown nur auf realisierter Basis.

## Empfehlung für Phase 4

**Kein Demohandel mit einer der getesteten Strategien.** Keine erfüllt die Anforderungen; die Daten sprechen bei
1 000 € und 0,25 % Risiko gegen jede von ihnen.

Vorschlag für Phase 4 (zur Entscheidung):

1. **Streamlit-Dashboard** auf Basis von `phase3_ergebnisse.json` und der Datenbasis (Ergebnisse, Drawdown,
   Datenqualität) – unabhängig von einer handelbaren Strategie sinnvoll.
2. **Beobachtungsmodus statt Handel:** Die Bitcoin-Trendfolge-Kandidaten (H1/M30) laufen live mit, Signale werden nur
   protokolliert („Paper-Trading“, keine Orders). Das ist ein echter, unverfälschter Forward-Test auf neuen Daten.
3. **Grundsatzentscheidung zu Kapital und Risiko** (deine Entscheidung): Mit 1 000 € sind Gold-Intraday und
   Bitcoin ab M30 bei 0,25 % praktisch nicht handelbar. Alternativen: höheres Demokapital (z. B. 3 000–10 000 €),
   höheres Risiko je Trade (z. B. 1 %, dann mit entsprechend größerem Drawdown) oder ein Broker/Konto mit kleinerer
   Mindestlotgröße.
4. Optional Strategie-Weiterentwicklung (Limit-Einstiege, Kostenfilter, Zeitfilter) – mit vorab festgelegter
   Hypothese und Bewertung auf neuen Daten, um Überanpassung zu vermeiden.
