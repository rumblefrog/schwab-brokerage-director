#!/usr/bin/env python3
"""One-time interactive setup for SnapTrade.

Run this locally BEFORE configuring the Claude Code Routine. It:

1. Reads/prompts for `SNAPTRADE_CLIENT_ID` and `SNAPTRADE_CONSUMER_KEY`.
2. Registers a new SnapTrade user and captures the `userSecret`.
3. Generates a Connection Portal URL. Open it in a browser, choose
   Charles Schwab, and complete SSO on Schwab's own page (SnapTrade
   never sees your Schwab credentials).
4. Prints the full set of env vars to paste into your Claude Code
   environment at https://claude.ai/settings.

Re-running this script registers a NEW user (the old user is orphaned).
To re-authorize Schwab for the same user without re-registering, use
scripts/relink_snaptrade.py instead.
"""
from __future__ import annotations

import os
import sys
from typing import Any

USER_ID_DEFAULT = "self"


def _prompt(name: str, hint: str = "") -> str:
    existing = os.environ.get(name)
    if existing:
        print(f"Using {name} from environment.")
        return existing
    tail = f" ({hint})" if hint else ""
    val = input(f"{name}{tail}: ").strip()
    if not val:
        print(f"{name} is required", file=sys.stderr)
        sys.exit(1)
    return val


def _register(client: Any, user_id: str) -> str:
    """Register a new SnapTrade user and return the userSecret."""
    resp = client.authentication.register_snap_trade_user(user_id=user_id)
    body = getattr(resp, "body", resp)
    if isinstance(body, dict):
        secret = body.get("userSecret") or body.get("user_secret")
    else:
        secret = getattr(body, "userSecret", None) or getattr(body, "user_secret", None)
    if not secret:
        raise RuntimeError(f"SnapTrade register returned no userSecret: {body!r}")
    return secret


def _login(client: Any, user_id: str, user_secret: str) -> str:
    """Generate a Connection Portal URL for the given user."""
    resp = client.authentication.login_snap_trade_user(user_id=user_id, user_secret=user_secret)
    body = getattr(resp, "body", resp)
    if isinstance(body, dict):
        url = body.get("redirectURI") or body.get("redirect_uri")
    else:
        url = getattr(body, "redirectURI", None) or getattr(body, "redirect_uri", None)
    if not url:
        raise RuntimeError(f"SnapTrade login returned no redirect URI: {body!r}")
    return url


def run(client_factory: Any = None) -> int:
    client_id = _prompt("SNAPTRADE_CLIENT_ID", "from https://dashboard.snaptrade.com/")
    consumer_key = _prompt("SNAPTRADE_CONSUMER_KEY", "keep this secret")
    user_id = os.environ.get("SNAPTRADE_USER_ID") or USER_ID_DEFAULT

    if client_factory is None:
        from snaptrade_client import SnapTrade  # lazy
        client = SnapTrade(client_id=client_id, consumer_key=consumer_key)
    else:
        client = client_factory(client_id=client_id, consumer_key=consumer_key)

    print(f"\nRegistering SnapTrade user '{user_id}'…")
    user_secret = _register(client, user_id)
    print("User registered.\n")

    print("Generating Connection Portal URL…")
    url = _login(client, user_id, user_secret)

    print("\n" + "=" * 72)
    print("  OPEN THIS URL IN YOUR BROWSER, CHOOSE CHARLES SCHWAB, AND LOG IN:")
    print("=" * 72)
    print(f"\n  {url}\n")
    print("=" * 72)
    print("\nOnce authorized, add these 5 env vars to your Claude Code environment")
    print("at https://claude.ai/settings (Environments tab):\n")
    print(f"  SNAPTRADE_CLIENT_ID={client_id}")
    print(f"  SNAPTRADE_CONSUMER_KEY={consumer_key}")
    print(f"  SNAPTRADE_USER_ID={user_id}")
    print(f"  SNAPTRADE_USER_SECRET={user_secret}")
    print(f"  DISCORD_WEBHOOK_URL=<your-discord-webhook-url>")
    print("\nKeep the userSecret safe — it cannot be recovered if lost.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
