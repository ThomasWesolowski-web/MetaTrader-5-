"""
Phase 1 – Technische Bestandsaufnahme (NUR LESEND).

Prüft:
  1. System (Betriebssystem, Python, Architektur, Pakete)
  2. Verbindung zum MetaTrader-5-Terminal und Kontotyp (Demo/Real)
  3. Gold- und Bitcoin-Symbol beim Broker
  4. Bid-/Ask-Kurse und Spread (inkl. Live-Aktualisierung)
  5. Verfügbarkeit historischer Kerzen- und Tickdaten

Erzeugt einen Prüfbericht als Markdown und JSON im Ordner "reports".

SICHERHEIT: Dieses Skript sendet KEINE Orders. Es ruft weder order_send noch
order_check auf. Einzige Änderung am Terminal: Gefundene Symbole werden per
symbol_select in die Marktübersicht aufgenommen, damit Kurse geliefert werden.

Aufruf (Windows, MT5-Terminal geöffnet und eingeloggt):
    python phase1_bestandsaufnahme.py
    python phase1_bestandsaufnahme.py --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    python phase1_bestandsaufnahme.py --gold XAUUSD.r --btc BTCUSD
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import platform
import re
import struct
import sys
import time
from pathlib import Path

OK, WARN, FAIL, INFO = "OK", "WARNUNG", "FEHLER", "INFO"

# Zeitrahmen, deren Historie geprüft wird: (Name, MT5-Konstante, Sekunden pro Kerze)
TIMEFRAMES = [
    ("M1", "TIMEFRAME_M1", 60),
    ("M5", "TIMEFRAME_M5", 300),
    ("M15", "TIMEFRAME_M15", 900),
    ("H1", "TIMEFRAME_H1", 3600),
    ("H4", "TIMEFRAME_H4", 14400),
    ("D1", "TIMEFRAME_D1", 86400),
]
MAX_BARS = 200_000  # Obergrenze pro Abfrage; das Terminal begrenzt zusätzlich über "Max. Balken im Chart"

ASSETS = {
    "gold": {
        "label": "Gold",
        "base": ("XAU", "GOLD"),
        "patterns": [r"^XAUUSD", r"^GOLD", r"XAU", r"GOLD"],
        "preferred_quote": "USD",
    },
    "btc": {
        "label": "Bitcoin",
        "base": ("BTC", "XBT", "BITCOIN"),
        "patterns": [r"^BTCUSD", r"^XBTUSD", r"^BITCOIN", r"BTC", r"XBT", r"BITCOIN"],
        "preferred_quote": "USD",
    },
}


class Report:
    """Sammelt Prüfergebnisse und Rohdaten für den Bericht."""

    def __init__(self) -> None:
        self.started = dt.datetime.now()
        self.checks: list[dict] = []
        self.data: dict = {}

    def add(self, section: str, name: str, status: str, detail: str = "") -> None:
        self.checks.append({"section": section, "name": name, "status": status, "detail": detail})
        print(f"  [{status:^7}] {name}" + (f" – {detail}" if detail else ""))

    def counts(self) -> dict:
        c = {OK: 0, WARN: 0, FAIL: 0, INFO: 0}
        for chk in self.checks:
            c[chk["status"]] += 1
        return c


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def ts(epoch: int | float) -> str:
    """MT5-Zeitstempel (Serverzeit des Brokers, als Epoch kodiert) lesbar machen."""
    return dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- 1. System

def check_system(rep: Report) -> None:
    section("1. System")
    s = "System"
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_bits": struct.calcsize("P") * 8,
        "python_executable": sys.executable,
        "packages": {p: pkg_version(p) for p in ("MetaTrader5", "numpy", "pandas", "streamlit")},
    }
    rep.data["system"] = info

    rep.add(s, "Betriebssystem", OK if platform.system() == "Windows" else FAIL,
            info["os"] + ("" if platform.system() == "Windows" else " – MT5-Python-API läuft nur unter Windows"))
    py_ok = sys.version_info >= (3, 9)
    rep.add(s, "Python-Version", OK if py_ok else FAIL, info["python"])
    rep.add(s, "Python 64-bit", OK if info["python_bits"] == 64 else FAIL, f"{info['python_bits']}-bit")
    for p, v in info["packages"].items():
        required = p in ("MetaTrader5", "numpy")
        status = OK if v else (FAIL if required else INFO)
        rep.add(s, f"Paket {p}", status, v or ("nicht installiert" + ("" if required else " (erst ab späteren Phasen nötig)")))


# --------------------------------------------------------------------------- 2. Verbindung

def connect(rep: Report, mt5, args) -> bool:
    section("2. MT5-Verbindung")
    s = "Verbindung"
    kwargs = {}
    if args.terminal:
        kwargs["path"] = args.terminal
    if args.login:
        kwargs.update(login=args.login, password=args.password or "", server=args.server or "")
    kwargs["timeout"] = args.timeout_ms

    t0 = time.perf_counter()
    ok = mt5.initialize(**kwargs)
    elapsed = (time.perf_counter() - t0) * 1000
    if not ok:
        rep.add(s, "initialize()", FAIL, f"last_error={mt5.last_error()} – Terminal geöffnet und eingeloggt? "
                                          f"Ggf. Pfad mit --terminal angeben.")
        return False
    rep.add(s, "initialize()", OK, f"{elapsed:.0f} ms")
    rep.data["mt5_package_version"] = getattr(mt5, "__version__", None)
    rep.data["terminal_build"] = list(mt5.version() or [])

    ti = mt5.terminal_info()
    if ti is None:
        rep.add(s, "terminal_info()", FAIL, str(mt5.last_error()))
        return False
    ti = ti._asdict()
    rep.data["terminal"] = {k: ti.get(k) for k in (
        "name", "company", "path", "data_path", "build", "connected", "trade_allowed",
        "tradeapi_disabled", "maxbars", "ping_last", "community_account", "language")}
    rep.add(s, "Terminal", OK, f"{ti.get('name')} / {ti.get('company')} / Build {ti.get('build')}")
    rep.add(s, "Verbunden mit Handelsserver", OK if ti.get("connected") else FAIL,
            f"Ping {ti.get('ping_last', 0) / 1000:.1f} ms" if ti.get("connected") else "keine Serververbindung")
    rep.add(s, "Max. Balken im Chart (maxbars)", OK if ti.get("maxbars", 0) >= 100_000 else WARN,
            f"{ti.get('maxbars')} – begrenzt die abrufbare Historie; Extras > Optionen > Charts")
    # Nur Information: Für Phase 1 nicht nötig, für spätere Phasen (Demo-Handel) schon.
    rep.add(s, "Algo-Trading im Terminal erlaubt", INFO,
            f"trade_allowed={ti.get('trade_allowed')} (erst ab Handelsphase relevant)")

    ai = mt5.account_info()
    if ai is None:
        rep.add(s, "account_info()", FAIL, str(mt5.last_error()))
        return True
    ai = ai._asdict()
    mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(ai.get("trade_mode"), str(ai.get("trade_mode")))
    login = str(ai.get("login", ""))
    rep.data["account"] = {
        "login_masked": ("*" * max(0, len(login) - 3)) + login[-3:],
        "server": ai.get("server"), "company": ai.get("company"), "currency": ai.get("currency"),
        "trade_mode": mode, "leverage": ai.get("leverage"), "balance": ai.get("balance"),
        "equity": ai.get("equity"), "margin_mode": ai.get("margin_mode"),
        "trade_allowed": ai.get("trade_allowed"), "trade_expert": ai.get("trade_expert"),
    }
    rep.add(s, "Kontotyp", OK if mode == "DEMO" else WARN,
            f"{mode} – Server {ai.get('server')}" + ("" if mode == "DEMO" else " – Projekt ist für ein DEMOKONTO vorgesehen!"))
    rep.add(s, "Kontowährung / Hebel", INFO, f"{ai.get('currency')} / 1:{ai.get('leverage')}")
    rep.add(s, "Kontostand / Equity", INFO, f"{ai.get('balance')} / {ai.get('equity')} {ai.get('currency')}")
    hedging = ai.get("margin_mode") == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", 2)
    rep.add(s, "Positionsmodus", INFO, "Hedging" if hedging else f"Netting/Exchange (margin_mode={ai.get('margin_mode')})")
    return True


# --------------------------------------------------------------------------- 3. Symbole

def score_symbol(sym, asset: dict) -> int:
    name = sym.name.upper()
    score = 0
    for i, pat in enumerate(asset["patterns"]):
        if re.search(pat, name):
            score = max(score, 100 - 10 * i)
    if score == 0:
        return 0
    base = (getattr(sym, "currency_base", "") or "").upper()
    if base in asset["base"]:
        score += 20
    if (getattr(sym, "currency_profit", "") or "").upper() == asset["preferred_quote"]:
        score += 15
    if getattr(sym, "trade_mode", 0) == 4:  # SYMBOL_TRADE_MODE_FULL
        score += 10
    if getattr(sym, "visible", False):
        score += 5
    score -= len(name)  # kürzere Namen (ohne Suffixe wie .mini) bevorzugen
    return score


def find_symbols(rep: Report, mt5, args) -> dict:
    section("3. Symbolsuche")
    s = "Symbole"
    all_syms = mt5.symbols_get() or ()
    rep.data["symbols_total"] = len(all_syms)
    rep.add(s, "Symbole beim Broker", OK if all_syms else FAIL, f"{len(all_syms)} gefunden")

    selected = {}
    rep.data["symbol_candidates"] = {}
    for key, asset in ASSETS.items():
        forced = args.gold if key == "gold" else args.btc
        cands = sorted(((score_symbol(x, asset), x) for x in all_syms), key=lambda t: -t[0])
        cands = [(sc, x) for sc, x in cands if sc > 0]
        rep.data["symbol_candidates"][key] = [
            {"name": x.name, "description": x.description, "path": x.path, "score": sc,
             "trade_mode": x.trade_mode, "visible": x.visible} for sc, x in cands[:15]]

        if forced:
            chosen = forced if mt5.symbol_info(forced) else None
            if not chosen:
                rep.add(s, f"{asset['label']}-Symbol", FAIL, f"vorgegebenes Symbol '{forced}' existiert nicht")
                continue
        elif cands:
            chosen = cands[0][1].name
        else:
            rep.add(s, f"{asset['label']}-Symbol", FAIL, "kein passendes Symbol gefunden")
            continue

        if not mt5.symbol_select(chosen, True):
            rep.add(s, f"{asset['label']}-Symbol", FAIL, f"{chosen} – symbol_select fehlgeschlagen: {mt5.last_error()}")
            continue
        others = ", ".join(x.name for _, x in cands[:6] if x.name != chosen)
        rep.add(s, f"{asset['label']}-Symbol", OK, f"{chosen}" + (f" (weitere Kandidaten: {others})" if others else ""))
        selected[key] = chosen
    return selected


def symbol_specs(rep: Report, mt5, key: str, name: str) -> None:
    s = f"Spezifikation {name}"
    si = mt5.symbol_info(name)._asdict()
    trade_modes = {0: "deaktiviert", 1: "nur Long", 2: "nur Short", 3: "nur Schließen", 4: "voll handelbar"}
    filling = []
    if si.get("filling_mode", 0) & 1:
        filling.append("FOK")
    if si.get("filling_mode", 0) & 2:
        filling.append("IOC")
    if not filling:
        filling.append("RETURN")
    spec = {
        "description": si.get("description"), "path": si.get("path"),
        "currency_base": si.get("currency_base"), "currency_profit": si.get("currency_profit"),
        "currency_margin": si.get("currency_margin"), "digits": si.get("digits"), "point": si.get("point"),
        "trade_contract_size": si.get("trade_contract_size"), "trade_tick_size": si.get("trade_tick_size"),
        "trade_tick_value": si.get("trade_tick_value"), "volume_min": si.get("volume_min"),
        "volume_max": si.get("volume_max"), "volume_step": si.get("volume_step"),
        "trade_stops_level": si.get("trade_stops_level"), "trade_freeze_level": si.get("trade_freeze_level"),
        "spread_float": si.get("spread_float"), "swap_long": si.get("swap_long"), "swap_short": si.get("swap_short"),
        "trade_mode": trade_modes.get(si.get("trade_mode"), si.get("trade_mode")),
        "filling_modes": filling,
    }
    rep.data.setdefault("assets", {}).setdefault(key, {})["symbol"] = name
    rep.data["assets"][key]["spec"] = spec
    rep.add(s, "Handelsmodus", OK if si.get("trade_mode") == 4 else WARN, spec["trade_mode"])
    rep.add(s, "Kontrakt / Stellen", INFO,
            f"Kontraktgröße {spec['trade_contract_size']}, {spec['digits']} Nachkommastellen, Point {spec['point']}")
    rep.add(s, "Volumen", INFO, f"min {spec['volume_min']} / max {spec['volume_max']} / Schritt {spec['volume_step']}")
    rep.add(s, "Stops-Level / Füllarten", INFO, f"{spec['trade_stops_level']} Points / {', '.join(filling)}")


# --------------------------------------------------------------------------- 4. Kurse

def check_quotes(rep: Report, mt5, key: str, name: str, sample_seconds: int) -> None:
    s = f"Kurse {name}"
    tick = mt5.symbol_info_tick(name)
    if tick is None or (tick.bid == 0 and tick.ask == 0):
        rep.add(s, "Aktueller Tick", FAIL, f"kein Kurs verfügbar ({mt5.last_error()})")
        return
    point = mt5.symbol_info(name).point or 1e-9
    spread_pts = round((tick.ask - tick.bid) / point)
    mid = (tick.ask + tick.bid) / 2
    q = {"bid": tick.bid, "ask": tick.ask, "spread": round(tick.ask - tick.bid, 10), "spread_points": spread_pts,
         "spread_pct": round((tick.ask - tick.bid) / mid * 100, 5) if mid else None,
         "tick_time_server": ts(tick.time)}
    ok_prices = tick.bid > 0 and tick.ask > 0 and tick.ask >= tick.bid
    rep.add(s, "Bid / Ask", OK if ok_prices else FAIL, f"{tick.bid} / {tick.ask}")
    rep.add(s, "Spread", OK if ok_prices else FAIL,
            f"{q['spread']} ({spread_pts} Points, {q['spread_pct']} % vom Kurs)")
    rep.add(s, "Zeit des letzten Ticks (Serverzeit)", INFO, q["tick_time_server"])

    # Live-Prüfung: Ändern sich die Kurse während des Messfensters?
    seen, last_msc = set(), None
    t_end = time.time() + sample_seconds
    updates = 0
    while time.time() < t_end:
        t = mt5.symbol_info_tick(name)
        if t is not None:
            if last_msc is not None and t.time_msc != last_msc:
                updates += 1
            last_msc = t.time_msc
            seen.add((t.bid, t.ask))
        time.sleep(0.25)
    q["sample_seconds"] = sample_seconds
    q["tick_updates_in_sample"] = updates
    q["distinct_quotes_in_sample"] = len(seen)
    rep.data.setdefault("assets", {}).setdefault(key, {})["quotes"] = q
    if updates:
        rep.add(s, f"Live-Aktualisierung ({sample_seconds}s)", OK, f"{updates} neue Ticks, {len(seen)} versch. Kurse")
    else:
        rep.add(s, f"Live-Aktualisierung ({sample_seconds}s)", WARN,
                "keine neuen Ticks – Markt evtl. geschlossen (Gold: Wochenende/tägl. Pause)")


# --------------------------------------------------------------------------- 5. Historie

def check_history(rep: Report, mt5, key: str, name: str) -> None:
    s = f"Historie {name}"
    hist = {}
    for tf_name, const, secs in TIMEFRAMES:
        rates = mt5.copy_rates_from_pos(name, getattr(mt5, const), 0, MAX_BARS)
        if rates is None or len(rates) == 0:
            # Beim ersten Abruf lädt das Terminal Daten evtl. noch nach – kurz warten, erneut versuchen.
            time.sleep(2)
            rates = mt5.copy_rates_from_pos(name, getattr(mt5, const), 0, MAX_BARS)
        if rates is None or len(rates) == 0:
            hist[tf_name] = {"bars": 0, "error": str(mt5.last_error())}
            rep.add(s, tf_name, FAIL, f"keine Daten ({mt5.last_error()})")
            continue

        times = rates["time"].astype("int64")
        diffs = times[1:] - times[:-1]
        gaps = diffs[diffs > secs * 3] if len(diffs) else diffs  # deutlich größer als 1 Kerze
        bad_ohlc = int(((rates["high"] < rates["low"]) | (rates["open"] <= 0) | (rates["close"] <= 0)).sum())
        span_days = (int(times[-1]) - int(times[0])) / 86400
        h = {
            "bars": int(len(rates)), "first": ts(times[0]), "last": ts(times[-1]),
            "span_days": round(span_days, 1),
            "gaps_gt_3_bars": int(len(gaps)),
            "largest_gap_hours": round(float(diffs.max()) / 3600, 1) if len(diffs) else 0,
            "invalid_ohlc_rows": bad_ohlc,
            "real_volume_available": bool((rates["real_volume"] > 0).any()),
            "avg_spread_points": round(float(rates["spread"].mean()), 1),
        }
        hist[tf_name] = h
        if tf_name == "M1":
            status = OK if span_days >= 180 else WARN
        elif tf_name in ("H1", "H4", "D1"):
            status = OK if span_days >= 365 * 2 else WARN
        else:
            status = OK if span_days >= 365 else WARN
        if bad_ohlc:
            status = WARN
        rep.add(s, tf_name, status,
                f"{h['bars']} Kerzen, {h['first']} – {h['last']} ({h['span_days']} Tage), "
                f"Lücken>3 Kerzen: {h['gaps_gt_3_bars']}, ungültige OHLC: {bad_ohlc}")

    # Tickhistorie der letzten 24 Stunden
    last_tick = mt5.symbol_info_tick(name)
    if last_tick is not None and last_tick.time:
        end = dt.datetime.fromtimestamp(last_tick.time, dt.timezone.utc)
        ticks = mt5.copy_ticks_range(name, end - dt.timedelta(days=1), end, mt5.COPY_TICKS_ALL)
        n = 0 if ticks is None else len(ticks)
        hist["ticks_24h"] = n
        rep.add(s, "Tickdaten (24h vor letztem Tick)", OK if n > 0 else WARN, f"{n} Ticks")
    rep.data.setdefault("assets", {}).setdefault(key, {})["history"] = hist


# --------------------------------------------------------------------------- Bericht

def write_report(rep: Report, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = rep.started.strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"phase1_bericht_{stamp}.md"
    json_path = out_dir / f"phase1_bericht_{stamp}.json"
    c = rep.counts()
    overall = "BESTANDEN" if c[FAIL] == 0 else "NICHT BESTANDEN"
    icon = {OK: "✅", WARN: "⚠️", FAIL: "❌", INFO: "ℹ️"}

    lines = [
        "# Phase 1 – Technischer Prüfbericht",
        "",
        f"- **Erstellt:** {rep.started:%Y-%m-%d %H:%M:%S} (lokale Zeit)",
        f"- **Gesamtergebnis:** **{overall}** – {c[OK]} OK, {c[WARN]} Warnungen, {c[FAIL]} Fehler",
        "- **Trades ausgeführt:** keine (Skript ist rein lesend)",
        "",
    ]
    assets = rep.data.get("assets", {})
    if assets:
        lines += ["## Zusammenfassung", "",
                  "| Asset | Symbol | Bid | Ask | Spread (Points) | Live-Ticks | M1-Historie ab | D1-Historie ab |",
                  "|---|---|---|---|---|---|---|---|"]
        for key, a in assets.items():
            q, hst = a.get("quotes", {}), a.get("history", {})
            lines.append(
                f"| {ASSETS[key]['label']} | `{a.get('symbol')}` | {q.get('bid', '–')} | {q.get('ask', '–')} | "
                f"{q.get('spread_points', '–')} | {q.get('tick_updates_in_sample', '–')} | "
                f"{hst.get('M1', {}).get('first', '–')} | {hst.get('D1', {}).get('first', '–')} |")
        lines.append("")

    current = None
    for chk in rep.checks:
        if chk["section"] != current:
            current = chk["section"]
            lines += ["", f"## {current}", "", "| Status | Prüfung | Details |", "|---|---|---|"]
        detail = chk["detail"].replace("|", "\\|")
        lines.append(f"| {icon[chk['status']]} {chk['status']} | {chk['name']} | {detail} |")

    for key, cands in rep.data.get("symbol_candidates", {}).items():
        if cands:
            lines += ["", f"## Symbolkandidaten {ASSETS[key]['label']}", "",
                      "| Symbol | Beschreibung | Pfad | Score |", "|---|---|---|---|"]
            lines += [f"| `{x['name']}` | {x['description']} | {x['path']} | {x['score']} |" for x in cands]

    lines += ["", "## Hinweise", "",
              "- Zeitstempel aus MT5 sind **Serverzeit des Brokers**, nicht deine lokale Zeit.",
              "- Die abrufbare Historie hängt vom Broker und von der Terminal-Einstellung "
              "„Max. Balken im Chart“ ab. Längere Historie ggf. im Terminal über Ansicht > Symbole > Balken laden.",
              "- Lücken in der Historie bei Gold sind normal (Wochenende, tägliche Handelspause). "
              "Bitcoin handelt bei vielen Brokern auch am Wochenende.",
              f"- Rohdaten: `{json_path.name}`", ""]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps({"started": rep.started.isoformat(), "summary": c, "overall": overall,
                                     "checks": rep.checks, "data": rep.data},
                                    indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 – technische Bestandsaufnahme MT5 (nur lesend)")
    ap.add_argument("--terminal", help="Pfad zu terminal64.exe (optional)")
    ap.add_argument("--login", type=int, help="Kontonummer (optional, sonst eingeloggtes Konto)")
    ap.add_argument("--password", help="Passwort (optional)")
    ap.add_argument("--server", help="Server (optional)")
    ap.add_argument("--gold", help="Gold-Symbol fest vorgeben, z. B. XAUUSD")
    ap.add_argument("--btc", help="Bitcoin-Symbol fest vorgeben, z. B. BTCUSD")
    ap.add_argument("--sample-seconds", type=int, default=10, help="Dauer der Live-Kursprüfung je Symbol")
    ap.add_argument("--timeout-ms", type=int, default=60_000)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "reports"))
    args = ap.parse_args()

    print("Phase 1 – Technische Bestandsaufnahme (nur lesend, keine Trades)")
    rep = Report()
    check_system(rep)

    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        rep.add("Verbindung", "import MetaTrader5", FAIL, f"{e} – Installation: pip install MetaTrader5")
        mt5 = None

    if mt5 is not None:
        try:
            if connect(rep, mt5, args):
                selected = find_symbols(rep, mt5, args)
                for key, name in selected.items():
                    section(f"4./5. {ASSETS[key]['label']} ({name})")
                    symbol_specs(rep, mt5, key, name)
                    check_quotes(rep, mt5, key, name, args.sample_seconds)
                    check_history(rep, mt5, key, name)
        except Exception as e:  # Bericht trotzdem schreiben
            rep.add("Ablauf", "Unerwarteter Fehler", FAIL, f"{type(e).__name__}: {e}")
        finally:
            mt5.shutdown()

    md_path, json_path = write_report(rep, Path(args.out))
    c = rep.counts()
    print(f"\nErgebnis: {c[OK]} OK, {c[WARN]} Warnungen, {c[FAIL]} Fehler")
    print(f"Bericht:  {md_path}\nRohdaten: {json_path}")
    return 0 if c[FAIL] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
