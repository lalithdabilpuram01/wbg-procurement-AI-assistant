"""Fixtures for the MCP transport tests.

Every test in this folder talks to a separate MCP server process over stdio through the official MCP client; none
calls the service in-process. Run with the isolated environment (from procurement-intelligence/):
    .venv-structured-mcp/bin/python -m pytest structured_agent/tests_mcp -q -p no:cacheprovider

Server processes used:
    analyst                 analyst-demo, labelled development execution enabled
    analyst_default_deny    analyst-demo, development execution NOT enabled (default deny)
    analyst_short_timeout   analyst-demo, development execution enabled, 1-second query timeout
    steward                 steward-demo, development execution enabled
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from silver.common.session import get_spark
from structured_agent.mcp_transport.client import server_parameters
from tests.live.structured_agent_mcp.mcp_session import BackgroundMcpSession


def open_server(**options) -> BackgroundMcpSession:
    return BackgroundMcpSession(server_parameters(**options)).open()


def read_audit(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def child_pids() -> set[int]:
    """Direct child processes of this test process (MCP servers started by stdio_client)."""
    found = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()
    return {int(pid) for pid in found}


@pytest.fixture(scope="session")
def audit_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("mcp_audit")


@pytest.fixture(scope="session")
def analyst(audit_dir):
    session = open_server(principal_id="analyst-demo", allow_dev_execution=True, audit_log=audit_dir / "analyst.jsonl")
    yield session
    session.close()


@pytest.fixture(scope="session")
def analyst_default_deny(audit_dir):
    session = open_server(principal_id="analyst-demo", audit_log=audit_dir / "default_deny.jsonl")
    yield session
    session.close()


@pytest.fixture(scope="session")
def analyst_short_timeout(audit_dir):
    session = open_server(principal_id="analyst-demo", allow_dev_execution=True, timeout_seconds=1,
                          audit_log=audit_dir / "short_timeout.jsonl")
    yield session
    session.close()


@pytest.fixture(scope="session")
def steward(audit_dir):
    session = open_server(principal_id="steward-demo", allow_dev_execution=True, audit_log=audit_dir / "steward.jsonl")
    yield session
    session.close()


# ------------------------------------------------------------------------------------- Databricks baselines and state
@pytest.fixture(scope="session")
def spark():
    """Trusted read-only session for baselines and state checks (separate from the MCP server's session)."""
    return get_spark()


def databricks_state(spark) -> dict:
    """Latest Delta version of every Silver and Gold table, plus the list of all objects in both schemas."""
    objects = spark.sql("""SELECT table_schema, table_name, table_type FROM workspace.information_schema.tables
                           WHERE table_schema IN ('wbg_silver', 'wbg_gold') ORDER BY 1, 2""").collect()
    versions = {}
    for obj in objects:
        if obj.table_type != "VIEW":
            fqn = f"workspace.{obj.table_schema}.{obj.table_name}"
            versions[fqn] = spark.sql(f"DESCRIBE HISTORY {fqn} LIMIT 1").collect()[0]["version"]
    return {"objects": [f"{o.table_schema}.{o.table_name}" for o in objects], "versions": versions}


@pytest.fixture(scope="session", autouse=True)
def databricks_state_at_start(spark) -> dict:
    return databricks_state(spark)
