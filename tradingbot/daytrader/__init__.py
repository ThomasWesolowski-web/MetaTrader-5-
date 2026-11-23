"""Day-Trader-Skills für MetaTrader 5 (nach dem Vorbild von github.com/Jakeschincariol/day-trader-skill, MIT).

Ablauf für einen Trade (je ein Befehl von daytrader.py, je ein Claude-Skill unter .claude/skills/):
    scannen -> planen -> zeitmaschine -> ticket -> freigeben -> senden      (ein Trade)
    status / schliessen                                                   (solange er offen ist)

Alles liegt unter <data>/daytrader/. Orders gehen ausschließlich über tradingbot/execution.py und nur an ein
Demokonto. Eine Order braucht: PASS der Risikoprüfung für genau dieses Ticket, ein ausdrückliches "ja" des
Nutzers für genau dieses Ticket und (in Claude Code) die Bestätigung des Befehls "senden".
"""
