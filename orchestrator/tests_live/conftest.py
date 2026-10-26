"""Live Lakebase checkpoint tests: opt-in only, development branch only, no LLM.

Run (from orchestrator/):
    WBG_CKPT_LIVE_TESTS=1 env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m pytest tests_live -q

* Skipped unless WBG_CKPT_LIVE_TESTS=1.
* The target is WBG_CKPT_ENDPOINT, by default the `dev-orchestrator` branch. `CheckpointDbSettings` refuses the
  `production` branch. The host is looked up with the Databricks CLI, and the login is the one already configured for
  the Lakebase POC (`lakebase.local.env`). Credentials are 60-minute OAuth tokens created at run time.
* No LLM: the live-call opt-in is removed and any real httpx request fails. The audit log goes to a temporary file.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from structured_agent.tests.nl2sql_fakes import block_live_network

from wbg_orchestrator.checkpointing import CheckpointDbSettings, validate_settings

DEV_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WBG_CKPT_LIVE_TESTS") != "1":
        skip = pytest.mark.skip(reason="live Lakebase checkpoint tests need WBG_CKPT_LIVE_TESTS=1")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch, tmp_path):
    block_live_network(monkeypatch)
    monkeypatch.setenv("WBG_AUDIT_LOG", str(tmp_path / "audit.jsonl"))


@pytest.fixture(scope="session")
def ckpt_settings() -> CheckpointDbSettings:
    from infrastructure.lakebase_poc.lakebase_poc.credentials import databricks_cli
    from infrastructure.lakebase_poc.lakebase_poc.settings import load_settings
    login = load_settings()
    endpoint = os.environ.get("WBG_CKPT_ENDPOINT", DEV_ENDPOINT)
    described = subprocess.run([databricks_cli(), "postgres", "get-endpoint", endpoint, "-p", login.databricks_profile, "-o", "json"],
                               capture_output=True, text=True, check=True, timeout=90)
    settings = CheckpointDbSettings(host=json.loads(described.stdout)["status"]["hosts"]["host"], database="databricks_postgres",
                                    user=login.user, endpoint=endpoint, databricks_profile=login.databricks_profile)
    validate_settings(settings)                       # refuses production/main, public, plain-text connections
    return settings
