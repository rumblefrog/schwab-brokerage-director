"""Tests for scripts/local_dry_run.py — the fixture-based E2E simulator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import local_dry_run, post_discord

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixtures():
    with (FIXTURES / "holdings.json").open() as f:
        holdings = json.load(f)
    with (FIXTURES / "market_context.json").open() as f:
        market = json.load(f)
    import yaml
    with (local_dry_run.CONFIG_PATH).open() as f:
        config = yaml.safe_load(f)
    return holdings, market, config


def test_compute_drift_sums_category_values():
    holdings, _, config = _load_fixtures()
    rows = local_dry_run._compute_drift(holdings, config)
    by_cat = {r["category"]: r for r in rows}
    # Values derived from holdings.json (normalize() output of holdings_raw.json).
    # VTI 49598.38 + SWPPX 12137.48 = 61735.86
    assert by_cat["us_equity_broad"]["current_usd"] == pytest.approx(61735.86, abs=0.01)
    # GDX 4660.70 + WPM 3985.80 + GLDM 2210.95 + SIL 1947.32 = 12804.77
    assert by_cat["precious_metals"]["current_usd"] == pytest.approx(12804.77, abs=0.01)


def test_compute_drift_positive_means_underweight():
    """CLAUDE.md §7: drift_usd = target - current; positive = underweight."""
    holdings, _, config = _load_fixtures()
    rows = local_dry_run._compute_drift(holdings, config)
    by_cat = {r["category"]: r for r in rows}
    # us_equity_broad at 52.7% vs 55% target: underweight → positive drift
    assert by_cat["us_equity_broad"]["drift_usd"] > 0
    # precious_metals at 10.9% vs 9% target: overweight → negative drift
    assert by_cat["precious_metals"]["drift_usd"] < 0


def test_pick_underweight_chooses_largest_positive_drift():
    holdings, _, config = _load_fixtures()
    rows = local_dry_run._compute_drift(holdings, config)
    chosen = local_dry_run._pick_underweight(rows, config["drift_tolerance_pct"], holdings["total_usd"])
    # VTI is primary_ticker for us_equity_broad — the most-underweight on this data.
    assert chosen["primary_ticker"] == "VTI"
    assert chosen["category"] == "us_equity_broad"


def test_pick_underweight_defaults_to_us_equity_broad_within_tolerance():
    # All categories within tolerance → default branch
    holdings = {"total_usd": 10000, "positions": [], "cash_usd": 0}
    config = {
        "categories": {
            "us_equity_broad": {"target_pct": 55, "primary_ticker": "VTI"},
            "cash_dry_powder": {"target_pct": 45, "primary_ticker": "SGOV"},
        }
    }
    rows = [
        {"category": "us_equity_broad", "target_pct": 55, "drift_usd": 50, "primary_ticker": "VTI", "current_pct": 54.5, "current_usd": 5450},
        {"category": "cash_dry_powder", "target_pct": 45, "drift_usd": -50, "primary_ticker": "SGOV", "current_pct": 45.5, "current_usd": 4550},
    ]
    chosen = local_dry_run._pick_underweight(rows, tolerance_pct=2, total_usd=10000)
    assert chosen["category"] == "us_equity_broad"


def test_render_output_parses_as_valid_recommendation():
    holdings, market, config = _load_fixtures()
    rows = local_dry_run._compute_drift(holdings, config)
    chosen = local_dry_run._pick_underweight(rows, config["drift_tolerance_pct"], holdings["total_usd"])
    md = local_dry_run._render(holdings, market, rows, chosen, today="2026-04-20")

    fm, headline, sections = post_discord.parse_recommendation(md)
    # Frontmatter must include all the embed-critical keys.
    assert fm["kind"] == "buy"
    # YAML parses bare YYYY-MM-DD as datetime.date; accept both forms.
    assert str(fm["date"]) == "2026-04-20"
    assert fm["ticker"] == "VTI"
    assert fm["amount_usd"] == 500.00
    assert fm["share_estimate"] > 0
    # Six canonical sections must all be present.
    assert {"Reasoning", "Positions", "Drift", "Market context", "Caveats", "Disclaimer"} <= set(sections)
    # Headline follows the canonical format.
    assert "VTI" in headline


def test_rendered_embed_fits_discord_limits():
    holdings, market, config = _load_fixtures()
    rows = local_dry_run._compute_drift(holdings, config)
    chosen = local_dry_run._pick_underweight(rows, config["drift_tolerance_pct"], holdings["total_usd"])
    md = local_dry_run._render(holdings, market, rows, chosen, today="2026-04-20")
    fm, headline, sections = post_discord.parse_recommendation(md)
    embed = post_discord.build_embed(fm, headline, sections, "DRY-RUN.md")
    assert post_discord._embed_size(embed) <= post_discord.LIMITS["total"]
