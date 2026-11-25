# Zeitmaschine – Strategien auf den eigenen MT5-Kursen testen

**Stand:** 25.11.2026 · **Start:** Doppelklick auf `ZEITMASCHINE.bat` (oder Desktop-Symbol „Zeitmaschine“)

Die Zeitmaschine spielt die Regeln deiner Bots auf den echten Kerzen deines Brokers durch und zeigt, was mit
deinem Konto passiert wäre – und was sonst noch hätte passieren können. **Sie handelt nicht**: keine Orders, auch
nicht auf dem Demokonto (sie kennt das Order-Modul gar nicht, ein Test prüft das).

## Was sie besser macht als die Zeitmaschine aus dem day-trader-skill

| Vorlage (day-trader-skill) | Diese Zeitmaschine |
|---|---|
| US-Aktien, Kurse von Yahoo | Deine MT5-Kerzen (Gold, Bitcoin, Euro) aus `data\market` bzw. direkt aus MT5 |
| Spread, Slippage, Gebühren fehlen | Echter Spread je Kerze, Slippage, Kommission, Swap – in drei Stufen (basis, konservativ, hart) |
| Kerzen einzeln zufällig gezogen: ruhige und wilde Phasen vermischen sich | Ganze Wochen-Blöcke bleiben zusammen (Block-Bootstrap), Pechsträhnen bleiben Pechsträhnen |
| Ein einzelner Trade | Ganze Strategie über Monate: Gold-Bot (Order Blocks) und Opening Range Breakout |
| Lücke über den Stop = −1 R | Lücke wird zum Eröffnungskurs ausgeführt (z. B. Weihnachten 2025: −3,5 R) |
| Keine Kontosicht | Gewinnrate, CRV, Rückgang, Ruin-Wahrscheinlichkeit für dein Konto und deine Lotgröße |

## Schritt für Schritt

1. **Neue Version holen:** wie immer `INSTALLIEREN.bat` in `C:\Users\Cora\MetaTrader-5-` doppelklicken und den
   Ordner `C:\Trading\TradingBot` bestätigen. Danach liegt auf dem Desktop das Symbol **„Zeitmaschine“**.
2. **MT5 offen lassen** (für Euro nötig, für Gold und Bitcoin reichen die gespeicherten Kurse). Es wird nur
   gelesen, auch wenn das Live-Konto eingeloggt ist.
3. **„Zeitmaschine“ doppelklicken.** Ein schwarzes Fenster mit Menü erscheint:
   `1` Gold-Bot, `2` ORB Gold, `3` ORB Bitcoin, `4` ORB Euro, `5` alles, `6` Gold-Bot über die ganze Historie.
4. **Kontostand eingeben** (Enter = 357 $) und warten. ORB braucht Sekunden, der Gold-Bot etwa 1–2 Minuten pro
   Jahr Kerzen (er rechnet jede 15-Minuten-Kerze genau wie live).
