---
name: mt5-scanner
description: >-
  Chart-Scanner für MetaTrader 5 (Gold, Bitcoin und weitere Symbole aus daytrader.toml). Sucht Opening-Range-
  Breakouts, Ausbrüche über das Vortageshoch bzw. unter das Vortagestief und RSI-Umkehrungen, nur auf
  abgeschlossenen Kerzen. Verwenden, wenn der Nutzer sagt "scanne", "gibt es Setups", "Opening Range",
  "was macht Gold/Bitcoin gerade", "Ausbruch", "Day-Trade suchen", oder als erster Schritt eines Day-Trades.
---

# mt5-scanner

Erster Schritt eines Day-Trades: scannen -> planen -> zeitmaschine -> ticket -> freigeben -> senden.

**Python:** `python` steht hier für das Python des Trading-Bots. Im installierten Ordner (INSTALLIEREN.bat) ist das `.venv/Scripts/python.exe`, sonst `python`. Immer im Hauptordner des Bots aufrufen (dort liegen `daytrader.py` und `daytrader.toml`).

```bash
python daytrader.py scannen                 # alle Symbole aus daytrader.toml
python daytrader.py scannen XAUUSD          # nur ein Symbol
python daytrader.py --json scannen          # maschinenlesbar
python daytrader.py --ohne-mt5 scannen      # nur lokale Datenbank (data/market), ohne Terminal
```

Kurse kommen aus der lokalen Datenbank und, wenn MetaTrader 5 offen und eingeloggt ist, live aus dem Terminal
(nur lesend). Ergebnis zusätzlich in `data/daytrader/scan.json`.

## Die Setups

| Setup | Regel |
| --- | --- |
| orb | Opening Range = Hoch/Tief der ersten `range_min` Minuten ab `start` der Sitzung. Ausgelöst, wenn eine Kerze danach über dem Hoch (Long) oder unter dem Tief (Short) schließt, bis `einstieg_bis`. Zustände: wartet, ausgelöst, abgelaufen, keine_daten |
| vortag | Schlusskurs über dem Hoch bzw. unter dem Tief des Vortags (Sitzungstag in Ortszeit) |
| rsi | RSI(14) kreuzt von unter 30 zurück über 30 (Long) oder von über 70 zurück unter 70 (Short) |

Sitzungen stehen in `daytrader.toml` (`[sitzungen.SYMBOL]`): Gold ab der COMEX-Eröffnung 08:20 New York
(14:20 deutscher Zeit, in der Umstellungszeit 13:20), Bitcoin ab der US-Börseneröffnung 09:30 New York. Für
weitere Symbole (z. B. DAX als GER40) eine Sitzung ergänzen und das Symbol bei `symbole` eintragen.

## So antworten

- Zeiten zusätzlich in deutscher Zeit nennen.
- Ein ausgelöstes Signal ist ein Kandidat, keine Empfehlung. Nächster Schritt: `/mt5-plan`.
- Steht dort "keine Kerzen" oder "keine_daten": MT5 öffnen oder `python daten_aktualisieren.py` ausführen.
- Kein Finanzrat. Die meisten Day-Trader verlieren Geld.
