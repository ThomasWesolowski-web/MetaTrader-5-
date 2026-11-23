---
name: mt5-plan
description: >-
  Einstieg und Ausstieg für einen MT5-Day-Trade planen, bevor Geld riskiert wird: Einstieg (Ask/Bid live), Stop
  (Mitte oder Gegenseite der Opening Range oder x ATR), Ziel als Vielfaches des Risikos und Glattstellzeit.
  Schreibt data/daytrader/plan.json. Verwenden bei "plane den Trade", "wo kommt der Stop hin", "Ziel setzen",
  "Einstieg", "wann raus", oder nach /mt5-scanner.
argument-hint: "SYMBOL [long|short]"
---

# mt5-plan

**Python:** `python` steht hier für das Python des Trading-Bots. Im installierten Ordner (INSTALLIEREN.bat) ist das `.venv/Scripts/python.exe`, sonst `python`. Immer im Hauptordner des Bots aufrufen (dort liegen `daytrader.py` und `daytrader.toml`).

```bash
python daytrader.py planen XAUUSD                                    # folgt dem ausgelösten ORB-Signal, Stop Range-Mitte
python daytrader.py planen XAUUSD --stop or-gegenseite --ziel 2
python daytrader.py planen BTCUSD --setup frei --richtung long --stop atr --atr-mult 1.5 --ziel 1.5
python daytrader.py planen XAUUSD --richtung short --einstieg 2650.5 # Einstieg vorgeben (z. B. Limit-Überlegung)
```

| Option | Bedeutung |
| --- | --- |
| `--setup` | orb (Standard), vortag, rsi, frei. Nur bei orb wird die Richtung aus dem Signal übernommen |
| `--richtung` | long oder short; Pflicht, wenn kein ausgelöstes ORB-Signal vorliegt |
| `--stop` | or-mitte (Standard bei orb), or-gegenseite, atr (Standard sonst) |
| `--atr-mult` | Abstand des ATR-Stops, Standard 1,5 × ATR(14) |
| `--ziel` | Ziel in R, Standard 1,5 (= 1,5 × Abstand Einstieg–Stop) |

Einstieg: Long zum Ask, Short zum Bid, live aus MT5. Ohne MT5 der letzte Schlusskurs (+ Spread bei Long).
Glattstellen: Zeit aus der Sitzung in `daytrader.toml`; spätestens dann muss die Position zu sein.

## So antworten

- Plan in klaren Worten zeigen: Richtung, Einstieg, Stop, Ziel, Risiko je Einheit, Glattstellzeit (auch deutsche Zeit).
- Eine Zeile "ACHTUNG" aus der Ausgabe immer weitergeben.
- Nächster Schritt: `/mt5-zeitmaschine`, danach `/mt5-risiko`.
- Der Plan ist eine Rechnung, keine Empfehlung.
