"""Unit tests for scripts/market_context.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from scripts import market_context

FIXTURES = Path(__file__).parent / "fixtures"


def _synthetic_history(latest_price: float = 100.0, days: int = 300) -> pd.DataFrame:
    """Return a pd.DataFrame shaped like yfinance.Ticker(...).history()."""
    end = datetime(2026, 4, 20)
    dates = pd.date_range(end=end, periods=days, freq="B")  # business days
    # Linear ramp so lookback returns are stable and easy to reason about.
    closes = [latest_price * (1 - 0.001 * (days - 1 - i)) for i in range(days)]
    return pd.DataFrame({"Close": closes}, index=dates)


def test_compute_returns_1w():
    df = _synthetic_history(latest_price=100.0, days=300)
    r = market_context.compute_returns(df)
    assert r["return_1w"] is not None
    # Five business days back = 0.5% lower; return ~= +0.00503
    assert r["return_1w"] == pytest.approx(0.005025, rel=1e-3)


def test_compute_returns_handles_short_history():
    df = _synthetic_history(latest_price=100.0, days=10)
    r = market_context.compute_returns(df)
    # 1y lookback unavailable with only 10 rows.
    assert r["return_1y"] is None
    assert r["return_1w"] is not None


def test_compute_returns_ytd_uses_year_boundary():
    df = _synthetic_history(latest_price=100.0, days=300)
    r = market_context.compute_returns(df)
    assert r["return_ytd"] is not None


def test_fetch_with_retry_retries_on_failure():
    calls = {"n": 0}

    def flaky(ticker: str):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return _synthetic_history()

    result = market_context.fetch_with_retry(
        "VTI", fetcher=flaky, retries=3, backoff_seconds=0, sleep=lambda _s: None
    )
    assert calls["n"] == 3
    assert not result.empty


def test_fetch_with_retry_raises_after_exhausting():
    def always_fail(ticker: str):
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        market_context.fetch_with_retry(
            "VTI", fetcher=always_fail, retries=3, backoff_seconds=0, sleep=lambda _s: None
        )


def test_build_context_fills_tickers_indices_sectors():
    def fake(ticker: str):
        return _synthetic_history(latest_price=100.0)

    ctx = market_context.build_context(
        ["VTI", "VXUS"], fetcher=fake, backoff_seconds=0, sleep=lambda _s: None
    )
    assert set(ctx["tickers"]) == {"VTI", "VXUS"}
    assert set(ctx["indices"]) == {"VIX", "TNX_10y_pct", "SPY", "QQQ", "EFA", "GLD", "UUP"}
    assert set(ctx["sectors"]) == set(market_context.SECTOR_ETFS)
    assert ctx["warnings"] == []


def test_build_context_records_warning_when_one_indicator_fails_completely():
    def fetcher(ticker: str):
        if ticker == "^VIX":
            raise RuntimeError("yahoo 500")
        return _synthetic_history()

    ctx = market_context.build_context(
        ["VTI"], fetcher=fetcher, retries=2, backoff_seconds=0, sleep=lambda _s: None
    )
    assert "VIX" not in ctx["indices"]
    assert any("VIX" in w for w in ctx["warnings"])
    # Other indicators still present
    assert "TNX_10y_pct" in ctx["indices"]
    assert "SPY" in ctx["indices"]


def test_build_context_warns_but_continues_on_ticker_failure():
    def fetcher(ticker: str):
        if ticker == "QQQM":
            raise RuntimeError("delisted")
        return _synthetic_history()

    ctx = market_context.build_context(
        ["VTI", "QQQM", "VXUS"], fetcher=fetcher, retries=1, backoff_seconds=0, sleep=lambda _s: None
    )
    assert "VTI" in ctx["tickers"]
    assert "VXUS" in ctx["tickers"]
    assert "QQQM" not in ctx["tickers"]
    assert any("QQQM" in w for w in ctx["warnings"])


def test_level_indices_report_level_and_weekly_delta():
    def fetcher(ticker: str):
        # Make VIX history with latest=17.2, five days back=16.1 → change_1w=1.10
        if ticker == "^VIX":
            dates = pd.date_range(end=datetime(2026, 4, 20), periods=10, freq="B")
            closes = [16.0, 16.05, 16.1, 16.1, 16.1, 16.15, 16.5, 16.8, 17.0, 17.2]
            return pd.DataFrame({"Close": closes}, index=dates)
        return _synthetic_history()

    ctx = market_context.build_context(
        [], fetcher=fetcher, retries=1, backoff_seconds=0, sleep=lambda _s: None
    )
    vix = ctx["indices"]["VIX"]
    assert vix["level"] == pytest.approx(17.2)
    # latest (17.2) - five-rows-ago (16.1) = 1.10
    assert vix["change_1w"] == pytest.approx(1.10, abs=0.01)


def test_dry_run_passes_through_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(market_context, "OUT_PATH", tmp_path / "market_context.json")
    rc = market_context.main(["--dry-run"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    # Fixture should carry through verbatim.
    assert parsed["tickers"]["VTI"]["price"] == 350.37
    assert parsed["indices"]["VIX"]["level"] == 17.2
    assert (tmp_path / "market_context.json").exists()


def test_load_held_tickers_returns_category_map_keys():
    tickers = market_context.load_held_tickers()
    assert set(tickers) == {"VTI", "SWPPX", "QQQM", "VXUS", "GDX", "WPM", "GLDM", "SIL", "SGOV"}
