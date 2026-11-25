"""Trades erzeugen: dieselben Regeln wie die Bots, Kerze für Kerze auf echten MT5-Kerzen, mit Kosten.

Ergebnis ist immer eine Tabelle mit einer Zeile je Trade (alle Teile zusammen):
  entry_time, exit_time, direction, setup, entry, stop, risk, risk_usd, volume, gross, commission, swap, pnl, r, exit_reason
risk_usd/gross/commission/swap/pnl in Kontowährung (USD) für die angegebene Lotgröße; r = pnl / (Risiko bis zum Stop in USD).
Spread und Slippage stecken schon in den Ein- und Ausstiegskursen.

Gemeinsame Annahmen (wie im Backtest tradingbot/backtest/engine.py):
  * Kerzen sind Bid-Kurse; Ask = Bid + Spread der Kerze (Kostenszenario: Faktor auf den Kerzen-Spread).
  * Signal am Schluss einer Kerze -> Einstieg frühestens zur Eröffnung der nächsten.
  * Berührt eine Kerze Stop und Ziel, zählt der Stop.
  * Eröffnet eine Kerze schon jenseits des Stops (Lücke), wird zum Eröffnungskurs ausgestiegen – der Verlust ist
    dann größer als 1 R. Die alte Zeitmaschine hat das als −1 R gezählt.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import features
from ..backtest.account import swap_amount, swap_nights
from ..backtest.costs import CostScenario
from ..backtest.engine import simulate as engine_simulate
from ..instruments import InstrumentSpec
from ..strategies import base as strat_base
from ..strategies import library as _library  # noqa: F401 – meldet die Strategien an
from ..timeframes import seconds

TRADE_COLS = ["entry_time", "exit_time", "direction", "setup", "entry", "stop", "risk", "risk_usd", "volume", "gross",
              "commission", "swap", "pnl", "r", "exit_reason"]


def _finish(rows: list[dict], spec: InstrumentSpec, cost: CostScenario, always_open: bool) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=TRADE_COLS)
    df = pd.DataFrame(rows)
    cs = spec.contract_size
    df["commission"] = -(cost.commission_per_lot_rt * df["volume"] + cost.commission_frac_rt * df["entry"] * cs
                         * df["volume"])
    if cost.swap:
        df["swap"] = [swap_amount(spec, int(d), float(v), float(e), float(x),
                                  swap_nights(int(a), int(b), always_open, spec.swap_rollover3days))[0]
                      for d, v, e, x, a, b in zip(df["direction"], df["volume"], df["entry"], df["exit_px"],
                                                  df["entry_server"], df["exit_server"])]
    else:
        df["swap"] = 0.0
    df["pnl"] = df["gross"] + df["commission"] + df["swap"]
    df["r"] = df["pnl"] / (df["risk"] * cs * df["volume"])
    df["risk_usd"] = df["risk"] * cs * df["volume"]
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    return df[TRADE_COLS].sort_values("entry_time").reset_index(drop=True)


# ============================================================================ Opening Range Breakout
ORB_DEFAULT_SESSION = {"XAUUSD": "new_york", "BTCUSD": "new_york", "EURUSD": "london"}


def orb_trades(bars: pd.DataFrame, spec: InstrumentSpec, cost: CostScenario, tf: str, lot: float = 0.01,
               session: str = "new_york", range_min: int = 15, rr: float = 1.5,
               always_open: bool = False, max_risk_usd: float | None = None) -> pd.DataFrame:
    """Opening Range Breakout wie im Demo-Bot (config.toml): erster Schluss jenseits der Range, Stop Gegenseite,
    Ziel rr x Risiko, glatt zum Handelsschluss. Ausführung über den Backtest-Simulator (engine.py).
    max_risk_usd: Trades mit größerem Verlust bis zum Stop werden ausgelassen (wie eine Risikogrenze im Bot)."""
    strat = strat_base.create("opening_range_breakout", session=session, range_min=range_min, rr=rr)
    with features.shared(bars):
        sig = strat.entries(bars)
    sp = cost.spread_price(bars["spread"].to_numpy(float), spec.point)
    raw, _ = engine_simulate(bars, sig, seconds(tf), sp, cost.slippage_frac, max_positions=1)
    rows = []
    for x in raw.itertuples():
        if max_risk_usd is not None and float(x.risk) * spec.contract_size * lot > max_risk_usd:
            continue
        rows.append(dict(entry_time=x.entry_time, exit_time=x.exit_time, direction=int(x.direction), setup="ORB",
                         entry=float(x.entry), stop=float(x.stop), risk=float(x.risk), volume=lot,
                         gross=float(x.pnl_price) * spec.contract_size * lot, exit_px=float(x.exit),
                         entry_server=int(x.entry_server), exit_server=int(x.exit_server),
                         exit_reason=str(x.exit_reason)))
    return _finish(rows, spec, cost, always_open)


# ============================================================================ Gold-Bot (Order Blocks)
def goldbot_trades(bars: pd.DataFrame, spec: InstrumentSpec, cost: CostScenario, gcfg, start: int | None = None,
                   lot: float | None = None, always_open: bool = False, max_risk_usd: float | None = None,
                   progress=None) -> pd.DataFrame:
    """Gold-Bot-Regeln (tradingbot/goldbot/strategie.py) Kerze für Kerze auf M15, so nah wie möglich am Live-Bot:

    * Analyse nur mit den bis dahin abgeschlossenen Kerzen (gleiche Funktion analyse() wie live).
    * Plan A: Limit im Order Block ab der nächsten Kerze; Kauf-Limit wird ausgeführt, wenn der ASK den Einstieg
      erreicht (Tief + Spread), Verkaufs-Limit beim Bid. Verfällt nach limit_gueltig_kerzen, wird gelöscht, wenn der
      Trend kippt, freitags ab freitag_bis_utc, oder wenn Ziel 1 vor dem Einstieg erreicht wird.
    * Plan B: Marktorder zur Eröffnung der nächsten Kerze (+ Spread beim Kauf, + Slippage).
    * Teile wie in goldbot.toml (2 x lot: Teil 1 bis Ziel 1, Teil 2 bis Ziel 2; nach Ziel 1 Stop auf Einstieg).
    * Handelszeiten (einstieg_von/bis_utc, freitag_bis_utc), max. Spread, max. Trades pro Tag, ein Trade zur Zeit.
    * Risikogrenze wie live: Verlust bis zum Stop (alle Teile) höher als max_risk_usd -> kein Trade
      (live: risiko_je_trade_pct vom Kontostand; hier vom Startkonto).
    Nicht nachgebildet: KI-Prüfung, Tagesverlust- und Drawdown-Stopp (die rechnet die Auswertung getrennt aus).
    """
    from ..goldbot.strategie import analyse

    lot = gcfg.lot if lot is None else lot
    parts = max(1, int(gcfg.parts))
    df = bars.reset_index(drop=True)
    o, h, l_, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sp = cost.spread_price(df["spread"].to_numpy(float), spec.point)
    sp_pts = df["spread"].to_numpy(float)
    t = pd.DatetimeIndex(df["time"])
    t_sec = (t.as_unit("ns").asi8 // 10**9).astype("int64")
    ts = df["time_server"].to_numpy("int64")
    tf_s = int(np.median(np.diff(t_sec[: min(len(t_sec), 2000)]))) if len(t_sec) > 2 else 900
    contiguous = np.zeros(len(df), bool)
    contiguous[:-1] = np.diff(t_sec) == tf_s
    afr = pd.DataFrame({"time": t_sec, "open": o, "high": h, "low": l_, "close": c})
    n = len(df)
    start = start if start is not None else min(gcfg.bars, n - 1)
    slip = cost.slippage_frac
    seen: set[str] = set()
    per_day: dict = {}
    rows: list[dict] = []
    cur: dict | None = None

    def close_leg(tr, k, px, i, reason):
        tr["legs"][k]["open"] = False
        tr["legs"][k]["px"] = px
        tr["legs"][k]["reason"] = reason
        if not any(lg["open"] for lg in tr["legs"]):
            tr["status"], tr["exit_i"] = "beendet", i

    def entry_allowed(i) -> bool:
        close_t = t[i] + pd.Timedelta(seconds=tf_s)
        if close_t.weekday() >= 5 or not (gcfg.entry_from_utc <= close_t.hour < gcfg.entry_to_utc):
            return False
        if close_t.weekday() == 4 and close_t.hour >= gcfg.friday_to_utc:
            return False
        if sp_pts[i] > gcfg.max_spread_points:
            return False
        return per_day.get(close_t.date(), 0) < gcfg.max_trades_per_day

    for i in range(start, n):
        # ---------------------------------------------------------- 1) laufende Order/Position mit Kerze i
        if cur is not None:
            d = cur["d"]
            if cur["status"] == "markt":
                if not contiguous[i - 1]:                         # Lücke (Wochenende): Signal veraltet
                    cur = None
                else:
                    px = o[i] + sp[i] + slip * o[i] if d == 1 else o[i] - slip * o[i]
                    if d * (px - cur["stop"]) <= 0 or d * (cur["ziel1"] - px) <= 0:
                        cur = None
                    else:
                        cur.update(status="offen", entry=px, fill_i=i)
            if cur is not None and cur["status"] == "wartend":
                ask_lo, bid_hi = l_[i] + sp[i], h[i]
                touched = (bid_hi >= cur["entry"]) if d == -1 else (ask_lo <= cur["entry"])
                reached = (l_[i] + sp[i] <= cur["ziel1"]) if d == -1 else (h[i] >= cur["ziel1"])
                ct = t[i]
                friday_cut = ct.weekday() == 4 and ct.hour >= gcfg.friday_to_utc
                if friday_cut or ct.weekday() >= 5:
                    cur = None
                elif touched:
                    # Lücke durch den Einstieg: zum (besseren) Eröffnungskurs
                    if d == 1:
                        px = min(cur["entry"], o[i] + sp[i])
                    else:
                        px = max(cur["entry"], o[i])
                    cur.update(status="offen", entry=px, fill_i=i)
                elif reached:
                    cur = None                                       # verpasst
                elif i - cur["placed_i"] >= gcfg.pending_valid_bars:
                    cur = None                                       # verfallen
            if cur is not None and cur["status"] == "offen":
                d = cur["d"]
                ask_o, ask_h, ask_l = o[i] + sp[i], h[i] + sp[i], l_[i] + sp[i]
                stop_hit = (l_[i] <= cur["stop"]) if d == 1 else (ask_h >= cur["stop"])
                if stop_hit:
                    if d == 1:
                        px = (o[i] if o[i] <= cur["stop"] else cur["stop"]) * (1 - slip)
                        gap = o[i] <= cur["stop"]
                    else:
                        px = (ask_o if ask_o >= cur["stop"] else cur["stop"]) * (1 + slip)
                        gap = ask_o >= cur["stop"]
                    why = "stop_luecke" if gap else ("einstieg" if cur["be"] else "stop")
                    for k, lg in enumerate(cur["legs"]):
                        if lg["open"]:
                            close_leg(cur, k, px, i, why)
                elif i > cur["fill_i"] or cur["setup"] == "B":
                    for k, lg in enumerate(cur["legs"]):
                        if lg["open"] and ((ask_l <= lg["z"]) if d == -1 else (h[i] >= lg["z"])):
                            close_leg(cur, k, lg["z"], i, "ziel")
                    if not cur["be"] and ((ask_l <= cur["ziel1"]) if d == -1 else (h[i] >= cur["ziel1"])):
                        cur["be"], cur["stop"] = True, cur["entry"]
            if cur is not None and cur["status"] == "beendet":
                rows.append(_goldbot_row(cur, t, ts, spec, lot))
                cur = None

        # ---------------------------------------------------------- 2) Analyse wie live (nur wenn sie etwas ändert)
        if cur is not None and cur["status"] in ("offen", "markt"):
            continue
        if i + 1 >= n:
            break
        an = analyse(afr.iloc[max(0, i + 1 - gcfg.bars):i + 1], gcfg)
        if cur is not None and cur["status"] == "wartend" and an.trend != cur["d"]:
            cur = None
        if cur is None and an.setups and entry_allowed(i):
            for s in sorted(an.setups, key=lambda s: s.plan != "B"):
                if s.key in seen:
                    continue
                seen.add(s.key)
                if max_risk_usd is not None and abs(s.ref_price - s.stop) * spec.contract_size * lot * parts \
                        > max_risk_usd:
                    continue
                legs = ([{"z": s.ziel1, "open": True}, {"z": s.ziel2, "open": True}] if parts == 2
                        else [{"z": s.ziel2, "open": True}])
                cur = {"setup": s.plan, "d": s.direction, "entry": s.entry, "stop": s.stop, "stop0": s.stop,
                       "ziel1": s.ziel1, "placed_i": i, "fill_i": None, "legs": legs, "be": False,
                       "status": "wartend" if s.plan == "A" else "markt"}
                day = (t[i] + pd.Timedelta(seconds=tf_s)).date()
                per_day[day] = per_day.get(day, 0) + 1
                break
        if progress and i % 2000 == 0:
            progress(i, n)
    if cur is not None and cur["status"] == "offen":                 # Datenende: zum letzten Schlusskurs
        for k, lg in enumerate(cur["legs"]):
            if lg["open"]:
                close_leg(cur, k, c[-1] + (sp[-1] if cur["d"] == -1 else 0.0), n - 1, "datenende")
        rows.append(_goldbot_row(cur, t, ts, spec, lot))
    return _finish(rows, spec, cost, always_open)


def _goldbot_row(tr, t, ts, spec, lot) -> dict:
    d = tr["d"]
    vol = lot * len(tr["legs"])
    gross = sum(d * (lg["px"] - tr["entry"]) for lg in tr["legs"]) * spec.contract_size * lot
    reasons = [lg["reason"] for lg in tr["legs"]]
    return dict(entry_time=t[tr["fill_i"]], exit_time=t[tr["exit_i"]], direction=d, setup=f"Plan {tr['setup']}",
                entry=float(tr["entry"]), stop=float(tr["stop0"]), risk=abs(tr["entry"] - tr["stop0"]), volume=vol,
                gross=float(gross), exit_px=float(np.mean([lg["px"] for lg in tr["legs"]])),
                entry_server=int(ts[tr["fill_i"]]), exit_server=int(ts[tr["exit_i"]]),
                exit_reason="+".join(reasons))
