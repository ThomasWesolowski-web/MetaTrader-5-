import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_market import FakeMT5  # noqa: E402
from tradingbot.config import Config  # noqa: E402
from tradingbot.mt5_client import MT5Client  # noqa: E402


@pytest.fixture(scope="session")
def fake_ny():
    """Unveränderlicher Markt mit Server-Schema ny_close (150 Tage, inkl. Sommerzeitwechsel)."""
    return FakeMT5("ny_close")


@pytest.fixture
def make_fake():
    def _make(**kw):
        kw.setdefault("days", 40)
        return FakeMT5(**kw)
    return _make


@pytest.fixture
def client_for():
    def _c(fake):
        return MT5Client(fake, retry_sleep=0)
    return _c


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.data_root = tmp_path / "data"
    c.report_dir = tmp_path / "reports"
    return c
