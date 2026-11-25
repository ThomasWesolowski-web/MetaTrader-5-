"""Einstellungen des Gold-Bots aus goldbot.toml (Beschreibung: docs/goldbot.md).

Harte Obergrenzen stehen hier im Code; goldbot.toml kann nur darunter gehen. Der Gold-Bot handelt NUR auf einem
Demokonto (execution.py prüft das vor jeder Order) – ein Live-Konto ist hier bewusst nicht einstellbar.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

CAP_LOT = 0.10                 # je Teil
CAP_PARTS = 2
CAP_RISK_PCT = 5.0             # Verlust bis zum Stop (alle Teile zusammen) vom Kontostand
CAP_DAILY_LOSS_PCT = 10.0      # eigene Verluste heute (realisiert + offen) vom Kontostand
CAP_DRAWDOWN_PCT = 25.0        # eigener Rückgang vom eigenen Höchststand, bezogen auf den Startkontostand
CAP_TRADES_PER_DAY = 6


@dataclass
class GoldConfig:
    # [goldbot]
    active: bool = True
    orders_enabled: bool = True
    notify: bool = True
    terminal_path: str = ""
    demo_login: int = 0                     # 0 = jedes Demokonto, sonst nur genau dieses
    symbol: str = "XAUUSD"
    # [handel]
    lot: float = 0.01                       # je Teil
    parts: int = 2                          # 2 = Teil 1 bis Ziel 1, Teil 2 bis Ziel 2; 1 = ein Teil bis Ziel 2
    max_risk_pct: float = 3.0
    max_daily_loss_pct: float = 6.0
    max_drawdown_pct: float = 15.0
    max_trades_per_day: int = 3
    max_spread_points: int = 80
    entry_from_utc: int = 1                 # neue Einstiege nur von ... bis ... Uhr UTC
    entry_to_utc: int = 20
    friday_to_utc: int = 18                 # freitags nur bis ... Uhr UTC, danach werden Limit-Orders gelöscht
    deviation_points: int = 100
    # [strategie]
    plan_a: bool = True
    plan_b: bool = True
    bars: int = 600                         # M15-Kerzen für die Analyse
    atr_period: int = 14
    swing_k: int = 3                        # Swing-Hoch/-Tief: höchste/tiefste Kerze von k links und k rechts
    ob_displacement_atr: float = 1.5        # Abverkauf/Anstieg nach dem Order Block mindestens so viele ATR
    ob_displacement_bars: int = 3
    ob_max_height_atr: float = 2.0          # höhere Order Blocks werden auf diese Höhe (vom äußeren Rand) gekürzt
    ob_max_distance_atr: float = 8.0        # Order Block höchstens so weit vom Kurs
    ob_lookback: int = 150                  # so viele Kerzen zurück nach Order Blocks suchen
    entry_in_ob: float = 0.5                # 0 = vorderer Rand, 0.5 = Mitte, 1 = hinterer Rand des Order Blocks
    stop_buffer_atr: float = 0.3            # Stop so weit hinter dem Order Block bzw. dem Retest
    min_stop_atr: float = 0.5
    max_stop_atr: float = 5.0
    min_rr: float = 1.0                     # Ziel 1 mindestens so weit wie der Stop
    target_buffer_atr: float = 0.1          # Ziel knapp VOR der Liquidität (wird sicherer erreicht)
    liquidity_tolerance_atr: float = 0.25   # gleiche Hochs/Tiefs innerhalb dieser Spanne = ein Liquiditätsniveau
    ziel1_fallback_rr: float = 1.5          # keine Liquidität mit min_crv: Ziel 1 = Einstieg + 1,5 x Risiko (0 = kein Trade)
    ziel2_fallback_rr: float = 3.0          # keine zweite Liquidität: Ziel 2 = Einstieg + 3 x Risiko
    break_min_body_atr: float = 0.3         # Plan B: Schlusskurs unter dem Schlüsseltief mit Körper >= 0,3 ATR
    retest_max_bars: int = 8                # Plan B: Retest binnen 8 Kerzen nach dem Bruch
    retest_tolerance_atr: float = 0.15
    pending_valid_bars: int = 32            # Limit-Order verfällt nach 32 Kerzen (8 Std.)
    # [ki]
    ai_enabled: bool = False
    ai_key_file: str = "ki_zugang.toml"
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_on_error: str = "ablehnen"           # "ablehnen" = kein Trade, "handeln" = nach den Regeln handeln
    ai_timeout_s: float = 90.0
    # intern
    data_root: str = "data"
    base_dir: Path = Path(".")

    def api_key(self) -> str | None:
        env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env:
            return env
        p = Path(self.ai_key_file)
        if not p.is_absolute():
            p = self.base_dir / p
        if not p.exists():
            return None
        key = str(tomllib.loads(p.read_text(encoding="utf-8")).get("api_key", "")).strip()
        return key if key.startswith("sk-") and "..." not in key else None


SECTIONS = {
    "goldbot": {"aktiv": "active", "orders": "orders_enabled", "benachrichtigen": "notify",
                "terminal_pfad": "terminal_path", "demo_konto": "demo_login", "symbol": "symbol"},
    "handel": {"lot": "lot", "teile": "parts", "risiko_je_trade_pct": "max_risk_pct",
               "max_tagesverlust_pct": "max_daily_loss_pct", "max_drawdown_pct": "max_drawdown_pct",
               "max_trades_pro_tag": "max_trades_per_day", "max_spread_points": "max_spread_points",
               "einstieg_von_utc": "entry_from_utc", "einstieg_bis_utc": "entry_to_utc",
               "freitag_bis_utc": "friday_to_utc", "abweichung_points": "deviation_points"},
    "strategie": {"plan_a": "plan_a", "plan_b": "plan_b", "kerzen": "bars", "atr_periode": "atr_period",
                  "swing_k": "swing_k", "ob_abverkauf_atr": "ob_displacement_atr",
                  "ob_abverkauf_kerzen": "ob_displacement_bars", "ob_max_hoehe_atr": "ob_max_height_atr",
                  "ob_max_abstand_atr": "ob_max_distance_atr", "ob_suche_kerzen": "ob_lookback",
                  "einstieg_im_ob": "entry_in_ob", "stop_puffer_atr": "stop_buffer_atr",
                  "min_stop_atr": "min_stop_atr", "max_stop_atr": "max_stop_atr", "min_crv": "min_rr",
                  "ziel_puffer_atr": "target_buffer_atr", "liquiditaet_toleranz_atr": "liquidity_tolerance_atr",
                  "ziel1_ersatz_crv": "ziel1_fallback_rr", "ziel2_ersatz_crv": "ziel2_fallback_rr", "bruch_min_koerper_atr": "break_min_body_atr",
                  "retest_max_kerzen": "retest_max_bars", "retest_toleranz_atr": "retest_tolerance_atr",
                  "limit_gueltig_kerzen": "pending_valid_bars"},
    "ki": {"aktiv": "ai_enabled", "zugang": "ai_key_file", "modell": "ai_model", "aufwand": "ai_effort",
           "bei_fehler": "ai_on_error", "zeitlimit_s": "ai_timeout_s"},
}


def load(path: str | Path, data_root: str = "data") -> GoldConfig:
    path = Path(path)
    d = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    c = GoldConfig(data_root=data_root, base_dir=path.resolve().parent)
    types = {f.name: f.type for f in fields(GoldConfig)}
    for sec, keys in SECTIONS.items():
        raw = d.get(sec, {})
        unknown = set(raw) - set(keys)
        if unknown:
            raise ValueError(f"goldbot.toml [{sec}]: unbekannte Einstellung(en) {', '.join(sorted(unknown))}")
        for k, attr in keys.items():
            if k in raw:
                cast = {"bool": bool, "int": int, "float": float, "str": str}[types[attr]]
                setattr(c, attr, cast(raw[k]))
    # harte Obergrenzen
    c.lot = min(max(c.lot, 0.0), CAP_LOT)
    c.parts = min(max(c.parts, 1), CAP_PARTS)
    c.max_risk_pct = min(max(c.max_risk_pct, 0.0), CAP_RISK_PCT)
    c.max_daily_loss_pct = min(max(c.max_daily_loss_pct, 0.0), CAP_DAILY_LOSS_PCT)
    c.max_drawdown_pct = min(max(c.max_drawdown_pct, 0.0), CAP_DRAWDOWN_PCT)
    c.max_trades_per_day = min(max(c.max_trades_per_day, 0), CAP_TRADES_PER_DAY)
    c.entry_in_ob = min(max(c.entry_in_ob, 0.0), 1.0)
    if c.lot <= 0:
        raise ValueError("goldbot.toml: lot muss größer als 0 sein (z. B. 0.01)")
    if c.ai_on_error not in ("ablehnen", "handeln"):
        raise ValueError("goldbot.toml [ki]: bei_fehler muss \"ablehnen\" oder \"handeln\" sein")
    if c.ai_effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("goldbot.toml [ki]: aufwand muss low, medium, high, xhigh oder max sein")
    return c
