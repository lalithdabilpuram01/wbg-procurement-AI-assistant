"""Runtime settings for the Structured Data Agent, read from environment variables.

No secret is stored in code. Database credentials come from the DSN environment variables (a password, when the server
requires one, comes from PGPASSWORD or ~/.pgpass through libpq). The Databricks credential is the CLI profile.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class QueryLimits:
    """Execution budget enforced by the SQL validator and the execution gateway."""

    max_rows: int = 1000              # largest LIMIT a query may request
    timeout_seconds: int = 60         # wall-clock limit per query
    max_joins: int = 4                # joins across all scopes of one query
    max_ctes: int = 4
    max_subquery_depth: int = 3
    max_ast_nodes: int = 1500
    max_scanned_rows: int = 1_500_000  # sum of registered row counts of the base objects a query reads


# All runtime settings; each field reads its environment variable when a Settings object is created.
@dataclass(frozen=True)
class Settings:
    # PostgreSQL registry store: the runtime reads with a SELECT-only role; initialisation uses the owner role.
    registry_dsn: str = field(default_factory=lambda: os.environ.get(
        "WBG_REGISTRY_DSN", "postgresql://wbg_registry_reader@localhost:5432/wbg_structured_registry"))
    registry_admin_dsn: str = field(default_factory=lambda: os.environ.get(
        "WBG_REGISTRY_ADMIN_DSN", "postgresql://wbg_registry_admin@localhost:5432/wbg_structured_registry"))

    # Databricks execution credential (CLI profile; never logged).
    databricks_profile: str = field(default_factory=lambda: os.environ.get("WBG_DATABRICKS_PROFILE", "wbg-databricks-free"))
    # Write-capable (owner) credentials are refused unless this flag is set for controlled assessment testing.
    allow_dev_owner_credentials: bool = field(default_factory=lambda: _env_bool("WBG_ALLOW_DEV_OWNER_CREDENTIALS"))

    # Local embedding model, loaded from the offline Hugging Face cache only.
    embedding_model: str = "Alibaba-NLP/gte-base-en-v1.5"

    limits: QueryLimits = field(default_factory=lambda: QueryLimits(
        max_rows=_env_int("WBG_SQL_MAX_ROWS", 1000), timeout_seconds=_env_int("WBG_SQL_TIMEOUT_SECONDS", 60)))

    # Observability: local audit log always; hosted Logfire export only when configured (and installed).
    audit_log_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "WBG_AUDIT_LOG", PACKAGE_DIR / "runs" / "audit_events.jsonl")))
    logfire_token_present: bool = field(default_factory=lambda: bool(os.environ.get("LOGFIRE_TOKEN")))


def load_settings() -> Settings:
    return Settings()
