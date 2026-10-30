"""Offline tests of the POC modules: settings, credential handling, synthetic data and SQL composition.

No network, no Databricks CLI call and no database connection.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from lakebase_poc import credentials, pgvector_store, synthetic
from lakebase_poc.connection import connection_kwargs
from lakebase_poc.settings import SettingsError, live_tests_enabled, load_settings

VALID = {"LAKEBASE_HOST": "ep-test.database.us-east-2.cloud.databricks.com", "LAKEBASE_DATABASE": "databricks_postgres",
         "LAKEBASE_USER": "someone@example.com", "LAKEBASE_ENDPOINT": "projects/p/branches/b/endpoints/e"}


# ---------------------------------------------------------------------------------------------------- settings
def test_settings_come_from_the_environment_and_enforce_tls():
    settings = load_settings(VALID, env_file=None)
    assert (settings.sslmode, settings.port, settings.database) == ("require", 5432, "databricks_postgres")
    assert "someone@example.com" not in repr(settings) and "ep-test" not in repr(settings)


@pytest.mark.parametrize("change", [{"LAKEBASE_HOST": ""}, {"LAKEBASE_SSLMODE": "disable"}, {"LAKEBASE_SSLMODE": "prefer"},
                                    {"LAKEBASE_ENDPOINT": "primary"}])
def test_invalid_settings_are_refused_without_echoing_values(change):
    with pytest.raises(SettingsError) as error:
        load_settings({**VALID, **change}, env_file=None)
    assert "someone@example.com" not in str(error.value)


def test_environment_overrides_the_local_file(tmp_path: Path):
    env_file = tmp_path / "lakebase.local.env"
    env_file.write_text("# comment\nLAKEBASE_HOST=file-host\nLAKEBASE_DATABASE='db'\n")
    settings = load_settings({**VALID, "LAKEBASE_HOST": "env-host"}, env_file=env_file)
    assert settings.host == "env-host" and settings.database == "databricks_postgres"


def test_live_runs_need_explicit_opt_in():
    assert not live_tests_enabled({}) and not live_tests_enabled({"LAKEBASE_LIVE_TESTS": "true"})
    assert live_tests_enabled({"LAKEBASE_LIVE_TESTS": "1"})


def test_connection_uses_tls_timeout_and_the_given_password_only():
    kwargs = connection_kwargs(load_settings(VALID, env_file=None), password="short-lived-token")
    assert kwargs["sslmode"] == "require" and kwargs["connect_timeout"] == 30 and kwargs["password"] == "short-lived-token"


# ------------------------------------------------------------------------------------------------- credentials
class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_token_is_parsed_and_never_part_of_errors(monkeypatch):
    monkeypatch.setattr(credentials, "databricks_cli", lambda: "/bin/databricks")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, '{"token": "SECRET-TOKEN", "expire_time": "x"}'))
    assert credentials.generate_token("projects/p/branches/b/endpoints/e", "profile") == "SECRET-TOKEN"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(0, '{"unexpected": "SECRET-TOKEN"}'))
    with pytest.raises(credentials.CredentialError) as error:
        credentials.generate_token("projects/p/branches/b/endpoints/e", "profile")
    assert "SECRET-TOKEN" not in str(error.value)


def test_cli_failure_is_reported_without_output_dump(monkeypatch):
    monkeypatch.setattr(credentials, "databricks_cli", lambda: "/bin/databricks")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(1, "SECRET-TOKEN", "Error: not authorized\nmore"))
    with pytest.raises(credentials.CredentialError) as error:
        credentials.generate_token("projects/p/branches/b/endpoints/e", "profile")
    assert str(error.value) == "credential request failed: Error: not authorized"


# ---------------------------------------------------------------------------------------------- synthetic data
def test_synthetic_data_is_deterministic_and_unit_length():
    first, second = synthetic.items(), synthetic.items()
    assert len(first) == synthetic.ITEM_COUNT and first[0].embedding.shape == (synthetic.DIMENSIONS,)
    assert all(np.array_equal(a.embedding, b.embedding) for a, b in zip(first, second))
    assert np.allclose([np.linalg.norm(i.embedding) for i in first], 1.0, atol=1e-6)
    assert len({i.item_id for i in first}) == len(first)


def test_each_query_ranks_its_source_item_first():
    items = synthetic.items()
    for source, query in synthetic.queries(items):
        ranking = synthetic.cosine_distance_ranking(items, query, 10)
        assert ranking[0][0] == source and ranking[0][1] < ranking[1][1]
        assert all(a[1] <= b[1] for a, b in zip(ranking, ranking[1:]))


# ------------------------------------------------------------------------------------------------ SQL contract
def test_sql_uses_fixed_identifiers_and_standard_pgvector_only():
    source = Path(pgvector_store.__file__).read_text()
    assert pgvector_store.SCHEMA == "wbg_rag_poc" and "vector_cosine_ops" in source and "USING hnsw" in source
    assert source.count("CREATE EXTENSION") == 1 and "CREATE EXTENSION IF NOT EXISTS vector\"" in source  # never lakebase_vector
    assert "ON CONFLICT (item_id) DO NOTHING" in source and "IF NOT EXISTS" in source
    assert "DROP " not in source and "TRUNCATE" not in source and "DELETE " not in source
