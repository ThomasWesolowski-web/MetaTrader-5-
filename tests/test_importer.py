import numpy as np
import pandas as pd
import pytest

from tradingbot.importer import Importer
from tradingbot.server_time import parse_scheme
from tradingbot.storage import MarketStore

TF_S = {"M1": 60, "M5": 300, "H1": 3600, "D1": 86400}


def importer(fake, client, store, scheme=None, chunk_bars=3000, **kw):
    return Importer(client, store, scheme or parse_scheme(fake.scheme), chunk_bars=chunk_bars,
                    earliest_utc="2020-01-01", now_utc_fn=lambda: fake.now_utc, log=lambda *a: None, **kw)


def expected(fake, sym, tf):
    """Was MT5 liefert, ohne die laufende Kerze."""
    arr = fake._visible(sym, tf)
    return arr[arr["time"] + TF_S[tf] <= fake.now_server()]


@pytest.mark.parametrize("tf", ["M1", "M5", "H1", "D1"])
def test_full_import_matches_mt5_exactly(make_fake, client_for, tmp_path, tf):
    fake = make_fake(available_bars={"M1": 20000})
    store = MarketStore(tmp_path)
    res = importer(fake, client_for(fake), store).update("BTCUSD", tf)
    df = store.load("BTCUSD", tf)
    exp = expected(fake, "BTCUSD", tf)
    assert np.array_equal(df["time_server"].to_numpy(), exp["time"])      # nichts erfunden, nichts fehlt
    assert np.allclose(df["close"].to_numpy(), exp["close"])
    assert res.history_exhausted and not res.errors
    assert res.rows_total == len(exp)


def test_chunks_respect_size(make_fake, client_for, tmp_path):
    fake = make_fake(available_bars={"M1": 20000})
    res = importer(fake, client_for(fake), MarketStore(tmp_path), chunk_bars=3000).update("BTCUSD", "M1")
    ranges = [c for c in fake.calls if c[0] == "range" and c[2] == "M1"]
    assert len(ranges) >= 7
    assert all((to - frm) / 60 <= 3000 for _, _, _, frm, to in ranges)
    # leere Blöcke (Ende der Historie) werden einmal wiederholt, weil das Terminal ältere Daten nachlädt
    assert res.requests <= len(ranges) <= res.requests + 3


def test_rejected_large_requests_are_split(make_fake, client_for, tmp_path):
    fake = make_fake(available_bars={"M1": 20000}, max_request_bars=2500)
    store = MarketStore(tmp_path)
    res = importer(fake, client_for(fake), store, chunk_bars=10000).update("BTCUSD", "M1")
    assert res.split_requests > 0
    assert len(store.load("BTCUSD", "M1")) == len(expected(fake, "BTCUSD", "M1"))


def test_utc_times_are_true_utc(make_fake, client_for, tmp_path):
    for scheme in ("ny_close", "eu_dst"):
        fake = make_fake(scheme=scheme, days=60, available_bars={"M1": 30000})
        store = MarketStore(tmp_path / scheme)
        importer(fake, client_for(fake), store).update("XAUUSD", "M1")
        df = store.load("XAUUSD", "M1")
        true_utc = fake.utc_m1["XAUUSD"]
        assert pd.DatetimeIndex(df["time"]).isin(true_utc).all(), scheme


def test_incremental_update_loads_only_missing(make_fake, client_for, tmp_path):
    fake = make_fake(available_bars={"M1": 10000})
    store = MarketStore(tmp_path)
    imp = importer(fake, client_for(fake), store)
    first = imp.update("BTCUSD", "M1")
    assert first.new_rows > 0

    fake.calls.clear()
    again = imp.update("BTCUSD", "M1")
    assert again.new_rows == 0
    assert again.requests <= 3            # 1x vorwärts, 1x Prüfung auf ältere Historie (+ ggf. Rand)

    before = store.bounds("BTCUSD", "M1")
    fake.now_utc += pd.Timedelta(hours=6)
    fake.calls.clear()
    upd = imp.update("BTCUSD", "M1")
    exp_new = expected(fake, "BTCUSD", "M1")
    exp_new = exp_new[exp_new["time"] > before["last_server"]]
    assert upd.forward_rows == len(exp_new)
    fwd = [c for c in fake.calls if c[0] == "range"][0]
    assert fwd[3] >= before["last_server"]  # beginnt bei der letzten gespeicherten Kerze


def test_running_bar_is_never_stored(make_fake, client_for, tmp_path):
    fake = make_fake()
    fake.now_utc = pd.Timestamp("2026-04-15 12:30", tz="UTC")  # Mittwoch, H1-Kerze 12:00 läuft noch
    store = MarketStore(tmp_path)
    importer(fake, client_for(fake), store).update("BTCUSD", "H1")
    last = store.load("BTCUSD", "H1")["time"].iloc[-1]
    assert last == pd.Timestamp("2026-04-15 11:00", tz="UTC")


def test_backfill_extends_when_more_history_becomes_available(make_fake, client_for, tmp_path):
    fake = make_fake(available_bars={"M5": 2000})
    store = MarketStore(tmp_path)
    imp = importer(fake, client_for(fake), store)
    r1 = imp.update("XAUUSD", "M5")
    assert r1.history_exhausted
    fake.available_bars["M5"] = 6000       # z. B. "Max. Balken im Chart" erhöht
    r2 = imp.update("XAUUSD", "M5")
    assert r2.backfill_rows > 0
    assert len(store.load("XAUUSD", "M5")) == len(expected(fake, "XAUUSD", "M5"))


def test_resume_after_interruption(make_fake, client_for, tmp_path):
    fake = make_fake(available_bars={"M1": 15000})
    store = MarketStore(tmp_path)

    class Broken:
        def __init__(self, inner, fail_after):
            self.inner, self.n, self.fail_after = inner, 0, fail_after

        def __getattr__(self, k):
            return getattr(self.inner, k)

        def rates_range(self, *a, **kw):
            self.n += 1
            if self.n > self.fail_after:
                raise ConnectionError("Verbindung verloren")
            return self.inner.rates_range(*a, **kw)

    with pytest.raises(ConnectionError):
        importer(fake, Broken(client_for(fake), 2), store).update("BTCUSD", "M1")
    partial = len(store.load("BTCUSD", "M1"))
    assert 0 < partial < len(expected(fake, "BTCUSD", "M1"))
    importer(fake, client_for(fake), store).update("BTCUSD", "M1")
    df = store.load("BTCUSD", "M1")
    assert np.array_equal(df["time_server"].to_numpy(), expected(fake, "BTCUSD", "M1")["time"])


def test_scheme_change_recomputes_utc(make_fake, client_for, tmp_path):
    fake = make_fake()
    store = MarketStore(tmp_path)
    importer(fake, client_for(fake), store, scheme=parse_scheme("fixed:+00:00")).update("BTCUSD", "H1")
    wrong = store.load("BTCUSD", "H1")["time"].iloc[-1]
    imp = importer(fake, client_for(fake), store)
    assert imp.ensure_scheme("BTCUSD", ["H1"])
    right = store.load("BTCUSD", "H1")["time"].iloc[-1]
    assert wrong - right == pd.Timedelta(hours=3)
    assert store.read_meta("BTCUSD")["time_scheme"] == "ny_close"
