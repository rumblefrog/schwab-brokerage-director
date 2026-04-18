#!/usr/bin/env python3
"""Fetch Schwab holdings via SnapTrade, normalize, emit JSON.

Output shape (also written to /tmp/holdings.json):
    {
      "as_of": "...",
      "total_usd": 117256.00,
      "cash_usd": 1357.00,
      "positions": [
        {"ticker", "shares", "market_value", "cost_basis", "category"},
        ...
      ]
    }

Exit codes:
    0 — success
    1 — generic failure
    2 — SnapTrade auth failure (routine should post the red re-link alert)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "target_allocation.yaml"
FIXTURE_RAW = REPO_ROOT / "tests" / "fixtures" / "holdings_raw.json"
OUT_PATH = Path("/tmp/holdings.json")

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_AUTH = 2

REQUIRED_ENV_VARS = (
    "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY",
    "SNAPTRADE_USER_ID",
    "SNAPTRADE_USER_SECRET",
)


class AuthFailure(RuntimeError):
    """Raised when SnapTrade rejects credentials or env vars are missing."""


def load_category_map(path: Path = CONFIG_PATH) -> dict[str, str]:
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("category_map") or {}


def _as_dict(obj: Any) -> Any:
    """Coerce SDK response models into plain dicts/lists for uniform access."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "body"):
        return _as_dict(obj.body)
    return obj


def normalize(raw: Any, category_map: dict[str, str]) -> dict[str, Any]:
    """Collapse SnapTrade's account-holdings shape into our canonical output.

    Accepts either a single account dict or a list of account dicts (the
    `get_all_user_holdings` endpoint returns a list).
    """
    raw = _as_dict(raw)
    accounts = raw if isinstance(raw, list) else [raw]

    total_usd = 0.0
    cash_usd = 0.0
    positions: list[dict[str, Any]] = []
    as_of: str | None = None

    for acct in accounts:
        total_value = acct.get("total_value") or {}
        total_usd += float(total_value.get("amount") or total_value.get("value") or 0)

        for bal in acct.get("balances") or []:
            cash_usd += float(bal.get("cash") or 0)

        sync = ((acct.get("account") or {}).get("sync_status") or {}).get("holdings") or {}
        as_of = acct.get("as_of") or sync.get("last_successful_sync") or as_of

        for pos in acct.get("positions") or []:
            symbol = pos.get("symbol") or {}
            # Live SnapTrade nests the universal symbol: pos.symbol.symbol.symbol == "VTI".
            # The test fixture uses the simpler pos.symbol.symbol == "VTI". Handle both.
            if isinstance(symbol, dict):
                inner = symbol.get("symbol")
                if isinstance(inner, dict):
                    ticker = inner.get("symbol") or inner.get("raw_symbol")
                else:
                    ticker = inner
            else:
                ticker = str(symbol)
            if not ticker:
                continue
            units = float(pos.get("units") or 0)
            price = float(pos.get("price") or 0)
            avg_cost = float(pos.get("average_purchase_price") or 0)
            positions.append(
                {
                    "ticker": ticker,
                    "shares": round(units, 4),
                    "market_value": round(units * price, 2),
                    "cost_basis": round(units * avg_cost, 2),
                    "category": category_map.get(ticker, "uncategorized"),
                }
            )

    return {
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
        "total_usd": round(total_usd, 2),
        "cash_usd": round(cash_usd, 2),
        "positions": positions,
    }


def fetch_from_snaptrade() -> Any:
    """Call SnapTrade and return the raw response body (list of accounts)."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise AuthFailure(f"Missing SnapTrade env vars: {', '.join(missing)}")

    from snaptrade_client import SnapTrade  # lazy — tests don't need the SDK
    from snaptrade_client.configuration import Configuration

    cfg = Configuration(
        client_id=os.environ["SNAPTRADE_CLIENT_ID"],
        consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    )
    # Honor system CA bundle (the SDK ignores REQUESTS_CA_BUNDLE/SSL_CERT_FILE).
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca_bundle:
        cfg.ssl_ca_cert = ca_bundle
    client = SnapTrade(configuration=cfg)
    try:
        response = client.account_information.get_all_user_holdings(
            user_id=os.environ["SNAPTRADE_USER_ID"],
            user_secret=os.environ["SNAPTRADE_USER_SECRET"],
        )
    except Exception as exc:
        status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if status in (401, 403):
            raise AuthFailure(f"SnapTrade rejected credentials ({status}): {exc}") from exc
        raise
    return _as_dict(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read tests/fixtures/holdings_raw.json instead of hitting SnapTrade.",
    )
    args = parser.parse_args(argv)

    try:
        category_map = load_category_map()
        if args.dry_run:
            with FIXTURE_RAW.open() as f:
                raw = json.load(f)
        else:
            raw = fetch_from_snaptrade()
        result = normalize(raw, category_map)
    except AuthFailure as exc:
        print(f"SnapTrade auth failure: {exc}", file=sys.stderr)
        return EXIT_AUTH
    except Exception as exc:
        print(f"fetch_holdings failed: {exc}", file=sys.stderr)
        return EXIT_GENERIC

    payload = json.dumps(result, indent=2)
    OUT_PATH.write_text(payload)
    print(payload)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