5. **Ergebnis lesen:** im Fenster steht eine Zusammenfassung, der Browser öffnet den Bericht mit Diagrammen.
   Alle Berichte liegen in `C:\Trading\TradingBot\reports\zeitmaschine\`.

Für Fortgeschrittene (Eingabeaufforderung im Bot-Ordner):

```bat
.venv\Scripts\python.exe zeitmaschine.py goldbot --tage 730 --konto 357
.venv\Scripts\python.exe zeitmaschine.py orb --symbol XAUUSD --sitzung london --range 30 --crv 2
.venv\Scripts\python.exe zeitmaschine.py orb --symbol BTCUSD --kosten hart --laeufe 20000 --wochen 26
.venv\Scripts\python.exe zeitmaschine.py goldbot --max-risiko 0      # ohne die 3-%-Risikogrenze des Bots
```

## So liest du den Bericht

1. **So wäre es wirklich gelaufen** – die echte Vergangenheit: Kontokurve, Gewinnrate, **CRV** (durchschnittlicher
   Gewinn ÷ durchschnittlicher Verlust), Ergebnis, **größter Rückgang** (vom Höchststand bis zum Tiefpunkt) und
   ob der Bot unterwegs in seinen Handelsstopp gegangen wäre.
2. **Kosten** – dieselben Trades mit niedrigen, realistischen und harten Kosten. Faustregel: nur was auch bei
   „konservativ“ im Plus ist, hat eine Chance; „hart“ zeigt, wie dünn der Vorteil ist.
3. **Je Halbjahr** – ist das Ergebnis stabil, oder kam es aus einer einzigen guten Phase?
4. **Was hätte auch passieren können?** – 10 000 neu zusammengesetzte Jahre aus echten Wochen-Blöcken. Der Fächer
   zeigt schlechte (5 %), mittlere und gute (95 %) Verläufe; das Balkendiagramm, wo das Konto nach einem Jahr
   steht. **Ruin** = Konto unter 10 % vom Start (bei 357 $: unter 36 $). Daneben zum Vergleich die alte Methode
   (einzeln gezogene Trades).
5. **Urteil** – in Worten, aus allen Teilen zusammen. Kein Finanzrat.

## Was genau gerechnet wird

- **Gold-Bot:** dieselbe Analyse wie live (`tradingbot/goldbot/strategie.py`) Kerze für Kerze auf M15, nur mit
  abgeschlossenen Kerzen. Plan A als Limit im Order Block (Kauf-Limit wird ausgeführt, wenn der **Ask** den Kurs
  erreicht), Plan B als Marktorder zur nächsten Eröffnung. 2 Teile à 0,01 Lot, nach Ziel 1 Stop auf Einstieg,
  Handelszeiten, Freitagsschluss, max. Spread, max. 3 Trades pro Tag und die Risikogrenze aus `goldbot.toml`
  (3 % vom Startkonto – größere Setups lässt der Bot aus). Nicht nachgebildet: KI-Prüfung.
- **Opening Range Breakout:** wie der Demo-Bot in `config.toml` (Range 15 Min. ab 09:30 New York, Stop
  Gegenseite, Ziel 1,5 × Risiko, glatt um 16:00 New York). Euro standardmäßig ab London-Eröffnung 08:00.
  Feste Lotgröße 0,01.
- **Kosten:** Kerzen sind Bid-Kurse, Ask = Bid + Spread der Kerze. basis = echter Spread, kaum Slippage, keine
  Kommission; konservativ = Spread × 1,5, Kommission wie Raw-Konto; hart = Spread × 2,5, zehnfache Slippage.
  Swap mit den aktuellen MT5-Werten. Berühren Stop und Ziel dieselbe Kerze, zählt der Stop.
- **Monte Carlo:** stationärer Block-Bootstrap (Politis/Romano) über Kalenderwochen, mittlere Blocklänge 4 Wochen,
  feste Lotgröße, Ruin beendet den Durchlauf.

## Grenzen

Vergangenheit ist keine Garantie. MT5 speichert je Kerze meist den kleinsten Spread – deshalb gibt es die Stufen
„konservativ“ und „hart“. Innerhalb einer Kerze ist die Reihenfolge von Hoch und Tief unbekannt (deshalb zählt im
Zweifel der Stop). Die Datenmenge begrenzt die Aussage: Gold M5 liegt ab Juni 2025 vor, Gold M15 ab August 2022,
Bitcoin M5 ab Dezember 2025. Unter 30 Trades sagt das Urteil ausdrücklich, dass es zu wenige sind.

## Erste Ergebnisse auf deinen Kursen (25.11.2026, letztes Jahr, Kosten „konservativ“, Konto 357 $)

| Strategie | Trades | Gewinnrate | CRV | Ergebnis | größter Rückgang | Ruin in 52 Wochen |
|---|---|---|---|---|---|---|
| Gold-Bot (Order Blocks, 2 × 0,01) | 73 | 23 % | 3,2 | −8 $ | 54 % | 10 % |
| ORB Gold (0,01) | 256 | 47 % | 1,2 | +165 $ | 90 % | 28 % |
| ORB Bitcoin (0,01) | 350 | 41 % | 1,1 | −233 $ | 89 % | 25 % |

Kurz: Keine der drei Strategien hat auf diesen Daten einen verlässlichen Vorteil nach Kosten. ORB Gold war im
Plus, aber fast nur durch das erste Halbjahr 2026 und mit rund 20 $ Risiko je Trade (5,7 % vom Konto) – unterwegs
wäre das Konto fast leer gewesen. Der Gold-Bot wäre schon am 22.12.2025 in seinen 15-%-Handelsstopp gelaufen.
