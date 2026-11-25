"""Ein Lauf der Zeitmaschine: Daten laden, Trades je Kostenszenario, Kennzahlen, Monte Carlo, Urteil."""

from __future__ import annotations

import time

import pandas as pd

from . import auswertung as aw
from .daten import DataSource
from .handel import ORB_DEFAULT_SESSION, goldbot_trades, orb_trades
from .kosten import ORDER, scenarios

STRATEGIEN = ("goldbot", "orb")


def run(src: DataSource, strategie: str, symbol: str, tage: int | None = 365, konto: float = 357.0,
        lot: float = 0.01, laeufe: int = 5000, wochen: int = 52, block_wochen: float = 4.0,
        szenario: str = "konservativ", gcfg=None, orb: dict | None = None, ruin_konto: float | None = None,
        calendars: dict | None = None, max_risiko_pct: float | None = -1, log=print, now=None) -> dict:
    """max_risiko_pct: Trades mit mehr Verlust bis zum Stop (in % vom Startkonto) auslassen. -1 = wie der Bot
    (Gold-Bot: risiko_je_trade_pct aus goldbot.toml; ORB: keine Grenze, feste Lotgröße), None = keine Grenze."""
    t0 = time.time()
    if strategie not in STRATEGIEN:
        raise ValueError(f"Unbekannte Strategie '{strategie}' – {STRATEGIEN}")
    spec = src.spec(symbol)
    if spec.currency_profit != src.account_currency:
        log(f"  Hinweis: Gewinne in {spec.currency_profit}, Konto in {src.account_currency} – Beträge sind "
            f"{spec.currency_profit}, nicht umgerechnet")
    always_open = (calendars or {}).get(symbol) == "24x7" or symbol.startswith("BTC")
    now = now or pd.Timestamp.now(tz="UTC")
    if strategie == "goldbot":
        from ..goldbot.config import GoldConfig
        gcfg = gcfg or GoldConfig()
        tf = "M15"
        warm = gcfg.bars
        if max_risiko_pct == -1:
            max_risiko_pct = gcfg.max_risk_pct
        dd_stopp = gcfg.max_drawdown_pct
        settings = {"teile": gcfg.parts, "lot_je_teil": lot, "plan_a": gcfg.plan_a, "plan_b": gcfg.plan_b,
                    "einstieg_utc": f"{gcfg.entry_from_utc}-{gcfg.entry_to_utc}",
                    "max_trades_pro_tag": gcfg.max_trades_per_day}
    else:
        orb = {"session": ORB_DEFAULT_SESSION.get(symbol, "new_york"), "range_min": 15, "rr": 1.5, "tf": "M5",
               **(orb or {})}
        tf = orb["tf"]
        warm = 0
        if max_risiko_pct == -1:
            max_risiko_pct = None
        dd_stopp = 20.0                                    # Drawdown-Grenze des Demo-Bots (config.toml)
        settings = {k: orb[k] for k in ("session", "range_min", "rr", "tf")} | {"lot": lot}
    bars = src.bars(symbol, tf)
    if tage:
        start_t = now - pd.Timedelta(days=tage)
        times = pd.DatetimeIndex(bars["time"])
        i0 = int((times < start_t).sum())
        bars = bars.iloc[max(0, i0 - warm):].reset_index(drop=True)
        first = max(warm, i0 - max(0, i0 - warm)) if strategie == "goldbot" else 0
    else:
        first = warm
    if len(bars) <= first + 50:
        raise RuntimeError(f"Zu wenige {symbol}-{tf}-Kerzen ({len(bars)}) – Zeitraum verkleinern oder Daten holen")
    log(f"  {symbol} {tf}: {len(bars) - first:,} Kerzen von {pd.Timestamp(bars['time'].iloc[first]).date()} bis "
        f"{pd.Timestamp(bars['time'].iloc[-1]).date()} ({src.source.get((symbol, tf), '?')})".replace(",", "."))

    max_risk_usd = konto * max_risiko_pct / 100 if max_risiko_pct else None
    settings["max_risiko_pct"] = max_risiko_pct
    trades, kz = {}, {}
    for name, cost in scenarios(symbol).items():
        log(f"  Kostenszenario {name} ...")
        if strategie == "goldbot":
            prog = (lambda i, n: log(f"    {100 * i / n:.0f} % der Kerzen", end="\r")) if name == ORDER[0] else None
            tr = goldbot_trades(bars, spec, cost, gcfg, start=first, lot=lot, always_open=always_open,
                                max_risk_usd=max_risk_usd, progress=prog)
            if prog:
                log(" " * 30, end="\r")
        else:
            tr = orb_trades(bars, spec, cost, tf, lot=lot, session=orb["session"], range_min=orb["range_min"],
                            rr=orb["rr"], always_open=always_open, max_risk_usd=max_risk_usd)
        trades[name], kz[name] = tr, aw.kennzahlen(tr, konto, dd_stopp)
    main = trades[szenario]
    mc = aw.monte_carlo(main, konto, laeufe, wochen, block_wochen, ruin_konto) if len(main) else {"laeufe": 0}
    mc_iid = (aw.monte_carlo(main, konto, laeufe, wochen, block_wochen, ruin_konto, einzeln=True)
              if len(main) else {"laeufe": 0})
    halves = aw.je_zeitraum(main, "H")
    out = {
        "strategie": strategie, "symbol": symbol, "zeitrahmen": tf, "einstellungen": settings,
        "konto": konto, "lot": lot, "szenario": szenario, "tage": tage,
        "kosten": {n: c.to_dict() for n, c in scenarios(symbol).items()},
        "kennzahlen": kz, "jahre": aw.je_zeitraum(main, "Y"), "halbjahre": halves,
        "monte_carlo": mc, "monte_carlo_einzeln": mc_iid,
        "urteil": aw.urteil(kz, halves, mc, konto),
        "trades": _trade_list(main), "dauer_s": round(time.time() - t0, 1),
        "erstellt_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "datenquelle": src.source.get((symbol, tf), ""),
    }
    return out


def _trade_list(tr: pd.DataFrame) -> list[dict]:
    out = []
    for x in tr.itertuples():
        out.append({"einstieg": str(x.entry_time)[:16], "ausstieg": str(x.exit_time)[:16],
                    "richtung": "Kauf" if x.direction == 1 else "Verkauf", "setup": x.setup,
                    "kurs": round(float(x.entry), 5), "stop": round(float(x.stop), 5),
                    "usd": round(float(x.pnl), 2), "r": round(float(x.r), 2), "grund": x.exit_reason})
    return out
