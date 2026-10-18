import json

import daten_aktualisieren


def write_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[data]\nroot = "data"\nreport_dir = "reports"\nchunk_bars = 5000\n'
                 'timeframes = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]\n', encoding="utf-8")
    return p


def test_end_to_end(make_fake, client_for, tmp_path, capsys):
    fake = make_fake(available_bars={"M1": 20000})
    cfgp = write_config(tmp_path)
    rc = daten_aktualisieren.main(["--config", str(cfgp)], client=client_for(fake), now_utc_fn=lambda: fake.now_utc)
    assert rc == 0
    specs = json.loads((tmp_path / "data" / "specs.json").read_text(encoding="utf-8"))
    assert specs["time_scheme"]["scheme"] == "ny_close"
    assert [c["symbol"] for c in specs["conversions"]] == ["EURUSD"]
    assert set(specs["instruments"]) == {"XAUUSD", "BTCUSD"}
    for sym in ("XAUUSD", "BTCUSD", "EURUSD"):
        for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
            assert list((tmp_path / "data" / "market" / sym / tf).glob("*.parquet")), (sym, tf)
    reports = list((tmp_path / "reports").glob("phase2_datenbericht_*.md"))
    assert reports and "Datenbestand und Qualität" in reports[0].read_text(encoding="utf-8")

    # Zweiter Lauf: nur noch fehlende Daten
    fake.calls.clear()
    daten_aktualisieren.main(["--config", str(cfgp)], client=client_for(fake), now_utc_fn=lambda: fake.now_utc)
    out = capsys.readouterr().out
    assert "+       0 neu" in out
    assert "+       1 neu" not in out

    # Prüfung ohne MT5-Verbindung
    assert daten_aktualisieren.main(["--config", str(cfgp), "--nur-pruefung"]) == 0
