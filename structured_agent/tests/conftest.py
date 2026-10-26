"""Shared fixtures. Validator tests use the reviewed registry bundle file, so they need no database or Databricks."""
from __future__ import annotations

import pytest

from structured_agent.config import QueryLimits
from structured_agent.metadata.sources import METADATA_DIR
from structured_agent.policy import AccessPolicy
from structured_agent.registry.models import RegistryBundle
from structured_agent.sql_validation.validator import SqlValidator

WORKSPACE = "wbg-procurement-poc"


@pytest.fixture(scope="session")
def bundle() -> RegistryBundle:
    latest = sorted((METADATA_DIR / "bundles").glob("registry_*.json"))[-1]
    return RegistryBundle.model_validate_json(latest.read_text())


@pytest.fixture(scope="session")
def policy() -> AccessPolicy:
    return AccessPolicy()


@pytest.fixture(scope="session")
def limits() -> QueryLimits:
    return QueryLimits()


@pytest.fixture(scope="session")
def validator(bundle, policy, limits) -> SqlValidator:
    return SqlValidator(bundle, policy, limits)


@pytest.fixture(scope="session")
def analyst(policy):
    return policy.resolve("analyst-demo", WORKSPACE)


@pytest.fixture(scope="session")
def steward(policy):
    return policy.resolve("steward-demo", WORKSPACE)


@pytest.fixture(scope="session")
def guest(policy):
    return policy.resolve("guest-demo", WORKSPACE)


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    """Every test runs offline: no live-call opt-in and no real httpx network access (see nl2sql_fakes)."""
    from structured_agent.tests.nl2sql_fakes import block_live_network

    block_live_network(monkeypatch)
