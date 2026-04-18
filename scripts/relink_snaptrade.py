#!/usr/bin/env python3
"""Regenerate a SnapTrade Connection Portal URL for the existing user.

Use this when the weekly routine posts the red "Schwab connection needs
re-linking" alert to Discord. It does NOT register a new user — it just
generates a fresh Connection Portal URL for the existing userSecret so
you can re-authorize Schwab in your browser.

Env vars required: SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY,
SNAPTRADE_USER_ID, SNAPTRADE_USER_SECRET.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from scripts.setup_snaptrade import _login

REQUIRED = (
    "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY",
    "SNAPTRADE_USER_ID",
    "SNAPTRADE_USER_SECRET",
)


def run(client_factory: Any = None) -> int:
    missing = [v for v in REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    if client_factory is None:
        from snaptrade_client import SnapTrade  # lazy
        client = SnapTrade(
            client_id=os.environ["SNAPTRADE_CLIENT_ID"],
            consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
        )
    else:
        client = client_factory(
            client_id=os.environ["SNAPTRADE_CLIENT_ID"],
            consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
        )

    url = _login(client, os.environ["SNAPTRADE_USER_ID"], os.environ["SNAPTRADE_USER_SECRET"])

    print("\n" + "=" * 72)
    print("  OPEN THIS URL IN YOUR BROWSER AND RE-AUTHORIZE CHARLES SCHWAB:")
    print("=" * 72)
    print(f"\n  {url}\n")
    print("=" * 72)
    print("\nThis does not change your userSecret. Existing env vars continue to work")
    print("once Schwab is re-authorized.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
