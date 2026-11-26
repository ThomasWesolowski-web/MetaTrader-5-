# Strategie-Finder – die beste Gold-Strategie je Setup suchen, ohne sich selbst zu belügen

**Stand:** 26.11.2026 · **Start:** Doppelklick auf `STRATEGIEFINDER.bat` (oder Desktop-Symbol „Strategie-Finder“)

Der Strategie-Finder rechnet **4 752 Regeln** für Gold (XAUUSD) mit der Zeitmaschine auf deinen echten MT5-Kerzen
durch – mit Spread, Slippage, Kommission und Swap – und sucht für jedes Setup die beste heraus. **Er handelt
nicht**: keine Orders, auch nicht auf dem Demokonto (ein Test prüft, dass er das Order-Modul gar nicht lädt).

## Was durchgerechnet wird

| Familie | Strategien |
|---|---|
| Trend | EMA-Kreuzung, MACD mit EMA-200-Filter, Supertrend, SMA-Box-Trend, EMA 20 + Wochen-VWAP, Pullback an die EMA |
| Breakout | Donchian/Turtle, Ausbruch + Momentum, Bollinger-Squeeze, Vortageshoch/-tief, Inside Bar, Momentum-Schub |
| Breakout/ORB | Opening Range Breakout London und New York (15/30/60 Min.), Asien-Range (0–7 UTC) |
| Mean Reversion | RSI-Umkehr (RSI 2 und 14), RSI(2)-Rücksetzer im Trend, Bollinger-Umkehr |
| Order Blocks/SMC | Order Block nach Strukturbruch, Fair Value Gap, ICT Silver Bullet, Liquidity Sweep, Market Structure Shift, Sweep + MSS, **dein Gold-Bot** (Kerze für Kerze wie live) |

Jede Strategie mit 2–14 Parametersätzen, auf **M5, M15, M30, H1 und H4**, zu vier **Handelszeiten**
(ganzer Tag, Asien 0–7 UTC, London 7–12 UTC, New York 12–17 UTC) und in drei **Richtungen** (beide, nur Kauf,
nur Verkauf). Ein **Setup** ist Zeitrahmen + Handelszeit, z. B. „M15 · Asien“ – davon gibt es 20.

## Warum nicht einfach die Strategie mit dem höchsten Gewinn?

Bei 4 752 Versuchen glänzen immer einige auf den vergangenen Kursen – rein durch Zufall. Im Rückblick waren
45 % aller Varianten im Plus. Wer davon die beste nimmt, hat fast immer nur eine Kurvenanpassung. Deshalb:

1. **Walk-Forward:** Der Finder wählt jedes Quartal neu, und zwar **nur mit den Trades davor** (bis 36 Monate
   zurück, mindestens 30 Trades, nur Trades, die vor dem Quartal schon geschlossen waren). Dann handelt er die
   Wahl im Quartal. Was dabei herauskommt, ist das **Out-of-Sample-Ergebnis** – so wäre es dem Finder wirklich
   ergangen. Überzeugt im Lernfenster nichts (t-Wert unter 1), setzt er das Quartal aus.
2. **Harte Kosten:** Dieselbe Wahl noch einmal mit Spread × 2,5 und zehnfacher Slippage.
3. **Gold halten:** Vergleich mit 0,01 Lot im selben Zeitraum einfach kaufen und halten (mit Swap).
4. **Deflated Sharpe Ratio:** Wie wahrscheinlich ist das Ergebnis echt, obwohl so viele Varianten und Setups
   probiert wurden? Ab 0,90 überzeugend.
5. **Nachbar-Test:** Funktioniert die gewählte Regel auch mit den benachbarten Parametern? Glänzt nur ein
   einziger Wert, ist das ein Warnzeichen.
6. **Zeitmaschine:** Für das beste Setup Kontoverlauf, größter Rückgang, Handelsstopp und 52-Wochen-Monte-Carlo
   mit Wochen-Blöcken auf deinem Kontostand.

**Ampel je Setup:**
🟢 grün = mindestens 30 Out-of-Sample-Trades, im Plus auch mit harten Kosten, Profit-Faktor ≥ 1,15, mindestens 60 %
der Quartale im Plus, Deflated Sharpe ≥ 0,90 → Kandidat für das Demokonto ·
🟡 gelb = im Plus, aber nicht überzeugend → beobachten, nicht handeln ·
🔴 rot = kein Vorteil.

## Schritt für Schritt

1. **Neue Version holen:** wie immer `INSTALLIEREN.bat` in `C:\Users\Cora\MetaTrader-5-` doppelklicken und den
   Ordner `C:\Trading\TradingBot` bestätigen. Danach liegt auf dem Desktop das Symbol **„Strategie-Finder“**.
2. **„Strategie-Finder“ doppelklicken.** Ein schwarzes Fenster mit Menü erscheint:
   - `1` alles durchrechnen (mit Gold-Bot, etwa 3–10 Minuten)
   - `2` schnell, ohne Gold-Bot (etwa 1–3 Minuten)
   - `3` alles durchrechnen und den Sieger als Demo-Kandidat eintragen – **nur wenn er grün ist**, sonst wird
     nichts eingetragen
