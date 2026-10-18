"""Gesamtablauf Phase 2: Verbinden, Zeitschema bestimmen, importieren, validieren, prüfen."""

from __future__ import annotations

import pandas as pd

from .config import Config
from .importer import Importer
from .instruments import (CurrencyConverter, InstrumentSpec, RiskCalculator, conversion_sources,
                          find_conversion_symbol)
from .quality import check_bars, consistency_with_m1
from .server_time import Detection, detect_scheme, estimate_live_offset, parse_scheme
from .sessions import get_calendar
from .storage import MarketStore

SPECS_FILE = "specs.json"


def _detect_time_scheme(cfg: Config, client, symbols: list[str], now_utc: pd.Timestamp, log) -> Detection:
    live = None
    # Frischester Tick (24/7-Symbole zuerst, da sie auch am Wochenende Kurse liefern)
    order = sorted(symbols, key=lambda s: cfg.calendar_for(s) != "24x7")
    for sym in order:
        tick = client.tick(sym)
        if tick and tick.get("time"):
            live = estimate_live_offset(tick["time"], now_utc.timestamp())
            if live is not None:
                log(f"  Aktueller Abstand Serverzeit–UTC (aus {sym}-Tick): {live / 3600:+.2f} h")
                break
    if cfg.server_timezone != "auto":
        scheme = parse_scheme(cfg.server_timezone)
        det = Detection(scheme, "konfiguriert", "vorgegeben", live_offset_s=live)
        if live is not None and scheme.offset_at(now_utc) != live:
            det.notes.append(f"Konfiguriertes Schema {scheme.name} passt NICHT zum gemessenen Abstand "
                             f"{live / 3600:+.2f} h!")
        return det
    ref = client.rates_from_pos(cfg.timezone_reference_symbol, "H1", 0, 20000)
    times = [] if ref is None else ref["time"]
    return detect_scheme(times, live, now_utc)


def validate_calculations(client, specs: dict, conversions: list[dict], account_ccy: str,
                          leverage: float) -> dict:
    """Eigene Gewinn-/Tickwert-/Margin-Berechnung mit den Rechenfunktionen des Terminals vergleichen.
    order_calc_profit/order_calc_margin erzeugen keine Order."""
    conv = CurrencyConverter()
    sources = conversion_sources({"conversions": conversions,
                                  "instruments": {k: v.to_dict() for k, v in specs.items()}})
    for c in sources:
        t = client.tick(c["symbol"])
        if t and t.get("bid"):
            conv.add_live(c["symbol"], c["base"], c["quote"], (t["bid"] + t["ask"]) / 2)
    out = {}
    for sym, spec in specs.items():
        row = {}
        try:
            tick = client.tick(sym) or {}
            ask = tick.get("ask") or 0.0
            if not ask:
                out[sym] = {"error": "kein Kurs"}
                continue
            spec.margin_rate = 1.0
            rc = RiskCalculator(spec, conv, account_ccy, leverage)
            # Kursbewegung 1 % – groß genug, dass die Cent-Rundung von MT5 den Vergleich nicht verfälscht
            move = round(ask * 0.01, spec.digits)
            ours = float(rc.pnl(1, 1.0, ask, ask + move)[0])
            theirs = client.calc_profit_buy(sym, 1.0, ask, ask + move)
            row["profit_1lot_plus1pct"] = {"kursbewegung": move, "eigene": round(ours, 2), "mt5": theirs,
                                        "abweichung_pct": None if not theirs else round(100 * (ours / theirs - 1), 4)}
            tv = float(rc.value_per_price_unit(1.0)[0]) * spec.tick_size
            row["tick_value"] = {"eigene": round(tv, 6), "mt5": spec.tick_value,
                                 "abweichung_pct": None if not spec.tick_value else round(100 * (tv / spec.tick_value - 1), 4)}
            m_ours = float(rc.margin(1.0, ask)[0])
            m_mt5 = client.calc_margin_buy(sym, 1.0, ask)
            if m_mt5 and m_ours > 0:
                spec.margin_rate = round(m_mt5 / m_ours, 6)
            notional = float(rc.value_per_price_unit(1.0)[0]) * ask
            row["margin_1lot"] = {"mt5": m_mt5, "ohne_marginsatz": round(m_ours, 2),
                                  "marginsatz_kalibriert": spec.margin_rate,
                                  "effektiver_hebel": round(notional / m_mt5, 1) if m_mt5 else None}
        except Exception as e:  # Validierung darf den Datenimport nicht abbrechen
            row["error"] = f"{type(e).__name__}: {e}"
        out[sym] = row
    return out


