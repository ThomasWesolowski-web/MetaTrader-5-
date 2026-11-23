"""Einstellungen der Telegram-Signale aus telegram.toml, Zugangsdaten aus telegram_zugang.toml (nicht im Repo)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Obergrenzen, fest im Code (Freigabe 23.11.2026: 10 % je Signal, nur Demokonto). telegram.toml kann nur darunter gehen.
CAP_RISK_PCT = 10.0
CAP_DAILY_LOSS_PCT = 20.0
CAP_DRAWDOWN_PCT = 40.0
CAP_POSITIONS = 2
CAP_LIVE_LOT = 0.05          # Live-Konto: feste Lotgröße höchstens so groß (Freigabe 23.11.2026: 0,01 Lot)
FOLLOWUP_MODES = ("alle", "nur_be_und_schliessen", "aus")


@dataclass
class TelegramConfig:
    channel: str = "FX Culture | Premium FX"
    credentials_file: str = "telegram_zugang.toml"
    orders_enabled: bool = True
    account: str = "demo"                                # "demo" oder "live"
    live_account: int = 0                                # Kontonummer des Live-Kontos (vom Nutzer eingetragen)
    lot: float = 0.0                                     # feste Lotgröße je Signal; 0 = aus dem Risiko berechnen
    symbol_map: dict = field(default_factory=dict)      # Name im Kanal -> Name beim Broker
    symbol_suffix: str = ""
    allowed: list = field(default_factory=list)         # leer = alle Symbole, die der Broker anbietet
    risk_pct: float = 10.0
    max_daily_loss_pct: float = 20.0
    max_drawdown_pct: float = 40.0
    max_positions: int = 2
    max_signals_per_day: int = 4
    min_rr: float = 1.0
    entry_wait_min: int = 120
    tolerance_r: float = 0.2
    target: str = "erstes"
    partial_pct: float = 50.0
    followups: str = "alle"
    followup_valid_h: float = 24.0
    max_signal_age_s: int = 180
    deviation_points: int = 100
    data_root: str = "data"
    base_dir: Path = Path(".")

    @property
    def live_login(self) -> int | None:
        return self.live_account if self.account == "live" and self.live_account > 0 else None

    def broker_symbol(self, sym: str) -> str:
        return self.symbol_map.get(sym, sym + self.symbol_suffix)

    def credentials(self) -> tuple[int, str]:
        p = Path(self.credentials_file)
        if not p.is_absolute():
            p = self.base_dir / p
        if not p.exists():
            raise FileNotFoundError(
                f"{p} fehlt. Bitte anlegen mit:\n  api_id = 1234567\n  api_hash = \"...\"\n"
                f"(beides von https://my.telegram.org -> API development tools)")
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        api_id, api_hash = d.get("api_id"), str(d.get("api_hash", "")).strip()
        if not api_id or not api_hash or "..." in api_hash:
            raise ValueError(f"{p}: api_id und api_hash eintragen")
        return int(api_id), api_hash


KEYS = {"risiko_je_trade_pct": "risk_pct", "max_tagesverlust_pct": "max_daily_loss_pct",
        "max_drawdown_pct": "max_drawdown_pct", "max_positionen": "max_positions",
        "max_signale_pro_tag": "max_signals_per_day", "min_crv": "min_rr", "einstieg_warten_min": "entry_wait_min",
        "toleranz_r": "tolerance_r", "ziel": "target", "teilgewinn_pct": "partial_pct", "folgenachrichten": "followups",
        "folge_gueltig_h": "followup_valid_h", "max_signal_alter_s": "max_signal_age_s",
        "abweichung_points": "deviation_points", "lot": "lot"}


def load(path: str | Path, data_root: str = "data") -> TelegramConfig:
    path = Path(path)
    d = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tg, sy, ha = d.get("telegram", {}), d.get("symbole", {}), d.get("handel", {})
    c = TelegramConfig(data_root=data_root, base_dir=path.resolve().parent)
    c.channel = str(tg.get("kanal", c.channel))
    c.credentials_file = str(tg.get("zugang", c.credentials_file))
    c.orders_enabled = bool(tg.get("orders", c.orders_enabled))
    c.account = str(tg.get("konto", c.account)).lower()
    c.live_account = int(tg.get("live_konto", 0) or 0)
    c.symbol_suffix = str(sy.get("suffix", ""))
    c.allowed = [str(s).upper() for s in sy.get("erlaubt", [])]
    c.symbol_map = {str(k).upper(): str(v) for k, v in (sy.get("namen") or {}).items()}
    for k, attr in KEYS.items():
        if k in ha:
            setattr(c, attr, type(getattr(c, attr))(ha[k]))
    unknown = set(ha) - set(KEYS)
    if unknown:
        raise ValueError(f"telegram.toml [handel]: unbekannte Einstellung(en) {', '.join(sorted(unknown))}")
    # harte Obergrenzen
    c.risk_pct = min(max(c.risk_pct, 0.0), CAP_RISK_PCT)
    c.max_daily_loss_pct = min(max(c.max_daily_loss_pct, 0.0), CAP_DAILY_LOSS_PCT)
    c.max_drawdown_pct = min(max(c.max_drawdown_pct, 0.0), CAP_DRAWDOWN_PCT)
    c.max_positions = min(max(c.max_positions, 0), CAP_POSITIONS)
    c.partial_pct = min(max(c.partial_pct, 0.0), 100.0)
    if c.account not in ("demo", "live"):
        raise ValueError("telegram.toml: konto muss \"demo\" oder \"live\" sein")
    if c.account == "live":
        if c.live_account <= 0:
            raise ValueError("telegram.toml: für konto = \"live\" die Live-Kontonummer bei live_konto eintragen "
                             "(steht in MT5 oben im Navigator bzw. unter Datei > Konto eröffnen/Login)")
        if not (0 < c.lot <= CAP_LIVE_LOT):
            raise ValueError(f"telegram.toml: auf dem Live-Konto nur mit fester Lotgröße lot zwischen 0,01 und "
                             f"{CAP_LIVE_LOT:g}")
    c.lot = max(c.lot, 0.0)
    if c.followups not in FOLLOWUP_MODES:
        raise ValueError(f"telegram.toml: folgenachrichten muss eins von {FOLLOWUP_MODES} sein")
    if c.target not in ("erstes", "letztes"):
        raise ValueError("telegram.toml: ziel muss \"erstes\" oder \"letztes\" sein")
    return c
