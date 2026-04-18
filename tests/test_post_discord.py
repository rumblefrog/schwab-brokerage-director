"""Unit tests for scripts/post_discord.py."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from scripts import post_discord

FIXTURES = Path(__file__).parent / "fixtures"
CANONICAL = FIXTURES / "example_recommendation.md"

WEBHOOK = "https://discord.com/api/webhooks/fake/token"


# --- Parsing ---------------------------------------------------------------


def test_parse_canonical_fixture_has_frontmatter_and_sections():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    assert fm["kind"] == "buy"
    assert fm["ticker"] == "VTI"
    assert fm["amount_usd"] == 500.00
    assert fm["share_estimate"] == 1.43
    assert headline == "This week: $500 → VTI"
    assert set(sections) == {"Reasoning", "Positions", "Drift", "Market context", "Caveats", "Disclaimer"}


def test_parse_alert_style_with_missing_sections():
    text = """---
kind: alert
date: 2026-04-20
---

# ALERT: Schwab connection needs re-linking

## Reasoning
SnapTrade rejected credentials. Re-run setup locally.

## Disclaimer
Not financial advice. LLMs can be wrong.
"""
    fm, headline, sections = post_discord.parse_recommendation(text)
    assert fm["kind"] == "alert"
    assert "Schwab" in headline
    assert "Reasoning" in sections
    assert "Positions" not in sections


# --- Build embed: color mapping --------------------------------------------


@pytest.mark.parametrize(
    "kind,expected_color",
    [("buy", 0x2ECC71), ("warning", 0xF1C40F), ("alert", 0xE74C3C)],
)
def test_embed_color_matches_kind(kind: str, expected_color: int):
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    fm["kind"] = kind
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert embed["color"] == expected_color


def test_embed_unknown_kind_falls_back_to_buy_color():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    fm["kind"] = "garbage"
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert embed["color"] == post_discord.KIND_COLORS["buy"]


# --- Build embed: structure ------------------------------------------------


def test_embed_title_is_headline():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert embed["title"] == "This week: $500 → VTI"


def test_embed_description_is_reasoning():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert "US broad equity" in embed["description"]
    # Fractional-share line must survive into the description.
    assert "1.43 shares of VTI at $350.37" in embed["description"]


def test_embed_field_order_places_caveats_first():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    field_names = [f["name"] for f in embed["fields"]]
    assert field_names == ["Caveats", "Market context", "Drift", "Positions"]


def test_embed_footer_carries_disclaimer():
    fm, headline, sections = post_discord.parse_recommendation(CANONICAL.read_text())
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert "Not financial advice" in embed["footer"]["text"]


# --- Truncation ------------------------------------------------------------


def _big_text(n: int, seed: str = "x") -> str:
    return (seed * 80 + "\n") * (n // 80)


def test_oversized_embed_stays_under_total_limit():
    # Build a monstrously oversized recommendation.
    fm = {"kind": "buy", "date": "2026-04-20", "ticker": "VTI"}
    headline = "This week: $500 → VTI"
    sections = {
        "Reasoning": _big_text(5000, "reasoning "),
        "Positions": _big_text(4000, "POS | "),
        "Drift": _big_text(3000, "DRIFT | "),
        "Market context": _big_text(2000, "MKT | "),
        "Caveats": _big_text(1500, "caveat "),
        "Disclaimer": "Not financial advice. LLMs can be wrong.",
    }
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert post_discord._embed_size(embed) <= post_discord.LIMITS["total"]
    # Each field value must individually be under 1024.
    for f in embed["fields"]:
        assert len(f["value"]) <= post_discord.LIMITS["field_value"]


def test_truncation_preserves_headline_and_reasoning_first():
    fm = {"kind": "buy"}
    headline = "This week: $500 → VTI"
    sections = {
        "Reasoning": "Short reasoning paragraph.",
        "Positions": _big_text(5000, "POS | "),
        "Drift": _big_text(3000, "D | "),
        "Market context": _big_text(3000, "M | "),
        "Caveats": _big_text(3000, "c "),
        "Disclaimer": "x",
    }
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    assert embed["title"] == headline
    # Reasoning (description) untouched because the oversized fields absorbed the cut.
    assert embed["description"] == "Short reasoning paragraph."


def test_truncation_shrinks_positions_before_drift():
    fm = {"kind": "buy"}
    headline = "H"
    sections = {
        "Reasoning": "r",
        "Positions": _big_text(4500, "POS | "),
        "Drift": _big_text(4500, "DRIFT | "),
        "Market context": "mc",
        "Caveats": "c",
        "Disclaimer": "d",
    }
    embed = post_discord.build_embed(fm, headline, sections, "2026-04-20.md")
    positions = next(f for f in embed["fields"] if f["name"] == "Positions")
    drift = next(f for f in embed["fields"] if f["name"] == "Drift")
    # Positions should be shrunk further than drift (it's first in SHRINK_ORDER).
    assert len(positions["value"]) <= len(drift["value"])


def test_truncation_suffix_references_source_filename():
    fm = {"kind": "buy"}
    sections = {
        "Reasoning": "short",
        "Positions": _big_text(5000, "p "),
        "Disclaimer": "d",
    }
    embed = post_discord.build_embed(fm, "H", sections, "2026-04-20.md")
    positions = next(f for f in embed["fields"] if f["name"] == "Positions")
    assert "2026-04-20.md" in positions["value"]


# --- _truncate guarantees --------------------------------------------------


def test_truncate_returns_input_when_within_limit():
    assert post_discord._truncate("abc", 10, "…suffix") == "abc"


def test_truncate_clamps_to_limit_even_when_suffix_alone_exceeds_it():
    # Regression: previously returned the full suffix and could exceed limit.
    out = post_discord._truncate("some long text", 3, "… (truncated)")
    assert len(out) <= 3


def test_truncate_preserves_prefix_and_appends_suffix_in_normal_case():
    out = post_discord._truncate("abcdefghij", 7, "..")
    assert out == "abcde.."
    assert len(out) == 7


# --- POST behavior ---------------------------------------------------------


@respx.mock
def test_post_webhook_success_returns_true():
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    ok = post_discord.post_webhook(
        WEBHOOK, {"title": "t"}, sleep=lambda _s: None
    )
    assert ok is True
    assert route.called


@respx.mock
def test_post_webhook_retries_once_on_5xx():
    responses = [httpx.Response(503), httpx.Response(204)]
    route = respx.post(WEBHOOK).mock(side_effect=responses)
    ok = post_discord.post_webhook(WEBHOOK, {"title": "t"}, sleep=lambda _s: None)
    assert ok is True
    assert route.call_count == 2


@respx.mock
def test_post_webhook_no_retry_on_4xx():
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(404, text="not found"))
    ok = post_discord.post_webhook(WEBHOOK, {"title": "t"}, sleep=lambda _s: None)
    assert ok is False
    assert route.call_count == 1


@respx.mock
def test_post_webhook_gives_up_after_second_5xx():
    route = respx.post(WEBHOOK).mock(
        side_effect=[httpx.Response(500), httpx.Response(503)]
    )
    ok = post_discord.post_webhook(WEBHOOK, {"title": "t"}, sleep=lambda _s: None)
    assert ok is False
    assert route.call_count == 2


def test_mock_url_logs_instead_of_posting(capsys):
    ok = post_discord.post_webhook("mock://", {"title": "t", "color": 1}, sleep=lambda _s: None)
    assert ok is True
    out = capsys.readouterr().out
    assert "MOCK DISCORD POST" in out
    # Payload should be valid JSON inside the log block.
    payload_start = out.index("{")
    payload_end = out.rindex("}") + 1
    parsed = json.loads(out[payload_start:payload_end])
    assert parsed["embeds"][0]["title"] == "t"


# --- End-to-end main() ----------------------------------------------------


def test_main_posts_canonical_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "mock://")
    rc = post_discord.main([str(CANONICAL)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VTI" in out
    assert '"color": 3066993' in out  # 0x2ecc71 decimal


def test_main_without_webhook_env_exits_generic(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    rc = post_discord.main([str(CANONICAL)])
    assert rc == post_discord.EXIT_GENERIC


def test_main_missing_file_exits_generic(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "mock://")
    rc = post_discord.main([str(tmp_path / "does_not_exist.md")])
    assert rc == post_discord.EXIT_GENERIC
