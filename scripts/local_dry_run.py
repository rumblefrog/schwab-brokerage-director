#!/usr/bin/env python3
"""Simulate one full Monday routine run from fixtures — no live APIs.

Exercises the full pipeline offline so you can iterate on CLAUDE.md
phrasing without burning routine quota:

    1. fetch_holdings.py --dry-run      → /tmp/holdings.json
    2. market_context.py --dry-run      → /tmp/market_context.json
    3. render a stub recommendation     → recommendations/DRY-RUN.md
    4. post_discord.py (mock webhook)   → stdout dump of the embed

Step 3 uses a deterministic mechanical rule (pick the most-underweight
category) so the output is reproducible. In a real routine run, step 3
is where Claude's reasoning happens — here we produce a plausible
placeholder that exercises the rest of the pipeline.

Run with:
    uv run python scripts/local_dry_run.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import post_discord  # noqa: E402

HOLDINGS_TMP = Path("/tmp/holdings.json")
MARKET_TMP = Path("/tmp/market_context.json")
CONFIG_PATH = REPO_ROOT / "config" / "target_allocation.yaml"
RECS_DIR = REPO_ROOT / "recommendations"
OUT_PATH = RECS_DIR / "DRY-RUN.md"


def _run_dry(script: str) -> None:
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script), "--dry-run"],
        check=True,
        cwd=REPO_ROOT,
    )


def _compute_drift(
    holdings: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return list of {category, current_usd, current_pct, target_pct, drift_usd}."""
    total = holdings["total_usd"]
    by_category: dict[str, float] = {}
    for p in holdings["positions"]:
        cat = p["category"]
        by_category[cat] = by_category.get(cat, 0.0) + p["market_value"]

    rows: list[dict[str, Any]] = []
    for cat, cfg in config["categories"].items():
        current = by_category.get(cat, 0.0)
        target_pct = float(cfg["target_pct"])
        drift_usd = (target_pct / 100.0 * total) - current
        rows.append(
            {
                "category": cat,
                "current_usd": round(current, 2),
                "current_pct": round(100 * current / total, 1) if total else 0.0,
                "target_pct": target_pct,
                "drift_usd": round(drift_usd, 2),
                "primary_ticker": cfg["primary_ticker"],
            }
        )
    return rows


def _pick_underweight(
    rows: list[dict[str, Any]],
    tolerance_pct: float,
    total_usd: float,
) -> dict[str, Any]:
    """Mechanical pick: largest positive drift_usd; fallback us_equity_broad."""
    tolerance_dollars = tolerance_pct / 100.0 * total_usd
    candidates = [r for r in rows if r["drift_usd"] > tolerance_dollars]
    if not candidates:
        return next(r for r in rows if r["category"] == "us_equity_broad")
    return max(candidates, key=lambda r: r["drift_usd"])


def _format_money(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.0f}"


