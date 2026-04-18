"""Smoke tests for scripts/setup_snaptrade.py + scripts/relink_snaptrade.py.

These are interactive local utilities, so the tests only verify the SDK
call shape + prompted env-var handling rather than covering every branch.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts import relink_snaptrade, setup_snaptrade


def _stub_client_factory(user_secret: str = "secret-123", redirect_url: str = "https://app.snaptrade.com/portal/abc"):
    client = MagicMock()
    client.authentication.register_snap_trade_user.return_value = MagicMock(
        body={"userSecret": user_secret}
    )
    client.authentication.login_snap_trade_user.return_value = MagicMock(
        body={"redirectURI": redirect_url}
    )
    return MagicMock(return_value=client), client


def test_setup_registers_user_and_prints_portal_url(monkeypatch, capsys):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    monkeypatch.setenv("SNAPTRADE_USER_ID", "self")

    factory, client = _stub_client_factory(user_secret="sek", redirect_url="https://portal.example/abc")
    rc = setup_snaptrade.run(client_factory=factory)
    assert rc == 0

    client.authentication.register_snap_trade_user.assert_called_once_with(user_id="self")
    client.authentication.login_snap_trade_user.assert_called_once_with(user_id="self", user_secret="sek")

    out = capsys.readouterr().out
    assert "https://portal.example/abc" in out
    assert "SNAPTRADE_USER_SECRET=sek" in out
    assert "DISCORD_WEBHOOK_URL" in out


def test_setup_raises_when_register_returns_no_secret(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")

    bad_client = MagicMock()
    bad_client.authentication.register_snap_trade_user.return_value = MagicMock(body={})
    factory = MagicMock(return_value=bad_client)

    with pytest.raises(RuntimeError, match="no userSecret"):
        setup_snaptrade.run(client_factory=factory)


def test_setup_prompts_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
    responses = iter(["cid-from-prompt", "ckey-from-prompt"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    factory, _client = _stub_client_factory()
    rc = setup_snaptrade.run(client_factory=factory)
    assert rc == 0
    out = capsys.readouterr().out
    assert "cid-from-prompt" in out


def test_relink_exits_nonzero_when_env_missing(monkeypatch, capsys):
    for v in relink_snaptrade.REQUIRED:
        monkeypatch.delenv(v, raising=False)
    rc = relink_snaptrade.run(client_factory=MagicMock())
    assert rc == 1
    assert "Missing env vars" in capsys.readouterr().err


def test_relink_prints_portal_url_without_registering(monkeypatch, capsys):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    monkeypatch.setenv("SNAPTRADE_USER_ID", "self")
    monkeypatch.setenv("SNAPTRADE_USER_SECRET", "existing-secret")

    factory, client = _stub_client_factory(redirect_url="https://portal.example/relink")
    rc = relink_snaptrade.run(client_factory=factory)
    assert rc == 0

    client.authentication.register_snap_trade_user.assert_not_called()
    client.authentication.login_snap_trade_user.assert_called_once_with(
        user_id="self", user_secret="existing-secret"
    )

    out = capsys.readouterr().out
    assert "https://portal.example/relink" in out
    assert "userSecret" in out  # reassurance text about secrets
