# Gold-Bot: Order-Block-Strategie für XAUUSD (M15), automatisch, nur Demokonto

Der Gold-Bot handelt Gold auf dem 15-Minuten-Chart nach festen Regeln und kann jedes Setup vor der Order zusätzlich
von Claude (KI) prüfen lassen. Er läuft neben dem Demo-Bot, den Day-Trader-Skills und den Telegram-Signalen, mit
eigener Magic-Nummer **20261125**, und sendet Orders **nur auf einem Demokonto** (fest im Code, wie beim Demo-Bot).

## Was der Bot macht

1. **Trend auf M15**: Abwärts = tiefere Hochs und tiefere Tiefs, aufwärts = höhere Hochs und höhere Tiefs (bestätigte
   Swing-Punkte: höchste/tiefste Kerze von 3 links und 3 rechts). Kein klarer Trend → kein Trade. Gehandelt wird nur
   mit dem Trend.
2. **Order Blocks (ATR-basiert, eigene Umsetzung)**: Abwärts ist ein Bear-Order-Block die letzte grüne Kerze vor einem
   Abverkauf von mindestens 1,5 ATR binnen 3 Kerzen mit Schluss unter ihrem Tief. Gültig, solange der Kurs die Zone
   noch nicht wieder berührt hat. Aufwärts spiegelbildlich.
3. **Liquidität (eigene Umsetzung)**: Sellside = nicht abgeholte Tiefs unter dem Kurs (gleiche Tiefs werden zu einem
   Niveau zusammengefasst, dazu das letzte Tief seit dem letzten Hoch). Buyside spiegelbildlich.
4. **Plan A – Rücksetzer**: Limit-Order in der Mitte des nächsten frischen Order Blocks, Stop 0,3 ATR hinter dem
   Order Block, Ziel 1 = nächste Liquidität (mindestens so weit wie der Stop), Ziel 2 = die Liquidität danach.
   Beispiel abwärts: Bear-OB 3540–3550 → Verkaufs-Limit 3545, Stop knapp über 3550, Ziel 1 letztes Tief.
   Die Limit-Order wird gelöscht, wenn der Trend kippt, Ziel 1 ohne Ausführung erreicht wird, nach 8 Stunden oder
   freitags ab 18 Uhr UTC.
5. **Plan B – Fortsetzung**: Eine M15-Kerze schließt unter dem letzten Schlüsseltief, danach kommt der Kurs binnen
   8 Kerzen von unten ans Niveau zurück und die Kerze schließt wieder darunter (Retest) → Verkauf zum Marktpreis,
   Stop über dem Retest-Hoch. Aufwärts spiegelbildlich. Plan B ersetzt eine noch nicht ausgeführte Plan-A-Order.
6. **Bei Ziel 1 die Hälfte schließen, Stop auf Einstieg**: Mit 0,01 Lot lässt sich keine Position halbieren. Der Bot
   sendet deshalb zwei Orders zu je 0,01 Lot (`teile = 2`): Teil 1 mit Ziel 1, Teil 2 mit Ziel 2. Ist Teil 1 im
   Gewinn geschlossen (oder der Kurs erreicht Ziel 1), zieht er den Stop von Teil 2 auf den Einstieg.
   Willst du insgesamt nur 0,01 Lot: `teile = 1` (eine Order bis Ziel 2, Stop auf Einstieg bei Ziel 1).
7. **Kein Gegentrend-Trade**, bis die Struktur bricht: Schließt eine Kerze über dem letzten tieferen Hoch, ist der
   Abwärtstrend vorbei – dann wartet der Bot, bis sich ein neuer Trend gebildet hat.

Ohne passende Liquidität (z. B. in neuen Tiefs) nimmt der Bot Ziel 1 = 1,5 × Risiko und Ziel 2 = 3 × Risiko.

## KI-Prüfung (optional)

