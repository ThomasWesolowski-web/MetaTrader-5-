# Day-Trader-Skills für MetaTrader 5

**Stand:** 23.11.2026 · **Vorbild:** [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(Jake Schincariol, MIT), umgebaut von US-Aktien (Yahoo, Alpaca) auf MetaTrader 5.

Fünf Claude-Skills unter `.claude/skills/`, die einen einzelnen Day-Trade Schritt für Schritt begleiten.
Unabhängig vom Bot: eigene Einstellungen (`daytrader.toml`), eigene Grenzen, eigene Magic-Nummer **20261123**.
Der Bot fasst diese Positionen nicht an, die Skills fassen keine Bot-Positionen an.

| Skill | Befehl | Aufgabe |
|---|---|---|
| `/mt5-scanner` | `python daytrader.py scannen` | Opening-Range-Breakout, Vortageshoch/-tief, RSI-Umkehr |
| `/mt5-plan` | `python daytrader.py planen XAUUSD` | Einstieg, Stop, Ziel in R, Glattstellzeit |
| `/mt5-zeitmaschine` | `python daytrader.py zeitmaschine` | 10 000 Durchläufe + derselbe Plan auf den letzten 60 Sitzungen |
| `/mt5-risiko` | `python daytrader.py ticket` | Lotgröße aus dem Risiko, alle Grenzen, PASS oder BLOCK |
| `/mt5-order` | `freigeben`, `senden`, `status`, `schliessen` | Ja des Nutzers erfassen, Order senden (nur Demo), Positionen verwalten |

## Benutzen

1. Den Bot-Ordner (z. B. `C:\Trading\TradingBot`) in Claude Code öffnen (Terminal: `claude`, oder in der
   Claude-Desktop-App unter „Code“). Die Skills werden automatisch erkannt.
2. MetaTrader 5 öffnen, mit dem **Demokonto** einloggen, „Algo Trading“ einschalten.
3. Auf Deutsch schreiben, z. B. „Scanne nach Opening-Range-Breakouts“, „Plane Gold long mit ATR-Stop“,
   „Lass die Zeitmaschine laufen“, „Wie viel Lot darf ich?“, „Sende die Order“.

Die Befehle funktionieren auch ohne Claude von Hand (`python daytrader.py --help`). Ohne MT5 laufen Scanner,
Plan und Zeitmaschine auf der lokalen Datenbank (`--ohne-mt5`); das Ticket ist dann nur eine Vorschau.

## Sicherheit

Eine Order erreicht MT5 nur über drei Hürden: PASS der Risikoprüfung für genau dieses Ticket (Fingerabdruck),
ein ausdrückliches „ja“ des Nutzers für genau dieses Ticket (wörtlich protokolliert, 10 Minuten gültig, eine
Order je Ja), und die Rückfrage von Claude Code vor `daytrader.py senden` (`.claude/settings.json`).
Gesendet wird ausschließlich über `tradingbot/execution.py` (`send_manual_order`), das unmittelbar vorher selbst
prüft, dass ein **Demokonto** eingeloggt ist. Abgelehnte Antworten: „ok“, „ja zu allen“, Fragen, ein anderes
Symbol oder eine andere Lotgröße.

Grenzen (`daytrader.toml`, `[risiko]`): 1 % Risiko je Trade, 3 % Tagesrisiko, 3 Trades pro Tag, 1 offene
Position, Chance/Risiko ≥ 1, Plan höchstens 30 Minuten alt, Kurs höchstens 0,25 × Risiko seit dem Plan gelaufen,
Einstieg nur im Einstiegsfenster der Sitzung. Steht der Bot im Handelsstopp (20 % Drawdown), blockieren auch die
Skills.

## Sitzungen

| Symbol | Sitzung | Range | Einstieg bis | Glattstellen |
|---|---|---|---|---|
| XAUUSD | COMEX-Eröffnung 08:20 New York | 15 Min. | 11:30 | 16:30 |
| BTCUSD | US-Börseneröffnung 09:30 New York | 15 Min. | 12:00 | 16:00 |

Weitere Symbole (z. B. DAX als `GER40`, Name beim Broker prüfen): Sitzung unter `[sitzungen.SYMBOL]` ergänzen
und bei `symbole` eintragen. Für Symbole ohne lokale Daten braucht es MT5.

## Was gegenüber dem Original fehlt oder anders ist

- Keine News-, Quartalszahlen- und Kongress-Skills (nur für US-Aktien sinnvoll).
- Kurse aus MT5 und der eigenen Datenbank statt Yahoo; Lot statt Stück; Kosten aus den Kostenszenarien des
  Backtests (Spread, Kommission, Slippage), die Zeitmaschine rechnet netto.
- Orders direkt über MT5 statt über einen Broker-Connector; nur Demokonto.

## Grenzen der Aussagekraft

Die Zeitmaschine zieht seit 25.11.2026 Blöcke von 6 aufeinanderfolgenden Kerzen eines vergangenen Tages
(`--block`, 1 = früheres Verhalten mit Einzelkerzen), damit ruhige und hektische Phasen zusammenbleiben; eine
Lücke über den Stop wird zum Eröffnungskurs gerechnet (mehr als −1 R). Nachrichten und Zinsentscheide kennt sie
nicht. Ganze Strategien über Monate, mit Konto- und Ruin-Sicht: `zeitmaschine.py` ([Beschreibung](zeitmaschine.md)). Die Setups selbst sind nicht im Backtest (Phase 3) bewertet. Kein Finanzrat.

## Tests

`tests/test_daytrader.py` mit simuliertem MT5: Opening-Range-Zustände, Scanner, Plan, Zeitmaschine
(reproduzierbar, Stop vor Ziel, Gewinnschwelle), Lotgröße abgerundet, alle Blockgründe, Freigabe-Antworten,
genau eine Demo-Order je Ja, kein Senden nach Kontowechsel, Schließen nur eigener Positionen, kompletter Ablauf
über die Befehlszeile, Skills verweisen nur auf vorhandene Befehle.
