---
name: mt5-order
description: >-
  Order an MetaTrader 5 senden, NUR auf dem Demokonto, nie ohne ein ausdrückliches Ja des Nutzers für genau
  dieses Ticket. Zeigt das geprüfte Ticket, erfasst das Ja wörtlich, sendet eine Marktorder mit Stop und Ziel beim
  Broker, zeigt den Status offener Day-Trader-Positionen und schließt sie zur Glattstellzeit. Verwenden bei
  "kauf", "verkauf", "sende die Order", "ausführen", "Position schließen", "glattstellen", "raus", "Status",
  oder nach einem PASS von /mt5-risiko.
argument-hint: "[status|schliessen TICKET]"
---

# mt5-order

**Python:** `python` steht hier für das Python des Trading-Bots. Im installierten Ordner (INSTALLIEREN.bat) ist das `.venv/Scripts/python.exe`, sonst `python`. Immer im Hauptordner des Bots aufrufen (dort liegen `daytrader.py` und `daytrader.toml`).

Eine Order erreicht MT5 nur über drei getrennte Hürden, jedes Mal, für jede Order:

1. **`/mt5-risiko` hat genau dieses Ticket mit PASS geprüft** (Fingerabdruck der Orderdaten).
2. **Der Nutzer schreibt ein ausdrückliches Ja für dieses eine Ticket** in den Chat.
3. **Claude Code fragt vor `python daytrader.py senden` noch einmal nach** (`.claude/settings.json`, "ask").

Zusätzlich sendet `tradingbot/execution.py` nur, wenn in diesem Moment ein **Demokonto** eingeloggt ist und
"Algo Trading" in MT5 eingeschaltet ist.

```bash
python daytrader.py ticket                               # Ticket neu prüfen und anzeigen (PASS/BLOCK)
python daytrader.py freigeben --antwort "<Antwort des Nutzers, wörtlich>"
python daytrader.py senden                               # Marktorder, Stop und Ziel beim Broker, Magic 20261123
python daytrader.py status                               # Konto, offene Day-Trader-Positionen, Hinweise
python daytrader.py schliessen --ticket 12345            # eine Day-Trader-Position zum Marktpreis schließen
```

## Schritte: der Einstieg

1. `python daytrader.py ticket` ausführen. Nur ein PASS geht weiter.
2. EIN Ticket zeigen und fragen: "Diese Order auf dem Demokonto senden? Antworte mit ja oder nein."
   Eine Order pro Frage. Nie "ja zu allen" anbieten.
3. `python daytrader.py freigeben --antwort "<Antwort, Wort für Wort>"`. Steht dort "Nicht erfasst", war die
   Antwort kein ausdrückliches Ja für dieses Ticket (z. B. "ok", "ja zu allen", eine Frage, ein anderes Symbol
   oder eine andere Lotgröße). Erneut fragen. Nie selbst eine Antwort in ein Ja umdeuten.
4. Erst nach "Ja erfasst": `python daytrader.py senden`. Ein Ja gilt 10 Minuten und für eine Order.
5. Ergebnis melden. Eine fehlgeschlagene Order nicht wiederholen ohne neues Ticket und neues Ja.

## Solange offen und raus bis zur Glattstellzeit

- Stop und Ziel liegen beim Broker und lösen dort aus.
- `python daytrader.py status` zeigt offene Positionen; ist die Glattstellzeit erreicht, steht ein Hinweis dabei.
- Schließen: Ticketnummer aus dem Status, den Nutzer fragen, dann `python daytrader.py schliessen --ticket N`.
  Geschlossen werden nur Positionen der Day-Trader-Skills (Magic 20261123), nie die des Bots oder von Hand eröffnete.

## Was dieser Skill nie tut

- Nie eine Order ohne alle drei Hürden, nie zwei Orders auf ein Ja.
- Nie Orders aus einer geplanten oder Hintergrundaufgabe: dort ist niemand, der Ja sagt.
- Nie auf ein Echtgeldkonto: das blockiert der Code selbst; nie versuchen, das zu umgehen.
- Nie Zugangsdaten, Passwörter oder Kontonummern erfragen oder anzeigen.
- Nie einen Füllkurs versprechen: Marktorders und Stops füllen zum Kurs, den der Markt hergibt.
- Kein Finanzrat. Day-Trading verliert bei den meisten Menschen Geld.