Ist `[ki] aktiv = true`, bekommt Claude vor jeder Order die letzten 80 M15-Kerzen, Trend, Order Block, Liquidität,
Stop und Ziele und antwortet nur **Ja oder Nein** mit kurzer Begründung. Ein Nein heißt: kein Trade. Die KI erfindet
keine Trades und ändert keine Preise. Dafür brauchst du einen API-Schlüssel von Anthropic (kostet grob 1–3 US-Cent je
Prüfung, bei wenigen Setups am Tag also Centbeträge). Ohne Schlüssel oder mit `aktiv = false` handelt der Bot nur nach
den Regeln.

## Grenzen (unabhängig von den anderen Bots)

| Einstellung | Standard | Obergrenze im Code |
|---|---|---|
| Lot je Teil / Teile | 0,01 / 2 | 0,10 / 2 |
| Risiko je Trade (alle Teile bis zum Stop) | 3 % | 5 % |
| eigener Tagesverlust (realisiert + offen) | 6 % | 10 % |
| eigener Drawdown vom eigenen Höchststand | 15 % | 25 % |
| Trades pro Tag | 3 | 6 |

Tagesverlust und Drawdown werden **nur aus den eigenen Gold-Bot-Trades** gemessen. Die Sperren des Demo-Bots
(`data\paper`) und der Telegram-Signale (`data\telegram`) werden weder gelesen noch verändert. Umgekehrt gilt: Der
Demo-Bot misst seinen Drawdown am ganzen Demokonto – verliert der Gold-Bot auf demselben Demokonto, zählt das dort mit.
Bei 0,01 Lot auf einem normalen Demokonto ist das unbedeutend.

Weitere Regeln: neue Einstiege nur 1–20 Uhr UTC (freitags bis 18 Uhr), Spread höchstens 80 Punkte (0,80 USD), Stop
zwischen 0,5 und 5 ATR, Chance/Risiko bis Ziel 1 mindestens 1.

## Installation Schritt für Schritt (Büro-PC)

**Wichtig vorab – zwei MetaTrader:** Die Telegram-Signale handeln auf deinem Vantage-**Live**-Konto. Der Gold-Bot
handelt nur auf einem **Demokonto**. Ein MetaTrader kann immer nur ein Konto gleichzeitig eingeloggt haben. Damit
beide laufen, brauchst du zwei MetaTrader-Programme nebeneinander: Vantage (Live) und STARTRADER (Demo).

1. **STARTRADER-MetaTrader öffnen** (oder von der STARTRADER-Webseite installieren, falls nur Vantage installiert ist)
   und mit dem Demokonto **2100405946** einloggen. Oben auf **Algo Trading** klicken (grün). In der Marktübersicht
   muss **XAUUSD** stehen (Rechtsklick → Symbole → XAUUSD → Anzeigen). Heißt Gold dort anders (z. B. `XAUUSD.r`),
   merk dir den Namen.
2. **Pfade der beiden MetaTrader herausfinden**: Rechtsklick auf das Desktop-Symbol des STARTRADER-MetaTrader →
   **Eigenschaften** → Feld **Ziel** kopieren (z. B. `C:\Program Files\STARTRADER MT5 Terminal\terminal64.exe`,
   ohne Anführungszeichen). Dasselbe für den Vantage-MetaTrader.
3. **goldbot.toml anpassen**: Im Ordner `C:\Users\Cora\MetaTrader-5-` die Datei `goldbot.toml` mit dem Editor öffnen
   (Rechtsklick → Öffnen mit → Editor) und eintragen:
   ```toml
   terminal_pfad = 'C:\Program Files\STARTRADER MT5 Terminal\terminal64.exe'
   demo_konto = 2100405946
   ```
   Falls Gold anders heißt, auch `symbol = "..."`. Speichern.
4. **telegram.toml anpassen** (damit die Telegram-Signale sicher beim Vantage-Live-MetaTrader bleiben): In derselben
   Datei unter `[telegram]` die Zeile ergänzen bzw. ausfüllen:
   ```toml
   terminal_pfad = 'C:\Program Files\Vantage International MT5\terminal64.exe'
   ```
   (deinen kopierten Vantage-Pfad). Speichern.
