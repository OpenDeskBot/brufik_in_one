from __future__ import annotations

import time

import pytest

from deskbot_server.auth.debug_ws_token import (
    extract_debug_token_from_query,
    issue_debug_ws_token,
    verify_debug_ws_token,
)


def test_issue_and_verify_debug_ws_token(monkeypatch):
    monkeypatch.setenv("DESKBOT_WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DESKBOT_DEBUG_WS_TOKEN_DAYS", "7")
    info = issue_debug_ws_token("user-abc")
    assert info.token
    assert info.user_id == "user-abc"
    assert info.expires_in == 7 * 86400
    assert verify_debug_ws_token(info.token) == "user-abc"


def test_verify_rejects_tampered_token(monkeypatch):
    monkeypatch.setenv("DESKBOT_WEB_SECRET_KEY", "test-secret")
    info = issue_debug_ws_token("user-abc")
    assert verify_debug_ws_token(info.token + "x") is None


def test_verify_rejects_expired_token(monkeypatch):
    import time as time_mod

    monkeypatch.setenv("DESKBOT_WEB_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DESKBOT_DEBUG_WS_TOKEN_DAYS", "1")
    base = time_mod.time()
    monkeypatch.setattr(time_mod, "time", lambda: base)
    info = issue_debug_ws_token("user-abc")
    monkeypatch.setattr(time_mod, "time", lambda: base + 2 * 86400)
    assert verify_debug_ws_token(info.token) is None


def test_extract_debug_token_from_query():
    assert extract_debug_token_from_query({"debug_token": " tok "}) == "tok"
    assert extract_debug_token_from_query({"api_key": "odk_x"}) is None
