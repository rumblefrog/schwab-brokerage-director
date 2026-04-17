"""Unit tests for scripts/fetch_holdings.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import fetch_holdings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def category_map() -> dict[str, str]:
    return fetch_holdings.load_category_map()


@pytest.fixture
def raw_holdings() -> dict:
    with (FIXTURES / "holdings_raw.json").open() as f:
        return json.load(f)


def test_normalize_totals_match_fixture(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    assert result["total_usd"] == 117256.00
    assert result["cash_usd"] == 1357.00
    assert result["as_of"] == "2026-04-20T22:00:00Z"


def test_normalize_produces_all_positions(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    tickers = {p["ticker"] for p in result["positions"]}
    assert tickers == {"VTI", "SWPPX", "QQQM", "VXUS", "SGOV", "GDX", "WPM", "GLDM", "SIL"}


def test_normalize_swppx_categorized_as_us_equity_broad(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    swppx = next(p for p in result["positions"] if p["ticker"] == "SWPPX")
    assert swppx["category"] == "us_equity_broad"


def test_normalize_wpm_categorized_as_precious_metals(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    wpm = next(p for p in result["positions"] if p["ticker"] == "WPM")
    assert wpm["category"] == "precious_metals"


def test_normalize_market_value_from_units_times_price(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    vti = next(p for p in result["positions"] if p["ticker"] == "VTI")
    # 141.56 * 350.37 = 49598.3772; rounded to 2dp = 49598.38
    assert vti["market_value"] == pytest.approx(141.56 * 350.37, rel=1e-6)


def test_normalize_cost_basis_from_units_times_avg_cost(raw_holdings, category_map):
    result = fetch_holdings.normalize(raw_holdings, category_map)
    vti = next(p for p in result["positions"] if p["ticker"] == "VTI")
    assert vti["cost_basis"] == pytest.approx(141.56 * 269.95, rel=1e-6)


def test_normalize_unknown_ticker_marked_uncategorized():
    raw = {
        "total_value": {"amount": 1000},
        "balances": [{"cash": 0}],
        "positions": [
            {"symbol": {"symbol": "NVDA"}, "units": 5, "price": 200, "average_purchase_price": 180}
        ],
        "as_of": "2026-04-20T22:00:00Z",
    }
    result = fetch_holdings.normalize(raw, {"VTI": "us_equity_broad"})
    assert result["positions"][0]["category"] == "uncategorized"


def test_normalize_accepts_list_of_accounts(raw_holdings, category_map):
    # get_all_user_holdings returns a list; normalize must aggregate across them.
    result = fetch_holdings.normalize([raw_holdings, raw_holdings], category_map)
    assert result["total_usd"] == 2 * 117256.00
    assert result["cash_usd"] == 2 * 1357.00
    assert len(result["positions"]) == 2 * 9


def test_dry_run_exits_zero_and_writes_tmp(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch_holdings, "OUT_PATH", tmp_path / "holdings.json")
    rc = fetch_holdings.main(["--dry-run"])
    assert rc == fetch_holdings.EXIT_OK
    assert (tmp_path / "holdings.json").exists()
    stdout = capsys.readouterr().out
    parsed = json.loads(stdout)
    assert parsed["total_usd"] == 117256.00


def test_missing_env_vars_exits_auth_code(monkeypatch, capsys):
    for var in fetch_holdings.REQUIRED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    rc = fetch_holdings.main([])
    assert rc == fetch_holdings.EXIT_AUTH
    assert "auth failure" in capsys.readouterr().err.lower()


def test_snaptrade_401_exits_auth_code(monkeypatch, capsys):
    for var in fetch_holdings.REQUIRED_ENV_VARS:
        monkeypatch.setenv(var, "placeholder")

    # Build a fake snaptrade_client module that raises a 401-ish exception.
    class FakeApiException(Exception):
        def __init__(self, status: int) -> None:
            super().__init__(f"HTTP {status}")
            self.status = status

    fake_sdk = MagicMock()
    fake_client = MagicMock()
    fake_client.account_information.get_all_user_holdings.side_effect = FakeApiException(401)
    fake_sdk.SnapTrade.return_value = fake_client
    monkeypatch.setitem(sys.modules, "snaptrade_client", fake_sdk)

    rc = fetch_holdings.main([])
    assert rc == fetch_holdings.EXIT_AUTH
    assert "auth failure" in capsys.readouterr().err.lower()


def test_snaptrade_500_exits_generic_code(monkeypatch, capsys):
    for var in fetch_holdings.REQUIRED_ENV_VARS:
        monkeypatch.setenv(var, "placeholder")

    class FakeApiException(Exception):
        def __init__(self, status: int) -> None:
            super().__init__(f"HTTP {status}")
            self.status = status

    fake_sdk = MagicMock()
    fake_client = MagicMock()
    fake_client.account_information.get_all_user_holdings.side_effect = FakeApiException(500)
    fake_sdk.SnapTrade.return_value = fake_client
    monkeypatch.setitem(sys.modules, "snaptrade_client", fake_sdk)

    rc = fetch_holdings.main([])
    assert rc == fetch_holdings.EXIT_GENERIC


def test_live_fetch_normalizes_via_sdk(monkeypatch, tmp_path, capsys, raw_holdings):
    for var in fetch_holdings.REQUIRED_ENV_VARS:
        monkeypatch.setenv(var, "placeholder")

    monkeypatch.setattr(fetch_holdings, "OUT_PATH", tmp_path / "holdings.json")

    # SDK returns a response object with a .body containing the list-of-accounts.
    response = MagicMock()
    response.body = [raw_holdings]
    response.to_dict = lambda: [raw_holdings]

    fake_sdk = MagicMock()
    fake_client = MagicMock()
    fake_client.account_information.get_all_user_holdings.return_value = response
    fake_sdk.SnapTrade.return_value = fake_client
    monkeypatch.setitem(sys.modules, "snaptrade_client", fake_sdk)

    rc = fetch_holdings.main([])
    assert rc == fetch_holdings.EXIT_OK
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["total_usd"] == 117256.00
    assert any(p["ticker"] == "VTI" for p in parsed["positions"])
