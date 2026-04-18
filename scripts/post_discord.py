#!/usr/bin/env python3
"""Post a recommendation markdown file to a Discord webhook.

Input format: tests/fixtures/example_recommendation.md — frontmatter + six
`## ` sections (Reasoning, Positions, Drift, Market context, Caveats,
Disclaimer) plus a `# ` headline.

Embed color is driven by the frontmatter `kind`:
    buy     -> green
    warning -> yellow
    alert   -> red

Discord embed limits (6000 chars total; 4096 description; 1024 per field
value; 25 fields) are enforced. Truncation order (first-shrunk to
last-shrunk): Positions, Drift, Market context, Caveats. Reasoning +
headline are never dropped — oversized reasoning gets truncated only
after all lower-priority fields are already shrunk/dropped.

Set DISCORD_WEBHOOK_URL=mock://... to skip the actual POST and log the
payload to stdout — useful for local_dry_run.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

EXIT_OK = 0
EXIT_GENERIC = 1

KIND_COLORS = {
    "buy": 0x2ECC71,      # green
    "warning": 0xF1C40F,  # yellow
    "alert": 0xE74C3C,    # red
}

LIMITS = {
    "total": 6000,
    "title": 256,
    "description": 4096,
    "field_value": 1024,
    "field_name": 256,
    "footer": 2048,
}

# Lower-priority sections truncated first. Higher-priority (Reasoning,
# headline) only touched after all of these are shrunk or dropped.
SHRINK_ORDER = ["Positions", "Drift", "Market context", "Caveats"]

# Order the fields appear in the embed (highest-signal first).
FIELD_ORDER = ["Caveats", "Market context", "Drift", "Positions"]

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
HEADLINE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
SECTION_SPLIT_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")


def parse_recommendation(text: str) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Return (frontmatter, headline, sections_by_name)."""
    fm: dict[str, Any] = {}
    body = text
    m = FRONTMATTER_RE.match(text)
    if m:
        fm = yaml.safe_load(m.group(1)) or {}
        body = text[m.end():]

    headline_match = HEADLINE_RE.search(body)
    headline = headline_match.group(1).strip() if headline_match else ""

    # split on ## headers: [pre, name1, body1, name2, body2, ...]
    parts = SECTION_SPLIT_RE.split(body)
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].strip()] = parts[i + 1].strip()

    return fm, headline, sections


def _truncate(s: str, limit: int, suffix: str) -> str:
    if len(s) <= limit:
        return s
    keep = limit - len(suffix)
    if keep <= 0:
        return suffix[:limit]
    return s[:keep] + suffix


def _embed_size(embed: dict[str, Any]) -> int:
    """Approximate char count per Discord's total-embed-size rule."""
    total = len(embed.get("title") or "")
    total += len(embed.get("description") or "")
    for f in embed.get("fields") or []:
        total += len(f.get("name") or "") + len(f.get("value") or "")
    total += len((embed.get("footer") or {}).get("text") or "")
    return total


def build_embed(
    frontmatter: dict[str, Any],
    headline: str,
    sections: dict[str, str],
    source_filename: str,
) -> dict[str, Any]:
    kind = (frontmatter.get("kind") or "buy").lower()
    color = KIND_COLORS.get(kind, KIND_COLORS["buy"])
    suffix = f"… (truncated — see recommendations/{source_filename})"

    embed: dict[str, Any] = {
        "title": _truncate(headline, LIMITS["title"], suffix),
        "color": color,
        "description": sections.get("Reasoning", ""),
        "fields": [],
        "footer": {"text": _truncate(sections.get("Disclaimer", ""), LIMITS["footer"], suffix)},
    }

    for name in FIELD_ORDER:
        value = sections.get(name, "").strip()
        if not value:
            continue
        embed["fields"].append({"name": name, "value": value, "inline": False})

    # Step 1: per-field and description hard limits.
    embed["description"] = _truncate(embed["description"], LIMITS["description"], suffix)
    for f in embed["fields"]:
        f["value"] = _truncate(f["value"], LIMITS["field_value"], suffix)

    # Step 2: shrink lower-priority fields until total fits.
    for name in SHRINK_ORDER:
        if _embed_size(embed) <= LIMITS["total"]:
            break
        field = next((f for f in embed["fields"] if f["name"] == name), None)
        if field is None:
            continue
        if len(field["value"]) > 200:
            field["value"] = _truncate(field["value"], 200, suffix)
        else:
            embed["fields"].remove(field)

    # Step 3: last resort — truncate the description (reasoning).
    if _embed_size(embed) > LIMITS["total"]:
        overhead = _embed_size(embed) - len(embed["description"])
        headroom = max(500, LIMITS["total"] - overhead)
        embed["description"] = _truncate(embed["description"], headroom, suffix)

    return embed


def post_webhook(
    url: str,
    embed: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    sleep: "callable" = time.sleep,  # type: ignore[valid-type]
) -> bool:
    """POST the embed. Retry once on 5xx; no retry on 4xx. Return True on success."""
    payload = {"embeds": [embed]}

    if url.startswith("mock://"):
        print("=== MOCK DISCORD POST ===")
        print(json.dumps(payload, indent=2))
        print("=========================")
        return True

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=10.0)

    try:
        for attempt in range(2):
            try:
                resp = client.post(url, json=payload)
            except httpx.RequestError as exc:
                print(f"Discord transport error (attempt {attempt + 1}): {exc}", file=sys.stderr)
                if attempt == 0:
                    sleep(2)
                    continue
                return False

            if 200 <= resp.status_code < 300:
                return True
            if 500 <= resp.status_code < 600 and attempt == 0:
                print(f"Discord 5xx (attempt 1): {resp.status_code}; retrying", file=sys.stderr)
                sleep(2)
                continue
            # 4xx or second 5xx: log and give up.
            body = (resp.text or "")[:500]
            print(f"Discord rejected {resp.status_code}: {body}", file=sys.stderr)
            return False
        return False
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to recommendation markdown file.")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return EXIT_GENERIC

    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL not set", file=sys.stderr)
        return EXIT_GENERIC

    text = path.read_text()
    fm, headline, sections = parse_recommendation(text)
    embed = build_embed(fm, headline, sections, source_filename=path.name)

    ok = post_webhook(url, embed)
    return EXIT_OK if ok else EXIT_GENERIC


if __name__ == "__main__":
    sys.exit(main())
