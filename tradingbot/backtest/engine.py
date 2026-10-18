"""Handelssimulation auf Kerzenbasis (Stufe 1, in Kursen – unabhängig von Kontogröße).

Annahmen (konservativ):
  * Kerzenkurse sind Bid-Kurse (MT5). Ask = Bid + Spread der Kerze.
  * Signal am Schluss von Kerze i -> Einstieg zur Eröffnung von i+1 (Long zum Ask, Short zum Bid),
    jeweils zuzüglich Slippage. Liegt zwischen i und i+1 eine Lücke (Wochenende, Datenausfall),
    entfällt der Einstieg – keine Ausführung auf veralteten Signalen.
  * Stop (Stop-Order) und Ziel (Limit-Order) werden innerhalb der Kerze über High/Low geprüft:
    Long-Stop bei Bid-Tief <= Stop, Short-Stop bei Ask-Hoch >= Stop. Werden Stop und Ziel in derselben
    Kerze berührt, gilt der Stop (ungünstigster Fall).
  * Eröffnet eine Kerze bereits jenseits des Stops (Lücke), wird zum Eröffnungskurs ausgeführt –
    nie zum Stop-Kurs innerhalb der Lücke.
  * Ausstiegssignal am Schluss von Kerze j -> Ausführung zur Eröffnung von j+1.
  * Höchstens max_positions gleichzeitig offene Positionen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategies.base import Signals

CHUNK = 256


def simulate(bars: pd.DataFrame, sig: Signals, tf_seconds: int, spread_price: np.ndarray,
             slippage_frac: float, max_positions: int = 2) -> tuple[pd.DataFrame, dict]:
    o = bars["open"].to_numpy(dtype=float)
    h = bars["high"].to_numpy(dtype=float)
    l = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    t = pd.DatetimeIndex(bars["time"]).as_unit("ns").asi8
    ts = bars["time_server"].to_numpy(dtype="int64")
    sp = np.asarray(spread_price, dtype=float)
    n = len(c)
    contiguous = np.zeros(n, dtype=bool)
    contiguous[:-1] = np.diff(t) == tf_seconds * 10**9
    ex_long = sig.exit_long if sig.exit_long is not None else np.zeros(n, dtype=bool)
    ex_short = sig.exit_short if sig.exit_short is not None else np.zeros(n, dtype=bool)
    ex_long = np.asarray(ex_long, dtype=bool)
    ex_short = np.asarray(ex_short, dtype=bool)

    skipped = {"gap": 0, "end_of_data": 0, "max_positions": 0, "invalid_stop": 0}
    rows = []
    open_exits: list[int] = []
    for k in range(len(sig)):
        i = int(sig.idx[k])
        e = i + 1
        if e >= n:
            skipped["end_of_data"] += 1
            continue
        if not contiguous[i]:
            skipped["gap"] += 1
            continue
        open_exits = [x for x in open_exits if x >= e]
        if len(open_exits) >= max_positions:
            skipped["max_positions"] += 1
            continue
        d = int(sig.direction[k])
        stop, target = float(sig.stop[k]), float(sig.target[k])
        slip_e = slippage_frac * o[e]
        entry = o[e] + sp[e] + slip_e if d == 1 else o[e] - slip_e
        if d * (entry - stop) <= 0 or (np.isfinite(target) and d * (target - entry) <= 0):
            skipped["invalid_stop"] += 1
            continue
        exit_arr = ex_long if d == 1 else ex_short
        time_stop = e + sig.max_bars - 1 if sig.max_bars else n  # Schluss dieser Kerze -> raus

        j0 = e
        exit_i, exit_px, reason = n - 1, None, "end"
        size = CHUNK
        while j0 < n:
            j1 = min(n, j0 + size)
            if d == 1:
                stop_hit = l[j0:j1] <= stop
                tgt_hit = h[j0:j1] >= target if np.isfinite(target) else np.zeros(j1 - j0, bool)
            else:
                stop_hit = h[j0:j1] + sp[j0:j1] >= stop
                tgt_hit = l[j0:j1] + sp[j0:j1] <= target if np.isfinite(target) else np.zeros(j1 - j0, bool)
            intrabar = stop_hit | tgt_hit
            sigx = exit_arr[j0:j1].copy()
            if j0 <= time_stop < j1:
                sigx[time_stop - j0] = True
            ji = int(np.argmax(intrabar)) if intrabar.any() else None
            js = int(np.argmax(sigx)) if sigx.any() else None
            if ji is None and js is None:
                j0, size = j1, size * 2
                continue
            if js is None or (ji is not None and ji <= js):
                j = j0 + ji
                exit_i = j
                if d == 1:
                    if o[j] <= stop or stop_hit[ji]:
                        px_open = o[j]
                        if px_open <= stop:
                            exit_px, reason = px_open - slippage_frac * px_open, "stop_gap"
                        else:
                            exit_px, reason = stop - slippage_frac * stop, "stop"
                    else:
                        exit_px, reason = (max(o[j], target) if o[j] >= target else target), "target"
                else:
                    ask_open = o[j] + sp[j]
                    if ask_open >= stop or stop_hit[ji]:
                        if ask_open >= stop:
                            exit_px, reason = ask_open + slippage_frac * ask_open, "stop_gap"
                        else:
                            exit_px, reason = stop + slippage_frac * stop, "stop"
                    else:
                        exit_px, reason = (ask_open if ask_open <= target else target), "target"
            else:
                j = j0 + js
                reason = "time" if j == time_stop and not exit_arr[j] else "signal"
                if j + 1 < n:
                    exit_i = j + 1
                    exit_px = (o[j + 1] - slippage_frac * o[j + 1] if d == 1
                               else o[j + 1] + sp[j + 1] + slippage_frac * o[j + 1])
                else:
                    exit_i, reason = j, "end"
                    exit_px = c[j] if d == 1 else c[j] + sp[j]
            break
        if exit_px is None:  # Datenende
            exit_i = n - 1
            exit_px = c[-1] if d == 1 else c[-1] + sp[-1]
        rows.append((i, e, exit_i, t[e], t[exit_i], ts[e], ts[exit_i], d, entry, exit_px, stop, target,
                     abs(entry - stop), sp[e], reason))
        open_exits.append(exit_i)

    cols = ["signal_i", "entry_i", "exit_i", "entry_time", "exit_time", "entry_server", "exit_server", "direction",
            "entry", "exit", "stop", "target", "risk", "spread_entry", "exit_reason"]
    df = pd.DataFrame(rows, columns=cols)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df["bars"] = df["exit_i"] - df["entry_i"]
    df["pnl_price"] = df["direction"] * (df["exit"] - df["entry"])
    df["r_gross"] = df["pnl_price"] / df["risk"]
    return df, skipped
