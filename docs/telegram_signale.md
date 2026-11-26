# Telegram-Signale automatisch handeln

Der Bot liest einen Telegram-Kanal (voreingestellt: **FX Culture | Premium FX**) mit deinem eigenen Telegram-Account
mit, erkennt Signale und handelt sie in MetaTrader 5 – ohne Rückfrage je Order (Freigabe vom 23.11.2026).

## Einrichten (einmalig, auf dem PC mit MT5)

1. **Telegram-Zugang:** <https://my.telegram.org> öffnen, mit deiner Telefonnummer anmelden, „API development tools“,
   eine App anlegen (Name egal). Du bekommst `api_id` und `api_hash`.
2. **TELEGRAM_START.bat** doppelklicken. Beim ersten Mal fragt es nach `api_id` und `api_hash` (wird in
   `telegram_zugang.toml` gespeichert, nur auf deinem PC) und dann nach deiner Telefonnummer (+49…) und dem Code aus
   der Telegram-App. Danach nie wieder.
3. **Live-Konto:** In `telegram.toml` bei `live_konto` deine Vantage-Live-Kontonummer eintragen. Ohne Nummer startet
   das Programm nicht; mit Nummer handelt es nur auf genau diesem Konto. Für einen Test auf Demo: `konto = "demo"`.
4. In MT5 muss das Konto eingeloggt und **Algo Trading** eingeschaltet sein.

Prüfen, ohne zu handeln: `python telegram_signale.py --verlauf 50` zeigt die letzten 50 Nachrichten des Kanals und was
der Bot daraus lesen würde. `--kanaele` listet deine Kanäle, falls der Name in `telegram.toml` nicht passt.

## Was der Bot macht

| Nachricht im Kanal | Aktion |
|---|---|
| `SIGNAL ALERT / XAUUSD / SELL / ENTRY AREA 3622-3626 / Target: 3560 / Stop loss: 3642` | Marktorder, sobald der Kurs in der Einstiegszone liegt (bis 120 Min.), Stop und Ziel beim Broker |
| `BUY TRADE XAU/USD / 4054.0-4050 / Stop Loss: 4033 / Target: open` | wie oben, aber ohne Ziel beim Broker – Ausstieg über die Folgenachrichten oder den Stop |
| „taking partials“, „secure some profits“, „closing out 80%“ | Teil schließen (genannter Anteil, sonst 50 %); 0,01 Lot ist nicht teilbar → ab 50 % ganz schließen |
| „moving my SL to BE“, „removing risk“, „breakeven“ | Stop auf den Einstiegskurs (nur wenn der Kurs im Gewinn ist) |
| „move SL to 3610“ | Stop dorthin, aber nur enger, nie weiter weg |
| „closing now“, „exit“, „calling it a day“ | alles schließen |
| „cancel“, „don't enter“ | Signal verwerfen, falls noch nicht eröffnet |
| alles andere (Werbung, News, „TP hit“, Texte über 300 Zeichen, @-Erwähnungen) | nur protokolliert |

Folgenachrichten wirken auf das Signal, auf das der Kanal antwortet, sonst auf das jüngste Signal der letzten 24 Stunden
(gleiches Symbol, falls eines genannt ist). Lieber übersehen als falsch lesen: Signale ohne Richtung, Stop oder Ziel
oder mit Stop auf der falschen Seite werden nicht gehandelt.

## Grenzen

Fest im Code (in `telegram.toml` nur kleiner einstellbar):

* Konto: Demokonto oder genau das Live-Konto aus `live_konto` – vor **jeder** Order geprüft (tradingbot/execution.py).
  Live nur mit fester Lotgröße (höchstens 0,05; eingestellt 0,01).
* Verlust bis zum Stop höchstens 25 % des Kontostands, sonst kein Trade.
* Tagesverlust 25 % (nur Telegram-Trades: realisiert + offenes Risiko; mit `stopp_basis = "konto"` das ganze Konto) → bis morgen keine neuen Signale.
* Drawdown 65 % (nur Ergebnis der Telegram-Trades, bezogen auf den Kontostand) → Stopp, weiter erst mit `python telegram_signale.py --handel-freigeben`.
* Höchstens 2 offene Telegram-Positionen, 4 Signale pro Tag, Chance/Risiko mindestens 1.
* Eigene Magic-Nummer 20261124: Positionen des Bots und der Day-Trader-Skills werden nicht angefasst.
  Der Drawdown-Stopp des Demo-Bots gilt hier nicht; die Telegram-Signale messen selbst am tatsächlichen Kontostand
  (Ein- und Auszahlungen werden herausgerechnet).

**Automatisch:** START.bat (Desktop „Trading starten“, auf Wunsch Autostart bei der Windows-Anmeldung) startet die Telegram-Signale mit, sobald sie einmal eingerichtet und angemeldet sind.

Protokoll: `data/telegram/ereignisse.jsonl`, Stand: `python telegram_signale.py --status`. Beenden: Strg+C oder STOP.bat.
