"""Strategie-Finder – rechnet sehr viele Strategien für Gold (XAUUSD) mit der Zeitmaschine durch und sucht je Setup
die beste heraus. NUR RECHNEN: es werden keine Orders gesendet, auch nicht auf dem Demokonto.

Was durchgerechnet wird: Trend (EMA, MACD, Supertrend, SMA-Box, VWAP, Pullback), Breakout (Donchian/Turtle,
Bollinger-Squeeze, Vortageshoch, Inside Bar, Momentum, Opening Range inkl. Asien-Range), Mean Reversion (RSI,
Bollinger), Order Blocks/Smart Money (Order Block, FVG, Silver Bullet, Sweep, MSS, Gold-Bot) – jeweils mit mehreren
Parametern, auf M5, M15, M30, H1, H4, zu vier Handelszeiten und in drei Richtungen.

Damit die "beste" Strategie nicht nur Kurvenanpassung ist, wählt der Finder per Walk-Forward: jedes Quartal neu und
nur mit den Daten davor. Bewertet wird, was danach passiert ist (Out-of-Sample) – mit harten Kosten gegengeprüft,
gegen "Gold einfach halten" verglichen und mit der Deflated Sharpe Ratio auf Glück geprüft.

Aufruf (oder einfach STRATEGIEFINDER.bat doppelklicken):
    python strategiefinder.py                          # alles (dauert je nach PC 2–10 Minuten)
    python strategiefinder.py --schnell                # ohne Gold-Bot (der braucht allein einige Minuten)
    python strategiefinder.py --zeitrahmen M15 H1      # nur diese Zeitrahmen
    python strategiefinder.py --sitzung london         # nur ein Setup-Zeitfenster
    python strategiefinder.py --demo-eintragen         # Sieger in config.toml eintragen – NUR wenn Ampel grün
    Optionen: --konto 357  --laeufe 10000  --kerne 4  --mt5  --kein-browser
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tradingbot.config import load_config  # noqa: E402
from tradingbot.strategiefinder import bericht  # noqa: E402
from tradingbot.strategiefinder.auswahl import Regeln  # noqa: E402
from tradingbot.strategiefinder.kandidaten import GOLDBOT, GRIDS, ZEITRAHMEN  # noqa: E402
from tradingbot.strategiefinder.lauf import run  # noqa: E402
from tradingbot.strategies.zusatz import SITZUNGEN  # noqa: E402
from tradingbot.zeitmaschine.daten import DataSource  # noqa: E402

MARKE = "# --- Strategie-Finder"


def log(*a, **k):
    print(*a, **k, flush=True)


def demo_eintragen(config_path: Path, eintrag: dict) -> str:
    """Sieger als [[paper.candidates]] in config.toml (vor [execution]) eintragen. Ersetzt einen früheren
    Finder-Eintrag. Der Demo-Bot handelt Kandidaten nur auf einem Demokonto (fest im Code)."""
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    # alten Finder-Block entfernen (von der Marke bis zur nächsten Leerzeile nach dem Block)
    out, skip = [], False
    for ln in lines:
        if ln.startswith(MARKE):
            skip = True
            continue
        if skip:
            if ln.strip() == "" or ln.startswith("[execution]"):
                skip = False
                if ln.strip() == "":
                    continue
            else:
                continue
        out.append(ln)
    text = "".join(out)
    block = (f"{MARKE} (automatisch, nur Demokonto; Ampel grün im Walk-Forward)\n"
             + bericht.toml_eintrag(eintrag) + "\n")
    i = text.find("\n[execution]")
    text = text[: i + 1] + block + text[i + 1:] if i >= 0 else text + "\n" + block
    config_path.write_text(text, encoding="utf-8")
    return block


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Strategie-Finder für XAUUSD – nur Rechnung, keine Orders")
    ap.add_argument("--zeitrahmen", nargs="+", choices=ZEITRAHMEN, default=list(ZEITRAHMEN))
    ap.add_argument("--sitzung", nargs="+", choices=list(SITZUNGEN), help="Handelszeiten (Standard: alle vier)")
    ap.add_argument("--strategie", nargs="+", choices=sorted(GRIDS) + [GOLDBOT], help="nur diese Strategien")
    ap.add_argument("--schnell", action="store_true", help="ohne Gold-Bot (spart einige Minuten)")
    ap.add_argument("--konto", type=float, default=357.0, help="Startkonto in USD für die Zeitmaschine (Standard 357)")
    ap.add_argument("--laeufe", type=int, default=10_000, help="Monte-Carlo-Durchläufe für den Sieger")
    ap.add_argument("--kerne", type=int, help="Prozessorkerne (Standard: alle bis auf einen)")
    ap.add_argument("--demo-eintragen", action="store_true",
                    help="Sieger als Demo-Kandidat in config.toml eintragen – nur wenn er alle Prüfungen besteht")
    ap.add_argument("--mt5", action="store_true", help="frische Kerzen aus dem offenen MT5 dazuholen (nur lesen)")
    ap.add_argument("--kein-browser", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--goldbot-config", default=str(ROOT / "goldbot.toml"))
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    print("STRATEGIE-FINDER – nur Rechnung auf vergangenen Kursen, es wird NICHTS gehandelt.\n")
    if client is None and a.mt5:
        try:
            from tradingbot.mt5_client import MT5Client
            client = MT5Client(terminal_path=cfg.terminal_path)
        except Exception:                                           # noqa: BLE001
            client = None
    src = DataSource(cfg.data_root, client=client, use_mt5=a.mt5, log=log)
    gcfg = None
    if not a.schnell:
        from tradingbot.goldbot import config as gconf
        gcfg = gconf.load(a.goldbot_config, str(cfg.data_root))
    try:
        o = run(src, zeitrahmen=tuple(a.zeitrahmen), sitzungen=a.sitzung, strategien=a.strategie,
                mit_goldbot=not a.schnell, konto=a.konto, gcfg=gcfg, regeln=Regeln(), laeufe=a.laeufe,
                kerne=a.kerne, log=log)
    finally:
        if client is not None:
            try:
                client.shutdown()
            except Exception:                                       # noqa: BLE001
                pass
    print()
    print(bericht.text(o))
    p_html, _ = bericht.save(o, Path(cfg.report_dir) / "strategiefinder")
    print(f"\nBericht: {p_html}")
    if a.demo_eintragen:
        if o.get("demo_kandidat"):
            block = demo_eintragen(Path(a.config), o["demo_kandidat"])
            print("\nIn config.toml eingetragen (Demo-Bot neu starten, damit er ihn übernimmt):\n" + block)
        else:
            print("\nNichts in config.toml eingetragen: kein Setup hat alle Prüfungen bestanden.")
    if not a.kein_browser:
        webbrowser.open(p_html.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