def run_update(cfg: Config, client, log=print, now_utc_fn=None, symbols=None, timeframes=None) -> dict:
    now_utc_fn = now_utc_fn or (lambda: pd.Timestamp.now(tz="UTC"))
    store = MarketStore(cfg.data_root)
    symbols = symbols or cfg.symbols
    timeframes = timeframes or cfg.timeframes
    client.connect()
    try:
        account = client.account_info()
        acc_ccy, leverage = account["currency"], float(account.get("leverage") or 1)
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(account.get("trade_mode"), str(account.get("trade_mode")))
        log(f"Konto: {mode}, Server {account.get('server')}, Währung {acc_ccy}, Hebel 1:{leverage:g}")
        all_symbols = client.symbols()

        specs, warnings = {}, []
        for sym in symbols:
            info = client.symbol_info(sym)
            if info is None:
                warnings.append(f"Symbol {sym} nicht gefunden")
                continue
            specs[sym] = InstrumentSpec.from_mt5(info)

        conversions = []
        # Erst die Gewinnwährungen (z. B. USD über EURUSD), dann Marginwährungen – diese lassen sich
        # oft über das Instrument selbst umrechnen (BTC -> USD über BTCUSD, dann USD -> EUR).
        profit_ccys = {s.currency_profit for s in specs.values()} - {acc_ccy}
        margin_ccys = {s.margin_result_currency for s in specs.values()} - {acc_ccy} - profit_ccys
        for ccy in sorted(profit_ccys) + sorted(margin_ccys):
            probe = CurrencyConverter()
            for src in conversion_sources({"conversions": conversions,
                                           "instruments": {k: v.to_dict() for k, v in specs.items()}}):
                probe.add_live(src["symbol"], src["base"], src["quote"], 1.0)
            if probe.can_convert(ccy, acc_ccy):
                continue
            c = find_conversion_symbol(all_symbols, acc_ccy, ccy)
            if c is None:
                warnings.append(f"Kein Umrechnungssymbol für {ccy}->{acc_ccy} gefunden")
                continue
            client.symbol_info(c["symbol"])
            if c not in conversions:
                conversions.append(c)
        conv_syms = [c["symbol"] for c in conversions]
        log(f"Umrechnungssymbole: {', '.join(conv_syms) or 'keine nötig'}")

        log("Zeitschema der Serverzeit bestimmen ...")
        det = _detect_time_scheme(cfg, client, list(specs) + conv_syms, now_utc_fn(), log)
        log(f"  Schema: {det.scheme.name} (Methode {det.method}, Sicherheit {det.confidence})")
        for n in det.notes:
            log(f"  Hinweis: {n}")

        importer = Importer(client, store, det.scheme, cfg.chunk_bars, cfg.earliest, now_utc_fn=now_utc_fn, log=log)
        results = []
        conv_tfs = [tf for tf in cfg.conversion_timeframes if tf in timeframes] or cfg.conversion_timeframes
        for sym in list(specs) + conv_syms:
            sym_tfs = conv_tfs if sym in conv_syms else timeframes
            importer.ensure_scheme(sym, sym_tfs)
            for tf in sym_tfs:
                r = importer.update(sym, tf)
                log(f"  {sym:<8} {tf:<4} +{r.new_rows:>8} neu  gesamt {r.rows_total:>9}  ab {str(r.first_utc)[:16]}"
                    f"  ({r.requests} Abfragen{', Historie vollständig geladen' if r.history_exhausted else ''})"
                    + (f"  FEHLER: {r.errors}" if r.errors else ""))
                results.append(r)

        log("Berechnungen mit dem Terminal abgleichen ...")
        validation = validate_calculations(client, specs, conversions, acc_ccy, leverage)
        terminal = client.terminal_info()
        doc = {
            "updated_utc": now_utc_fn().isoformat(),
            "account": {"currency": acc_ccy, "leverage": leverage, "trade_mode": mode,
                        "server": account.get("server"), "company": account.get("company")},
            "terminal": {"build": terminal.get("build"), "maxbars": terminal.get("maxbars")},
            "time_scheme": det.to_dict(),
            "instruments": {k: v.to_dict() for k, v in specs.items()},
            "conversions": conversions,
            "validation": validation,
            "warnings": warnings,
        }
        store.write_json(SPECS_FILE, doc)
        return {"doc": doc, "imports": results, "requests": client.request_count}
    finally:
        client.shutdown()


def run_quality(cfg: Config, symbols=None, timeframes=None) -> dict:
    store = MarketStore(cfg.data_root)
    doc = store.read_json(SPECS_FILE, {}) or {}
    conv_syms = [c["symbol"] for c in doc.get("conversions", [])]
    symbols = (symbols or cfg.symbols) + [s for s in conv_syms if s not in (symbols or cfg.symbols)]
    timeframes = timeframes or cfg.timeframes
    results, consistency = [], {}
    for sym in symbols:
        is_conv = sym in conv_syms and sym not in cfg.symbols
        cal = get_calendar(cfg.calendars.get(sym, "fx_24x5" if is_conv else "24x7"))
        sym_tfs = [tf for tf in timeframes if not is_conv or tf in cfg.conversion_timeframes]
        m1 = store.load(sym, "M1") if "M1" in sym_tfs else pd.DataFrame()
        for tf in sym_tfs:
            df = m1 if tf == "M1" else store.load(sym, tf)
            if df.empty:
                continue
            results.append(check_bars(df, sym, tf, cal, cfg.quality))
            if tf != "M1" and not m1.empty:
                consistency[f"{sym} {tf}"] = consistency_with_m1(m1, df, tf)
            del df
        del m1  # Speicher freigeben, bevor das nächste Symbol geladen wird
    return {"results": results, "consistency": consistency, "specs": doc}