def _render(
    holdings: dict[str, Any],
    market: dict[str, Any],
    drift_rows: list[dict[str, Any]],
    chosen: dict[str, Any],
    today: str,
    amount_usd: float = 500.00,
) -> str:
    ticker = chosen["primary_ticker"]
    price = market["tickers"].get(ticker, {}).get("price", 0.0)
    shares = amount_usd / price if price else 0.0

    frontmatter = (
        f"---\n"
        f"kind: buy\n"
        f"date: {today}\n"
        f"category: {chosen['category']}\n"
        f"ticker: {ticker}\n"
        f"amount_usd: {amount_usd:.2f}\n"
        f"share_estimate: {shares:.2f}\n"
        f"price_used: {price:.2f}\n"
        f"---\n\n"
    )

    headline = f"# This week: ${amount_usd:.0f} → {ticker}\n\n"

    reasoning = (
        f"## Reasoning\n\n"
        f"**[DRY-RUN STUB]** This is a mechanical placeholder produced by "
        f"local_dry_run.py — in a real routine run, Claude writes this section.\n\n"
        f"Category `{chosen['category']}` is the largest underweight at "
        f"{chosen['current_pct']}% vs {chosen['target_pct']}% target — a "
        f"{_format_money(chosen['drift_usd'])} gap. Route this week's deposit to "
        f"`{ticker}` per the primary_ticker rule.\n\n"
        f"${amount_usd:.2f} (~{shares:.2f} shares of {ticker} at ${price:.2f}).\n\n"
    )

    positions = "## Positions\n\n| Ticker | Category | Market Value | Weight |\n|---|---|---|---|\n"
    for p in holdings["positions"]:
        pct = 100 * p["market_value"] / holdings["total_usd"]
        positions += f"| {p['ticker']} | {p['category']} | ${p['market_value']:,.0f} | {pct:.1f}% |\n"
    if holdings.get("cash_usd", 0) > 0:
        cash_pct = 100 * holdings["cash_usd"] / holdings["total_usd"]
        positions += f"| Cash | — | ${holdings['cash_usd']:,.0f} | {cash_pct:.1f}% |\n"
    positions += "\n"

    drift = "## Drift\n\n| Category | Current % | Target % | Drift $ |\n|---|---|---|---|\n"
    for r in drift_rows:
        arrow = "↑" if r["drift_usd"] > 0 else ("↓" if r["drift_usd"] < 0 else "·")
        drift += f"| {r['category']} | {r['current_pct']}% | {r['target_pct']}% | {_format_money(r['drift_usd'])} {arrow} |\n"
    drift += "\n"

    vix = market["indices"].get("VIX", {})
    tnx = market["indices"].get("TNX_10y_pct", {})
    mkt = (
        f"## Market context\n\n"
        f"- VIX {vix.get('level', 'n/a')}, 1w change {vix.get('change_1w', 'n/a')}.\n"
        f"- 10y Treasury yield {tnx.get('level', 'n/a')}%.\n"
        f"- {len(market['tickers'])} held tickers returned data; {len(market['sectors'])} sector ETFs returned data.\n"
        f"- Warnings: {len(market.get('warnings', []))}.\n\n"
    )

    caveats = (
        f"## Caveats\n\n"
        f"- This is a DRY-RUN. The reasoning above is mechanical, not Claude-authored.\n"
    )
    if market.get("warnings"):
        caveats += f"- Market-data warnings: {'; '.join(market['warnings'][:3])}\n"
    caveats += "\n"

    disclaimer = "## Disclaimer\n\nNot financial advice. LLMs can be wrong. Sanity-check before trading.\n"

    return frontmatter + headline + reasoning + positions + drift + mkt + caveats + disclaimer


def main() -> int:
    print("== Step 1: fetch_holdings.py --dry-run ==")
    _run_dry("fetch_holdings.py")

    print("\n== Step 2: market_context.py --dry-run ==")
    _run_dry("market_context.py")

    print("\n== Step 3: render recommendation (mechanical stub) ==")
    with HOLDINGS_TMP.open() as f:
        holdings = json.load(f)
    with MARKET_TMP.open() as f:
        market = json.load(f)
    with CONFIG_PATH.open() as f:
        config = yaml.safe_load(f)

    drift_rows = _compute_drift(holdings, config)
    chosen = _pick_underweight(drift_rows, config["drift_tolerance_pct"], holdings["total_usd"])
    print(f"Mechanical pick: {chosen['primary_ticker']} ({chosen['category']}) — drift {_format_money(chosen['drift_usd'])}")

    today = date.today().isoformat()
    md = _render(holdings, market, drift_rows, chosen, today)

    RECS_DIR.mkdir(exist_ok=True)
    OUT_PATH.write_text(md)
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")

    print("\n== Step 4: post_discord.py (mock webhook) ==")
    fm, headline, sections = post_discord.parse_recommendation(md)
    embed = post_discord.build_embed(fm, headline, sections, OUT_PATH.name)
    post_discord.post_webhook("mock://local-dry-run", embed)

    print("\n== DRY-RUN complete ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