3. **Kontostand eingeben** (Enter = 357 $) und warten. Im Fenster läuft mit, welche Strategie gerade dran ist.
4. **Ergebnis lesen:** Zusammenfassung im Fenster, der Browser öffnet den Bericht. Alle Berichte liegen in
   `C:\Trading\TradingBot\reports\strategiefinder\`.
5. **Bericht von oben nach unten:** Urteil → Übersicht aller Setups (Ampel-Matrix) → Rangliste → bestes Setup
   (aktuelle Wahl, Regeln, Nachbar-Test, Kurve, Wahl je Quartal) → Zeitmaschine → Strategie-Familien →
   Warnliste „Rückblick-Sieger“ (keine Empfehlung!).
6. **Wenn einmal ein Setup grün ist:** Menüpunkt `3` trägt es in `config.toml` als Demo-Kandidat ein (Strategie
   `finder_gefiltert`, nur Demokonto). Danach „Trading stoppen“ und „Trading starten“, damit der Demo-Bot ihn
   übernimmt. Achtung: Ein späteres `INSTALLIEREN.bat` überschreibt `config.toml` (Sicherung `config.toml.bak`) –
   dann Menüpunkt `3` einfach noch einmal ausführen.

Empfehlung: einmal im Monat laufen lassen. Bleibt dasselbe Setup über mehrere Monate vorne, ist das
aussagekräftiger als ein einzelner Lauf.

Für Fortgeschrittene (Eingabeaufforderung im Bot-Ordner):

```bat
.venv\Scripts\python.exe strategiefinder.py --schnell --zeitrahmen M15 H1
.venv\Scripts\python.exe strategiefinder.py --sitzung asien london --konto 500
.venv\Scripts\python.exe strategiefinder.py --strategie donchian_breakout supertrend ema_trend
.venv\Scripts\python.exe strategiefinder.py --mt5          # frische Kerzen aus dem offenen MT5 dazuholen
```

## Erstes Ergebnis auf deinen Kursen (26.11.2026, Daten bis 25.11.2026, Konto 357 $, 0,01 Lot)

Gerechnet in 2 Minuten 17 Sekunden (mit Gold-Bot). **Kein Setup ist grün.** 8 von 20 Setups waren
Out-of-Sample im Plus, keines überzeugend genug.

| Setup (Out-of-Sample) | Ampel | Trades | Ergebnis | hart | R/Trade | DSR | Gold halten |
|---|---|---|---|---|---|---|---|
| M15 · Asien 0–7 UTC (05/2024–11/2026) | 🟡 | 137 | +1 209 $ | +1 117 $ | 0,75 | 0,78 | +608 $ |
| M30 · Asien 0–7 UTC | 🟡 | 189 | +519 $ | +414 $ | 0,26 | 0,35 | +439 $ |
| M15 · ganzer Tag | 🟡 | 209 | +1 302 $ | +1 154 $ | 0,32 | 0,19 | +608 $ |
| M30 · ganzer Tag | 🟡 | 293 | +1 501 $ | +1 337 $ | 0,18 | 0,08 | +439 $ |
| H1 · ganzer Tag | 🟡 | 367 | +781 $ | +620 $ | 0,08 | 0,01 | +407 $ |
| alle New-York-Setups (12–17 UTC) | 🔴 | – | alle im Minus | | | | |

**Bestes Setup: M15 · Asien.** Aktuelle Wahl: **Donchian-/Turtle-Ausbruch (100 Kerzen, Stop 1,5 ATR), nur
Kauf, Einstieg 0–7 UTC.** Alle 6 Parametersätze derselben Regel sind im Plus (Nachbar-Test bestanden).

Ehrlich dazu:
- **Deflated Sharpe 0,78** (unter 0,90): Bei 20 verglichenen Setups könnte das Ergebnis noch Glück sein.
- Die Regel **kauft nur** und lebt vom starken Gold-Anstieg seit 2024. Dreht der Trend, kann sie schnell kippen.
- Auf 357 $ wäre das Konto unterwegs um **49 %** gefallen; der 20-%-Stopp des Demo-Bots hätte am 22.09.2025
  ausgelöst. Monte Carlo (52 Wochen): schlecht 337 $, Mitte 804 $, gut 1 424 $, Ruin 0,4 %.
- Nach Familien brachte **Trendfolge** Out-of-Sample am meisten; **Mean Reversion** verlor, Order Blocks/SMC
  (inkl. Gold-Bot) lagen bei null.

Kurz: Der Finder findet eine plausible Richtung (Trendfolge-Käufe in der Asien-Sitzung), aber noch keinen
Vorteil, der alle Prüfungen besteht. Deshalb wurde nichts eingetragen. Beobachten und monatlich neu rechnen.

## Grenzen

Vergangenheit ist keine Garantie. MT5 speichert je Kerze meist den kleinsten Spread – deshalb „konservativ“ und
„hart“. Die Reihenfolge von Hoch und Tief in einer Kerze ist unbekannt (im Zweifel zählt der Stop). Gold M5 liegt
erst ab Juni 2025 vor (wenig Out-of-Sample), M15 ab August 2022, M30/H1/H4 ab März 2018. Handelszeiten sind in
UTC und verschieben sich nicht mit der Sommerzeit. Der Gold-Bot handelt 2 × 0,01 Lot, alle anderen 0,01 Lot.
