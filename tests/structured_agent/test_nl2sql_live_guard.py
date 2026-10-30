"""The fail-closed live-call guard: two independent opt-ins, no bypass through the client, .env cannot enable it.

No test here makes a network request: the autouse safety net (conftest) makes any real request fail loudly, and the
cases that obtain a grant never send anything.
"""
from __future__ import annotations

import sys

import httpx
import pytest

from structured_agent.nl2sql import cli
from structured_agent.nl2sql import settings as settings_module
from structured_agent.nl2sql.live_guard import LIVE_LLM_ENV_VAR, LiveAccess, LiveCallsDisabled, authorize_live_access
from structured_agent.nl2sql.llm_client import ClaudeRequest, PortkeyClaudeClient
from structured_agent.nl2sql.settings import ConfigurationError, load_claude_settings
from tests.structured_agent.nl2sql_fakes import TEST_API_KEY, claude_settings

VALID_ENV_FILE = {"PORTKEY_API_KEY": TEST_API_KEY, "PORTKEY_CLAUDE_MODEL": "@wbg-anthropic/claude-sonnet-5-5",
                  "WBG_ALLOW_DEV_OWNER_CREDENTIALS": "false"}


@pytest.fixture
def valid_env_file(monkeypatch):
    """Pretend .env holds valid credentials (the real .env is never read by these tests)."""
    monkeypatch.setattr(settings_module, "read_env_file", lambda: dict(VALID_ENV_FILE))
    for name in VALID_ENV_FILE:
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------------------------------- default: denied
def test_valid_env_file_alone_cannot_create_a_live_client(valid_env_file):
    settings = load_claude_settings()
    with pytest.raises(LiveCallsDisabled):
        PortkeyClaudeClient(settings)


@pytest.mark.parametrize("operator_confirmed, env_value", [(False, None), (False, "true"), (True, None), (True, "false"),
                                                            (True, "1")])
def test_both_opt_ins_are_required(monkeypatch, operator_confirmed, env_value):
    if env_value is not None:
        monkeypatch.setenv(LIVE_LLM_ENV_VAR, env_value)
    with pytest.raises(LiveCallsDisabled):
        authorize_live_access(operator_confirmed)


def test_both_opt_ins_grant_access_without_sending_anything(monkeypatch):
    monkeypatch.setenv(LIVE_LLM_ENV_VAR, "true")
    client = PortkeyClaudeClient(claude_settings(), live_access=authorize_live_access(True))
    assert client.timeout_seconds == 45.0                 # constructed; no request was made


def test_grant_stops_working_when_the_env_opt_in_is_removed(monkeypatch):
    monkeypatch.setenv(LIVE_LLM_ENV_VAR, "true")
    client = PortkeyClaudeClient(claude_settings(), live_access=authorize_live_access(True))
    monkeypatch.delenv(LIVE_LLM_ENV_VAR)
    with pytest.raises(LiveCallsDisabled):
        client.complete(ClaudeRequest(system="s", user="u", trace_id="req_guard"))


# --------------------------------------------------------------------------------------------- no bypasses
def test_a_real_transport_cannot_be_injected():
    with pytest.raises(LiveCallsDisabled):
        PortkeyClaudeClient(claude_settings(), transport=httpx.HTTPTransport())


def test_a_grant_cannot_be_forged():
    with pytest.raises(LiveCallsDisabled):
        LiveAccess(object())


def test_the_opt_in_variable_is_refused_in_the_env_file(monkeypatch):
    monkeypatch.setattr(settings_module, "read_env_file", lambda: {**VALID_ENV_FILE, LIVE_LLM_ENV_VAR: "true"})
    with pytest.raises(ConfigurationError, match=LIVE_LLM_ENV_VAR):
        load_claude_settings()


# ------------------------------------------------------------------------------------------- model configuration
@pytest.mark.parametrize("model", ["@other-provider/claude-sonnet-5-5", "@wbg-anthropic/grok-3", "@wbg-anthropic/gpt-4o",
                                   "claude-sonnet-5-5"])
def test_unsupported_model_configuration_is_refused(monkeypatch, model):
    monkeypatch.setattr(settings_module, "read_env_file", lambda: {**VALID_ENV_FILE, "PORTKEY_CLAUDE_MODEL": model})
    monkeypatch.delenv("PORTKEY_CLAUDE_MODEL", raising=False)
    with pytest.raises(ConfigurationError) as raised:
        load_claude_settings()
    assert "PORTKEY_CLAUDE_MODEL" in str(raised.value) and model not in str(raised.value) and TEST_API_KEY not in str(raised.value)


def test_approved_model_configuration_loads(valid_env_file):
    assert load_claude_settings().model == "@wbg-anthropic/claude-sonnet-5-5"


# --------------------------------------------------------------------------------------------------------- CLI
@pytest.mark.parametrize("argv, env_value", [
    (["cli", "question"], None),                              # neither opt-in
    (["cli", "--confirm-live-call", "question"], None),       # flag only
    (["cli", "question"], "true"),                            # environment only
])
def test_cli_refuses_unless_both_opt_ins_are_present(monkeypatch, capsys, valid_env_file, argv, env_value):
    def no_client(*args, **kwargs):
        raise AssertionError("no Portkey client may be created")

    monkeypatch.setattr(cli, "PortkeyClaudeClient", no_client)
    if env_value is not None:
        monkeypatch.setenv(LIVE_LLM_ENV_VAR, env_value)
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 3
    assert "Refusing to run" in capsys.readouterr().err
