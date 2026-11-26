"""Erkennung für den Gold-Bot: Trend, Order Blocks, Liquidität, Plan A und Plan B – nur abgeschlossene Kerzen.

Eigene, ATR-basierte Umsetzung (keine Kopie fremder Indikatoren):

Trend (M15)      Bestätigte Swing-Hochs/-Tiefs (höchste/tiefste Kerze von swing_k links und rechts). Abwärts =
                 letztes Hoch tiefer als das vorige UND letztes Tief tiefer als das vorige, und seit dem letzten Hoch
                 hat keine Kerze darüber geschlossen. Aufwärts spiegelbildlich. Sonst kein Trend -> kein Trade.
Order Block      Abwärts: letzte grüne Kerze, nach der der Kurs binnen ob_abverkauf_kerzen um mindestens
                 ob_abverkauf_atr x ATR fällt und unter ihrem Tief schließt. Zone = Tief..Hoch dieser Kerze (zu hohe
                 Zonen vom äußeren Rand her gekürzt). Gültig, solange der Kurs die Zone seitdem nicht berührt hat;
                 ein Schluss über der Zone bricht die Struktur. Aufwärts spiegelbildlich (letzte rote Kerze).
Liquidität       Sellside = nicht abgeholte Swing-Tiefs (und das letzte Tief seit dem letzten Hoch) unterhalb des
                 Kurses, gleiche Tiefs (innerhalb liquiditaet_toleranz_atr x ATR) zu einem Niveau zusammengefasst.
                 Buyside spiegelbildlich.
Plan A           Mit dem Trend: Limit-Order im nächsten frischen Order Block (Einstieg einstieg_im_ob), Stop
                 stop_puffer_atr x ATR hinter dem Order Block, Ziel 1 = nächste Liquidität mit mindestens min_crv,
                 Ziel 2 = die Liquidität danach. Ohne passende Liquidität (z. B. in neuen Tiefs): Ziel 1 =
                 ziel1_ersatz_crv x Risiko, Ziel 2 = ziel2_ersatz_crv x Risiko.
Plan B           Mit dem Trend: eine Kerze schließt unter dem letzten Schlüsseltief (Swing-Tief), danach kommt der
                 Kurs binnen retest_max_kerzen von unten an das Niveau zurück und die Kerze schließt wieder darunter
                 (Retest) -> Einstieg zum Marktpreis, Stop über dem Hoch des Retests. Aufwärts spiegelbildlich.

Alle Funktionen sehen nur die übergebenen (abgeschlossenen) Kerzen – ein Test prüft, dass sich die Erkennung an
Kerze i nicht ändert, wenn spätere Kerzen dazukommen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr as _atr
from .config import GoldConfig


@dataclass
class OrderBlock:
    direction: int            # -1 = Bear-OB (für Verkäufe), +1 = Bull-OB (für Käufe)
    bar: int                  # Index der OB-Kerze
    time: int
    lo: float
    hi: float
    touched: bool = False
    broken: bool = False


@dataclass
class Level:
    price: float
    count: int                # wie viele Hochs/Tiefs dort liegen (2+ = "gleiche Tiefs", stärkere Liquidität)
    time: int


@dataclass
class Setup:
    plan: str                 # "A" (Limit im Order Block) oder "B" (Retest nach Bruch, Marktorder)
    direction: int
    entry: float | None       # Limit-Kurs (Plan A) oder None = Marktpreis (Plan B)
    ref_price: float          # Plan A: entry; Plan B: Schlusskurs der Retest-Kerze (für Chance/Risiko)
    stop: float
    ziel1: float
    ziel2: float
    atr: float
    key: str                  # eindeutig je Setup (wird nur einmal gehandelt)
    bar_time: int             # Zeit der Kerze, an der das Setup erkannt wurde
    zone: tuple | None = None # Order-Block-Zone (Plan A)
    level: float | None = None  # Schlüsseltief/-hoch (Plan B)
    reason: str = ""

    @property
    def side(self) -> str:
        return "KAUF" if self.direction == 1 else "VERKAUF"

    @property
    def rr1(self) -> float:
        return abs(self.ziel1 - self.ref_price) / max(abs(self.ref_price - self.stop), 1e-9)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["zone"] = list(self.zone) if self.zone else None
        return d


@dataclass
class Analysis:
    n: int
    time: int
    close: float
    atr: float
    trend: int
    trend_text: str
    swing_highs: list = field(default_factory=list)   # [(index, preis)]
    swing_lows: list = field(default_factory=list)
    order_blocks: list = field(default_factory=list)  # gültige OB in Trendrichtung (frisch, nicht gebrochen)
    sellside: list = field(default_factory=list)      # Level unter dem Kurs, nächstes zuerst
    buyside: list = field(default_factory=list)       # Level über dem Kurs, nächstes zuerst
    setups: list = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"Trend M15: {self.trend_text}  |  Schluss {self.close:.2f}  |  ATR {self.atr:.2f}"]
        if self.swing_highs[-2:]:
            out.append("  letzte Hochs: " + ", ".join(f"{p:.2f}" for _, p in self.swing_highs[-2:])
                       + "  |  letzte Tiefs: " + ", ".join(f"{p:.2f}" for _, p in self.swing_lows[-2:]))
        for ob in self.order_blocks[:3]:
            out.append(f"  {'Bear' if ob.direction == -1 else 'Bull'}-Order-Block {ob.lo:.2f}-{ob.hi:.2f}")
        if self.sellside:
            out.append("  Sellside-Liquidität: " + ", ".join(f"{lv.price:.2f}{'*' * (lv.count > 1)}"
                                                           for lv in self.sellside[:3]))
        if self.buyside:
            out.append("  Buyside-Liquidität: " + ", ".join(f"{lv.price:.2f}{'*' * (lv.count > 1)}"
                                                          for lv in self.buyside[:3]))
        for s in self.setups:
            out.append(f"  SETUP Plan {s.plan}: {s.side} " + (f"Limit {s.entry:.2f}" if s.entry else "Markt")
                       + f", Stop {s.stop:.2f}, Ziel 1 {s.ziel1:.2f}, Ziel 2 {s.ziel2:.2f} (CRV {s.rr1:.1f}) – {s.reason}")
        if not self.setups:
            out.append("  kein Setup")
        return out


# ---------------------------------------------------------------------- Bausteine
def to_frame(rates) -> pd.DataFrame:
    """MT5-Kerzen (strukturiertes Array oder DataFrame) -> DataFrame mit time, open, high, low, close."""
    df = pd.DataFrame(rates)
    return df[["time", "open", "high", "low", "close"]].astype({"time": "int64"}).reset_index(drop=True)


def swings(high: np.ndarray, low: np.ndarray, k: int):
    """Bestätigte Swing-Hochs/-Tiefs als Listen [(index, preis)]; ein Swing an j ist erst ab j+k bekannt."""
    n = len(high)
    sh, sl = [], []
    for j in range(k, n - k):
        h, l_ = high[j - k:j + k + 1], low[j - k:j + k + 1]
        if high[j] == h.max() and np.argmax(h) == k:
            sh.append((j, float(high[j])))
        if low[j] == l_.min() and np.argmin(l_) == k:
            sl.append((j, float(low[j])))
    return sh, sl


def trend(close: np.ndarray, sh: list, sl: list) -> tuple[int, str]:
    if len(sh) < 2 or len(sl) < 2:
        return 0, "unklar (zu wenige Hochs/Tiefs)"
    (ih1, h1), (ih2, h2) = sh[-2], sh[-1]
    (il1, l1), (il2, l2) = sl[-2], sl[-1]
    if h2 < h1 and l2 < l1:
        if (close[ih2 + 1:] > h2).any():
            return 0, f"abwärts gebrochen (Schluss über dem letzten Hoch {h2:.2f})"
        return -1, f"abwärts (tiefere Hochs {h1:.2f} > {h2:.2f}, tiefere Tiefs {l1:.2f} > {l2:.2f})"
    if h2 > h1 and l2 > l1:
        if (close[il2 + 1:] < l2).any():
            return 0, f"aufwärts gebrochen (Schluss unter dem letzten Tief {l2:.2f})"
        return 1, f"aufwärts (höhere Hochs {h1:.2f} < {h2:.2f}, höhere Tiefs {l1:.2f} < {l2:.2f})"
    return 0, "seitwärts (keine klare Folge von Hochs/Tiefs)"


def order_blocks(df: pd.DataFrame, a: np.ndarray, direction: int, cfg: GoldConfig) -> list[OrderBlock]:
    """Order Blocks einer Richtung, jüngste zuerst, mit Status berührt/gebrochen (Stand: letzte Kerze)."""
    o, h, l_, c, t = (df[x].to_numpy(float) for x in ("open", "high", "low", "close", "time"))
    n, m = len(df), cfg.ob_displacement_bars
    out = []
    for j in range(max(1, n - cfg.ob_lookback), n - 1):
        if not np.isfinite(a[j]):
            continue
        nxt = slice(j + 1, min(n, j + 1 + m))
        if direction == -1:
            if not c[j] > o[j]:
                continue
            if not (l_[nxt].min() <= l_[j] - cfg.ob_displacement_atr * a[j] and c[nxt].min() < l_[j]):
                continue
            hi = h[j]
            lo = max(l_[j], hi - cfg.ob_max_height_atr * a[j])
        else:
            if not c[j] < o[j]:
                continue
            if not (h[nxt].max() >= h[j] + cfg.ob_displacement_atr * a[j] and c[nxt].max() > h[j]):
                continue
            lo = l_[j]
            hi = min(h[j], lo + cfg.ob_max_height_atr * a[j])
        # gilt erst nach der Verdrängung: welche Kerze hat den Schluss jenseits gebracht?
        end = j + 1 + int(np.argmax(c[nxt] < l_[j])) if direction == -1 else j + 1 + int(np.argmax(c[nxt] > h[j]))
        after = slice(end + 1, n)
        ob = OrderBlock(direction, j, int(t[j]), float(lo), float(hi))
        if direction == -1:
            ob.touched = bool((h[after] >= lo).any())
            ob.broken = bool((c[after] > hi).any())
        else:
            ob.touched = bool((l_[after] <= hi).any())
            ob.broken = bool((c[after] < lo).any())
        out.append(ob)
    return out[::-1]


def liquidity(df: pd.DataFrame, sh: list, sl: list, a_now: float, cfg: GoldConfig) -> tuple[list, list]:
    """(sellside, buyside): nicht abgeholte Tiefs unter / Hochs über dem letzten Schlusskurs, nächstes zuerst."""
    h, l_, c, t = (df[x].to_numpy(float) for x in ("high", "low", "close", "time"))
    n = len(df)
    close = c[-1]
    tol = cfg.liquidity_tolerance_atr * a_now
    lows = [(j, p) for j, p in sl if not (l_[j + 1:] < p).any()]
    highs = [(j, p) for j, p in sh if not (h[j + 1:] > p).any()]
    # letztes Tief seit dem letzten bestätigten Hoch (noch nicht als Swing bestätigt) zählt auch als Liquidität
    if sh:
        j0 = sh[-1][0]
        jl = j0 + int(np.argmin(l_[j0:]))
        if jl < n - 1 and not any(j == jl for j, _ in lows):
            lows.append((jl, float(l_[jl])))
    if sl:
        j0 = sl[-1][0]
        jh = j0 + int(np.argmax(h[j0:]))
        if jh < n - 1 and not any(j == jh for j, _ in highs):
            highs.append((jh, float(h[jh])))

    def cluster(points, below: bool) -> list[Level]:
        pts = sorted(((p, j) for j, p in points if (p < close if below else p > close)), reverse=below)
        levels: list[Level] = []
        for p, j in pts:
            if levels and abs(levels[-1].price - p) <= tol:
                lv = levels[-1]
                lv.count += 1
                lv.price = min(lv.price, p) if below else max(lv.price, p)
                lv.time = max(lv.time, int(t[j]))
            else:
                levels.append(Level(float(p), 1, int(t[j])))
        return levels

    return cluster(lows, True), cluster(highs, False)


def targets(direction: int, ref: float, stop: float, levels: list[Level], a_now: float,
            cfg: GoldConfig) -> tuple[float, float] | None:
    """Ziel 1 = erste Liquidität mit mindestens min_crv, Ziel 2 = die nächste danach (sonst Ersatz-CRV)."""
    risk = abs(ref - stop)
    buf = cfg.target_buffer_atr * a_now
    cand = [lv.price + buf * direction * -1 for lv in levels]          # knapp vor dem Niveau
    cand = [p for p in cand if direction * (p - ref) > 0]
    z1 = next((p for p in cand if direction * (p - ref) >= cfg.min_rr * risk), None)
    if z1 is None:                       # keine Liquidität weit genug entfernt (z. B. neue Tiefs): Ziel in R
        if cfg.ziel1_fallback_rr <= 0:
            return None
        z1 = ref + direction * max(cfg.ziel1_fallback_rr, cfg.min_rr) * risk
    rest = [p for p in cand if direction * (p - z1) >= 0.5 * a_now]
    z2 = rest[0] if rest else ref + direction * max(cfg.ziel2_fallback_rr * risk, 1.5 * abs(z1 - ref))
    return float(z1), float(z2)


# ---------------------------------------------------------------------- Gesamtanalyse
def analyse(df: pd.DataFrame, cfg: GoldConfig) -> Analysis:
    df = df.reset_index(drop=True)
    o, h, l_, c, t = (df[x].to_numpy(float) for x in ("open", "high", "low", "close", "time"))
    n = len(df)
    a = _atr(h, l_, c, cfg.atr_period)
    a_now = float(a[-1]) if n and np.isfinite(a[-1]) else float("nan")
    sh, sl = swings(h, l_, cfg.swing_k)
    d, text = trend(c, sh, sl)
    res = Analysis(n, int(t[-1]) if n else 0, float(c[-1]) if n else float("nan"), a_now, d, text, sh, sl)
    if n < 50 or not np.isfinite(a_now):
        res.trend, res.trend_text = 0, "zu wenige Kerzen"
        return res
    res.sellside, res.buyside = liquidity(df, sh, sl, a_now, cfg)
    if d == 0:
        return res
    obs = [ob for ob in order_blocks(df, a, d, cfg) if not ob.touched and not ob.broken]
    # nur Order Blocks auf der richtigen Seite des Kurses und in Reichweite
    obs = [ob for ob in obs if (ob.lo > c[-1] if d == -1 else ob.hi < c[-1])
           and abs((ob.lo if d == -1 else ob.hi) - c[-1]) <= cfg.ob_max_distance_atr * a_now]
    obs.sort(key=lambda ob: abs((ob.lo if d == -1 else ob.hi) - c[-1]))
    res.order_blocks = obs
    levels = res.sellside if d == -1 else res.buyside

    if cfg.plan_a and obs:
        ob = obs[0]
        near, far = (ob.lo, ob.hi) if d == -1 else (ob.hi, ob.lo)
        entry = near + (far - near) * cfg.entry_in_ob
        stop = far - d * cfg.stop_buffer_atr * a_now
        risk = abs(entry - stop)
        if cfg.min_stop_atr * a_now <= risk <= cfg.max_stop_atr * a_now:
            tg = targets(d, entry, stop, levels, a_now, cfg)
            if tg:
                res.setups.append(Setup("A", d, round(entry, 2), round(entry, 2), round(stop, 2), round(tg[0], 2),
                                        round(tg[1], 2), a_now, f"A{d}:{ob.time}", int(t[-1]), (ob.lo, ob.hi),
                                        reason=f"Rücksetzer in den {'Bear' if d == -1 else 'Bull'}-Order-Block "
                                               f"{ob.lo:.2f}-{ob.hi:.2f}"))

    if cfg.plan_b:
        s = _plan_b(o, h, l_, c, t, a, sh if d == 1 else sl, d, levels, a_now, cfg)
        if s:
            res.setups.append(s)
    return res


def _plan_b(o, h, l_, c, t, a, swing_list, d, levels, a_now, cfg) -> Setup | None:
    """Retest nach Schluss jenseits des Schlüsseltiefs (d=-1) bzw. -hochs (d=+1) – nur auf der letzten Kerze."""
    n = len(c)
    r = n - 1
    for jb in range(r - 1, max(r - 1 - cfg.retest_max_bars, 0), -1):          # Bruchkerze jb vor der Retest-Kerze r
        # Schlüsselniveau: letztes bestätigtes Swing-Tief/-Hoch, das VOR der Bruchkerze bekannt war
        known = [(j, p) for j, p in swing_list if j + cfg.swing_k < jb]
        if not known:
            continue
        lvl = known[-1][1]
        crossed = (c[jb] < lvl <= c[jb - 1]) if d == -1 else (c[jb] > lvl >= c[jb - 1])
        if not crossed or abs(c[jb] - o[jb]) < cfg.break_min_body_atr * a[jb]:
            continue
        mid = slice(jb + 1, r)
        tol = cfg.retest_tolerance_atr * a_now
        if d == -1:
            if (c[mid] >= lvl).any() or (h[mid] >= lvl - tol).any():               # schon gescheitert / schon getestet
                return None
            if not (h[r] >= lvl - tol and c[r] < lvl):
                return None
            stop = h[jb + 1:r + 1].max() + cfg.stop_buffer_atr * a_now
        else:
            if (c[mid] <= lvl).any() or (l_[mid] <= lvl + tol).any():
                return None
            if not (l_[r] <= lvl + tol and c[r] > lvl):
                return None
            stop = l_[jb + 1:r + 1].min() - cfg.stop_buffer_atr * a_now
        ref = c[r]
        risk = abs(ref - stop)
        if not (cfg.min_stop_atr * a_now <= risk <= cfg.max_stop_atr * a_now):
            return None
        tg = targets(d, ref, stop, levels, a_now, cfg)
        if not tg:
            return None
        word = "Tief" if d == -1 else "Hoch"
        return Setup("B", d, None, round(float(ref), 2), round(float(stop), 2), round(tg[0], 2), round(tg[1], 2),
                     a_now, f"B{d}:{int(t[jb])}", int(t[r]), level=round(float(lvl), 2),
                     reason=f"Schluss {'unter' if d == -1 else 'über'} dem Schlüssel-{word} {lvl:.2f}, Retest bestätigt")
    return None