5. **Optional – KI einschalten**: Auf https://console.anthropic.com anmelden → **API Keys** → **Create Key**, Schlüssel
   kopieren (beginnt mit `sk-ant-`). Unter **Billing** ein kleines Guthaben aufladen (5 USD reichen lange). Im Ordner
   `C:\Users\Cora\MetaTrader-5-` eine neue Textdatei `ki_zugang.toml` anlegen mit dem Inhalt
   `api_key = "sk-ant-..."` (dein Schlüssel). In `goldbot.toml` unter `[ki]` `aktiv = true` setzen.
   Diese Datei bleibt nur auf deinem PC.
6. **Installieren**: In `C:\Users\Cora\MetaTrader-5-` **INSTALLIEREN.bat** doppelklicken, Zielordner
   `C:\Trading\TradingBot` bestätigen. Deine Daten bleiben erhalten.
7. **Testen ohne Orders**: Im Ordner `C:\Trading\TradingBot` in die Adresszeile `cmd` tippen und Enter, dann:
   ```bat
   .venv\Scripts\python.exe goldbot.py --analyse
   .venv\Scripts\python.exe goldbot.py --rueckblick 60
   ```
   Der erste Befehl zeigt Trend, Order Blocks, Liquidität und ein mögliches Setup; die erste Zeile nennt das Konto –
   dort muss **Demo** und 2100405946 stehen. Der zweite spielt die Regeln auf den letzten 60 Tagen durch.
   Mit KI zusätzlich: `.venv\Scripts\python.exe goldbot.py --ki-test`.
8. **Starten**: **Trading starten** doppelklicken. Es öffnet sich zusätzlich das minimierte Fenster
   „Trading - Gold-Bot“. In Telegram (Gespeicherte Nachrichten) kommt „🥇 Gold-Bot (Demo) gestartet“.

## Bedienung

| Was | Wie |
|---|---|
| Stand ansehen | `.venv\Scripts\python.exe goldbot.py --status` |
| Gold-Bot nicht mehr mitstarten | `goldbot.toml`: `aktiv = false`, dann INSTALLIEREN.bat |
| nur analysieren, keine Orders | `goldbot.toml`: `orders = false` |
| nach Drawdown-Stopp weiter | `.venv\Scripts\python.exe goldbot.py --handel-freigeben` |
| alles sofort schließen | `.venv\Scripts\python.exe goldbot.py --alles-schliessen` |
| beenden | **Trading stoppen** (Orders und Positionen behalten Stop und Ziel beim Broker) |

Meldungen (Order, Ausführung, Stop auf Einstieg, Ergebnis, KI-Nein, Sperren) kommen in deine Telegram-„Gespeicherten
Nachrichten“. Sie laufen über die Telegram-Signale (nur ein Programm darf die Telegram-Anmeldung nutzen); laufen
diese nicht, warten die Meldungen in `data\meldungen\`. Protokoll: `data\goldbot\ereignisse.jsonl`.

## Hinweise

* Die Regeln sind eine eigene Umsetzung der Idee „Trend + Order Block + Liquidität“, keine Kopie der Indikatoren
  „PS Gold OB Finder ATR Based“ oder „LuxAlgo Buyside & Sellside Liquidity“. Die Zonen können deshalb etwas anders
  liegen als im Chart.
* Der Rückblick ist eine vereinfachte Simulation (ohne Kommission, Handelszeiten und KI). Er zeigt, ob die Regeln
  plausibel arbeiten – nicht, dass sie Geld verdienen. Erst einige Wochen Demo, dann entscheiden.
* Live-Handel ist im Gold-Bot nicht vorgesehen. Soll er später live handeln, muss das ausdrücklich freigegeben und im
  Code ergänzt werden.
