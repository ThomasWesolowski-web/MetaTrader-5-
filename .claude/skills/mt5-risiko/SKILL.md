---
name: mt5-risiko
description: >-
  Positionsgröße und Risikoprüfung für einen MT5-Day-Trade: berechnet die Lotgröße aus dem Risiko je Trade
  (Verlust bis zum Stop inkl. Kommission) und prüft alle Grenzen aus daytrader.toml: Demokonto, Einstiegsfenster,
  Plan-Alter, Kursabweichung, Chance/Risiko, Trades pro Tag, Tagesrisiko, offene Positionen, Margin, Mindestlot,
  Handelsstopp des Bots. Ergebnis PASS oder BLOCK mit Grund. Verwenden bei "wie viel Lot", "Positionsgröße",
  "prüf die Order", "darf ich den Trade machen", "Risiko-Check", oder vor /mt5-order, immer.
---

# mt5-risiko

**Python:** `python` steht hier für das Python des Trading-Bots. Im installierten Ordner (INSTALLIEREN.bat) ist das `.venv/Scripts/python.exe`, sonst `python`. Immer im Hauptordner des Bots aufrufen (dort liegen `daytrader.py` und `daytrader.toml`).

```bash
python daytrader.py ticket                       # data/daytrader/plan.json -> data/daytrader/ticket.json
python daytrader.py --json ticket
python daytrader.py --ohne-mt5 ticket --kontostand 1000   # nur Vorschau, immer BLOCK
```

Größe: Lot = Risiko je Trade ÷ (Abstand bis zum Stop × Kontraktgröße × Umrechnung + Kommission), **abgerundet**
auf das Volumenraster. Liegt schon das Mindestlot über dem erlaubten Risiko, wird nicht gehandelt (BLOCK).

## Die Grenzen (daytrader.toml, [risiko])

| Grenze | Standard |
| --- | --- |
| Risiko je Trade | 1 % vom Kontostand |
| Tagesrisiko | 3 % (Summe der Risiken aller heute gesendeten Day-Trades) |
| Trades pro Tag | 3 |
| offene Day-Trader-Positionen | 1 |
| Chance/Risiko | mindestens 1,0 |
| Plan-Alter | höchstens 30 Minuten |
| Kursabweichung seit dem Plan | höchstens 0,25 × Risiko |
| Einstiegsfenster | `start` bis `einstieg_bis` der Sitzung |
| Konto | nur Demokonto (fest im Code) |

Ein BLOCK ist endgültig für dieses Ticket: nicht zur Freigabe vorlegen, nicht senden. Der Grund steht in der
Ausgabe; meist heißt die Lösung "neu planen" oder "warten".

## So antworten

- Das Ticket in klaren Worten zeigen: Kauf/Verkauf, Lot, Symbol, Stop, Ziel, ungefährer Verlust bis zum Stop.
- Bei PASS: weiter mit `/mt5-order` (dort wird nach dem Ja gefragt). Bei BLOCK: Gründe nennen, nichts weiter.
- Ein PASS ist eine Regelprüfung, keine Empfehlung.
