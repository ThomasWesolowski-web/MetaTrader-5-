# Phase 5 – Demo-Handel

**Stand:** 21.10.2026 · **Freigabe des Nutzers (21.10.2026):** Orders für eingehende Signale, **nur auf dem
Demokonto**; Risiko 1,5 % je Trade, Tagesverlust 4,5 %, Handelsstopp bei 20 % Drawdown; Strategien: die vier
Bitcoin-Trendfolge-Kandidaten und `sma_box_trend`.

## Regeln (fest im Programm)

| Regel | Wert | Umsetzung |
|---|---|---|
| Konto | **nur Demokonto** | `tradingbot/execution.py` prüft `trade_mode == 0` beim Start und vor **jeder** Order; bei Echtgeld- oder Wettbewerbskonto keine Order |
| Risiko je Trade | 1,5 % vom Kontostand | Volumen so, dass Verlust bis zum Stop + Kommission (Annahme) höchstens 1,5 % kostet, abgerundet; unter 0,01 Lot kein Trade |
| Tagesverlust | 4,5 % (realisiert, Brokertag) | danach keine neuen Orders bis zum nächsten Tag |
| Drawdown | 20 % (inkl. offener Positionen) | Handel gestoppt; offene Positionen behalten ihren Stop; weiter nur mit `python beobachtung_ausfuehren.py --handel-freigeben` |
| Positionen | höchstens 2 gleichzeitig | über alle Strategien |
| Stop/Ziel | beim Broker hinterlegt | mit der Order; Mindestabstand des Brokers wird geprüft |
| Ausstiege | Stop/Ziel beim Broker; Ausstiegssignal oder Zeitstopp der Strategie | Schließen zum Marktpreis nach Kerzenschluss |
| Einstiege | nur für Signale, die höchstens 5 Minuten nach Kerzenschluss erkannt werden | verspätete Signale (Programm lief nicht) werden nicht gehandelt |
| Kennzeichnung | Magic-Nummer 20261021 | andere, von Hand eröffnete Positionen werden nicht angefasst |

Ein- und Ausschalten: `config.toml`, Abschnitt `[execution]`, `orders_enabled = true/false`. Ohne diesen Eintrag
sendet das Programm keine Orders. Orders sendet ausschließlich `tradingbot/execution.py` (ein Test prüft das).
In MT5 muss die Schaltfläche **„Algo Trading“** eingeschaltet sein, sonst werden keine Orders gesendet (Hinweis im
Protokoll).

## Gehandelte Kandidaten (`config.toml`, `[paper]`)

| Markt | TF | Strategie | Backtest auf echten Daten |
|---|---|---|---|
| BTCUSD | H1 | kombi_breakout_momentum (n 55, rr 3) | Phase 3: einziger Satz mit t-Wert > 2 bei höherem Risiko |
| BTCUSD | H1 | donchian_breakout (n 55, 2,5 ATR) | Phase 3: positiv bei 1 %, statistisch schwach |
| BTCUSD | H1 | ema_trend (50/200, 3 ATR) | Phase 3: positiv bei 1 %, statistisch schwach |
| BTCUSD | M30 | ema_trend (50/200, 3 ATR) | Phase 3: positiv bei 1 %, statistisch schwach |
| BTCUSD | M15 | sma_box_trend | **noch kein Backtest auf echten Daten** |
| XAUUSD | M15 | sma_box_trend | **noch kein Backtest auf echten Daten** |

`sma_box_trend` wird auf Wunsch des Nutzers ohne Backtest-Ergebnis gehandelt. Die Vorlage nannte Bitcoin und Gold;
deshalb laufen beide Märkte. Ein Markt lässt sich in `config.toml` durch Löschen des Eintrags entfernen.

## Protokoll und Anzeige

- Ereignisse in `data/paper/ereignisse.jsonl`: `order_eroeffnet`, `order_nicht_gesendet` (mit Grund),
  `order_geschlossen`, `position_beendet` (mit Ergebnis), `tageslimit_erreicht`, `handel_gestoppt`,
  `handel_freigegeben`.
- Dashboard → Übersicht: Handelsstatus (aktiv, gestoppt, Drawdown, offene Positionen); Ereignisse im Bereich
  „Live-Beobachtung“. `python beobachtung_ausfuehren.py --status` zeigt den Handelsstatus ebenfalls.
- Die hypothetische Rechnung (je 0,01 Lot, Referenzkonto) läuft unverändert weiter, als Vergleich zu den echten
  Demo-Ausführungen.

## Tests

`tests/test_execution.py` (12) mit simuliertem MT5: Orders nur zu rechtzeitig erkannten Signalen; Stop der Strategie
beim Broker; Risiko nie über 1,5 %; Positionsgröße nachgerechnet; Mindestlot und 2 Positionen; keine Orders bei
Echtgeldkonto, auch bei Wechsel nach dem Start; keine Orders ohne Freigabe in der Konfiguration oder bei
ausgeschaltetem Algo-Trading; Handelsstopp bei 20 % Drawdown und Freigabe; Tagesverlustgrenze; Ausstiegssignal,
Zeitstopp, beim Broker geschlossene Positionen und abweichende Positionsnummer (Netting-Konto). Die Tests laufen gegen ein simuliertes Terminal; das Verhalten
des echten Vantage-Servers (Füllart, Ablehnungen) zeigt sich erst im Demobetrieb.

## Offene Punkte

1. Backtest der neuen Strategien auf dem PC (`python backtest_ausfuehren.py`), damit `sma_box_trend` bewertet ist.
2. Kommission und Slippage sind bei der Positionsgröße weiterhin Annahmen (Szenario „konservativ“).
3. Nach einigen Wochen: echte Demo-Ergebnisse mit Backtest und hypothetischer Rechnung vergleichen.
