---
name: mt5-zeitmaschine
description: >-
  Zeitmaschine für einen MT5-Day-Trade: spielt den Rest des Handelstages 10 000-mal aus den eigenen Kerzen der
  letzten 60 Sitzungen durch und rechnet denselben Plan auf allen vergangenen Tagen nach (gleiche Uhrzeit und, bei
  orb, gleiche Regel). Gibt die Quote Ziel zuerst / Stop zuerst / offen, Durchschnitt in R nach Kosten und ein
  ehrliches Urteil. Verwenden bei "Zeitmaschine", "wie stehen die Chancen", "simuliere den Trade", "Monte Carlo",
  "wie oft trifft das Ziel", "ist das ein Münzwurf", oder nach /mt5-plan und vor /mt5-risiko.
---

# mt5-zeitmaschine

**Python:** `python` steht hier für das Python des Trading-Bots. Im installierten Ordner (INSTALLIEREN.bat) ist das `.venv/Scripts/python.exe`, sonst `python`. Immer im Hauptordner des Bots aufrufen (dort liegen `daytrader.py` und `daytrader.toml`).

```bash
python daytrader.py zeitmaschine                    # data/daytrader/plan.json, 10 000 Durchläufe, Seed 19
python daytrader.py zeitmaschine --laeufe 20000 --tage 90
python daytrader.py --json zeitmaschine
```

## So funktioniert sie

1. **Vorrat:** nur vollständige Sitzungen vor dem Plan-Tag, von `start` bis `glattstellen` (Ortszeit). Jede
   Kerze wird zu Verhältnissen zum vorherigen Schlusskurs.
2. **Ein Durchlauf:** ab dem Einstieg Blöcke von 6 aufeinanderfolgenden Kerzen eines zufälligen vergangenen Tages
   (gleiche Tageszeit, ± 2 Kerzen) aneinanderhängen und auf den aktuellen Kurs skalieren – ruhige und hektische
   Phasen bleiben zusammen (`--block 1` = einzelne Kerzen wie früher). Zuerst Stop, dann Ziel prüfen; berührt
   eine Kerze beides, zählt der Stop.
3. **R:** Ziel = +Ziel-R, Stop = −1, offen = Abstand zur Glattstellzeit / Risiko. Kommission und Slippage aus dem
   Kostenszenario (`konservativ`) werden abgezogen ("netto"). Eröffnet eine Kerze jenseits des Stops (Lücke),
   wird zum Eröffnungskurs gerechnet – dann mehr als −1 R.
4. **Geschichte:** derselbe Plan auf jedem vergangenen Tag. Das ist wirklich passiert, keine Simulation.
5. **Urteil:** Gewinnschwelle ist 1 ÷ (1 + R), also 50 % bei 1 R und 40 % bei 1,5 R. Weniger als 5 Punkte
   Abstand: "Nahe am Münzwurf" bzw. "Nahe an der Gewinnschwelle". 5 bis 15: "Neigt zum Ziel/Stop". Mehr: "Deutlich".

## So antworten

- Quoten, Durchschnitt netto, beide Geschichts-Zeilen und das Urteil nennen.
- Immer dazusagen: Simulation aus den letzten Wochen, keine Vorhersage. Nachrichten, Zinsentscheide und
  Wochenenden kennt sie nicht. Eine gute Quote macht aus einem Day-Trade keine sichere Sache.
- Nächster Schritt, wenn der Nutzer weiter will: `/mt5-risiko`.
- Fragt der Nutzer, ob eine ganze Strategie (Gold-Bot, Opening Range Breakout) über Monate funktioniert, ist das
  die große Zeitmaschine: `python zeitmaschine.py goldbot` bzw. `python zeitmaschine.py orb --symbol XAUUSD`
  (nur Rechnung, keine Orders; Beschreibung `docs/zeitmaschine.md`).
