"""Every test here runs offline for the LLM: no live-call opt-in and no real httpx network access."""
from __future__ import annotations

import pytest

from structured_agent.tests.nl2sql_fakes import block_live_network


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    block_live_network(monkeypatch)
