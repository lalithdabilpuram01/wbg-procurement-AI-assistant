"""Fail-closed guard for real (network) LLM requests.

Live access needs two independent opt-ins, both given for the current invocation:
  1. operator confirmation (the CLI's --confirm-live-call flag), and
  2. WBG_ALLOW_LIVE_LLM_CALLS=true in the PROCESS environment.
The variable is deliberately never read from .env (app_config.check_env flags it if it appears there), so valid
secrets in .env can never enable live calls on their own.

`authorize_live_access` is the only way to obtain a `LiveAccess` grant, and PortkeyClaudeClient needs one for any
transport that is not an in-memory test transport. The client re-checks the environment before every live request,
so the grant stops working as soon as the variable is unset.
"""
from __future__ import annotations

import os

LIVE_LLM_ENV_VAR = "WBG_ALLOW_LIVE_LLM_CALLS"
_GRANT_TOKEN = object()   # private: only authorize_live_access can create a LiveAccess


class LiveCallsDisabled(RuntimeError):
    """A real LLM request was attempted without both live-access opt-ins."""


class LiveAccess:
    """Proof that both opt-ins were present when the client was created."""

    def __init__(self, token: object):
        if token is not _GRANT_TOKEN:
            raise LiveCallsDisabled("LiveAccess can only be created by authorize_live_access()")


def live_env_opt_in() -> bool:
    return os.environ.get(LIVE_LLM_ENV_VAR, "").strip().lower() == "true"


def require_live_env_opt_in() -> None:
    if not live_env_opt_in():
        raise LiveCallsDisabled(f"Live LLM calls are disabled: set {LIVE_LLM_ENV_VAR}=true for this invocation only")


def authorize_live_access(operator_confirmed: bool) -> LiveAccess:
    """Grant live access only when the operator confirmed AND the process environment opts in."""
    if not operator_confirmed:
        raise LiveCallsDisabled("Live LLM calls are disabled: operator confirmation (--confirm-live-call) is missing")
    require_live_env_opt_in()
    return LiveAccess(_GRANT_TOKEN)
