#!/usr/bin/env python3
"""Fetch market context via yfinance and emit JSON.

Output shape (also written to /tmp/market_context.json):
    {
      "as_of": "...",
      "tickers": {TICKER: {"price", "return_1w", ..., "return_1y"}, ...},
      "indices": {"VIX": {"level", "change_1w"},
                  "TNX_10y_pct": {"level", "change_1w"},
                  "SPY"|"QQQ"|"EFA"|"GLD"|"UUP": {"return_1w", "return_1m"}},
      "sectors": {SPDR_TICKER: {"return_1w", "return_1m"}, ...},
      "warnings": [...]
    }

Per-indicator retry with exponential backoff; indicators that fail all
retries are recorded in `warnings` and omitted from the output — the
routine is expected to carry on and note the gap in the recommendation's
## Caveats section.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "target_allocation.yaml"
FIXTURE_OUT = REPO_ROOT / "tests" / "fixtures" / "market_context.json"
OUT_PATH = Path("/tmp/market_context.json")

# Trading-day lookbacks. YTD handled separately.
LOOKBACKS = {
    "return_1w": 5,
    "return_1m": 21,
    "return_3m": 63,
    "return_1y": 252,
}

LEVEL_INDICES = [
    # (key, yfinance-ticker) — reported as {"level": latest, "change_1w": level_delta}
    ("VIX", "^VIX"),
    ("TNX_10y_pct", "^TNX"),
]

RETURN_INDICES = [
    # (key, yfinance-ticker) — reported as {"return_1w", "return_1m"}
    ("SPY", "SPY"),
    ("QQQ", "QQQ"),
    ("EFA", "EFA"),
    ("GLD", "GLD"),
    ("UUP", "UUP"),
]

SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC"]


def load_held_tickers(path: Path = CONFIG_PATH) -> list[str]:
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    return list((cfg.get("category_map") or {}).keys())


def _fetch_history_yf(ticker: str):
    """Thin wrapper over yfinance.Ticker(...).history — imported lazily for tests."""
    import yfinance  # lazy
    df = yfinance.Ticker(ticker).history(period="1y", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"empty history for {ticker}")
    return df


def fetch_with_retry(
    ticker: str,
    *,
    fetcher: Callable[[str], Any] = _fetch_history_yf,
    retries: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
):
    """Call fetcher(ticker), retrying with exponential backoff."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fetcher(ticker)
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                sleep(backoff_seconds * (2 ** attempt))
    assert last is not None
    raise last


def _close_ago(closes, days_back: int) -> float | None:
    """Return the close `days_back` rows before the last row, or None if out of range."""
    idx = -1 - days_back
    if abs(idx) > len(closes):
        return None
    val = closes.iloc[idx]
    if val is None or float(val) == 0:
        return None
    return float(val)


def compute_returns(history) -> dict[str, float | None]:
    """Compute 1w/1m/3m/YTD/1y returns from a yfinance-style DataFrame."""
    closes = history["Close"]
    latest = float(closes.iloc[-1])
    returns: dict[str, float | None] = {}
    for key, lookback in LOOKBACKS.items():
        old = _close_ago(closes, lookback)
        returns[key] = (latest - old) / old if old else None

    # YTD: closing price on or before Jan 1 of the current year
    latest_date = closes.index[-1]
    year_start = latest_date.replace(month=1, day=1)
    year_mask = closes.index < year_start
    ytd_prices = closes[year_mask]
    if len(ytd_prices) > 0:
        base = float(ytd_prices.iloc[-1])
        returns["return_ytd"] = (latest - base) / base if base else None
    else:
        returns["return_ytd"] = None

    return returns


def _safe_fetch(
    ticker: str,
    warnings: list[str],
    label: str | None = None,
    **retry_kwargs,
):
    """Fetch with retry; on total failure append to warnings and return None."""
    tag = label or ticker
    try:
        return fetch_with_retry(ticker, **retry_kwargs)
    except Exception as exc:
        warnings.append(f"{tag}: {exc}")
        return None


def build_context(
    tickers: list[str],
    *,
    fetcher: Callable[[str], Any] = _fetch_history_yf,
    retries: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    retry_kwargs = dict(fetcher=fetcher, retries=retries, backoff_seconds=backoff_seconds, sleep=sleep)
    warnings: list[str] = []
    out_tickers: dict[str, Any] = {}
    out_indices: dict[str, Any] = {}
    out_sectors: dict[str, Any] = {}

    for t in tickers:
        df = _safe_fetch(t, warnings, **retry_kwargs)
        if df is None:
            continue
        price = float(df["Close"].iloc[-1])
        out_tickers[t] = {"price": price, **compute_returns(df)}

    for key, yf_ticker in LEVEL_INDICES:
        df = _safe_fetch(yf_ticker, warnings, label=key, **retry_kwargs)
        if df is None:
            continue
        closes = df["Close"]
        level = float(closes.iloc[-1])
        prior = _close_ago(closes, 5)
        out_indices[key] = {
            "level": round(level, 2),
            "change_1w": round(level - prior, 2) if prior is not None else None,
        }

    for key, yf_ticker in RETURN_INDICES:
        df = _safe_fetch(yf_ticker, warnings, label=key, **retry_kwargs)
        if df is None:
            continue
        r = compute_returns(df)
        out_indices[key] = {"return_1w": r["return_1w"], "return_1m": r["return_1m"]}

    for s in SECTOR_ETFS:
        df = _safe_fetch(s, warnings, **retry_kwargs)
        if df is None:
            continue
        r = compute_returns(df)
        out_sectors[s] = {"return_1w": r["return_1w"], "return_1m": r["return_1m"]}

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "tickers": out_tickers,
        "indices": out_indices,
        "sectors": out_sectors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass through tests/fixtures/market_context.json instead of hitting yfinance.",
    )
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            with FIXTURE_OUT.open() as f:
                result = json.load(f)
        else:
            result = build_context(load_held_tickers())
    except Exception as exc:
        print(f"market_context failed: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(result, indent=2)
    OUT_PATH.write_text(payload)
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
