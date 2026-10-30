"""Spark session for Silver and Gold jobs: Databricks Connect (profile + Serverless) from a laptop, or the job's own
session when the code runs inside Databricks (the Lakeflow ETL job, etl/).

The `wbg-databricks-free` profile authenticates with `auth_type = databricks-cli`, which needs the
`databricks` CLI on PATH. When it is not installed globally, the copy bundled with the Databricks
VS Code extension is used (added to PATH for this process only).
"""
from __future__ import annotations

import glob
import os
import shutil

DEFAULT_PROFILE = os.environ.get("WBG_DATABRICKS_PROFILE", "wbg-databricks-free")


def ensure_databricks_cli_on_path() -> str | None:
    """Return the CLI path, adding the VS Code extension's bundled CLI to PATH if needed."""
    found = shutil.which("databricks")
    if found:
        return found
    candidates = sorted(glob.glob(os.path.expanduser("~/.vscode/extensions/databricks.databricks-*/bin/databricks")))
    if candidates:
        os.environ["PATH"] = os.path.dirname(candidates[-1]) + os.pathsep + os.environ.get("PATH", "")
        return candidates[-1]
    return None


def in_databricks() -> bool:
    """True inside a Databricks job or notebook (the runtime sets this variable; a laptop never does)."""
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


def get_spark(profile: str = DEFAULT_PROFILE):
    """The job's own Spark session inside Databricks; otherwise a Serverless Spark Connect session for the CLI profile."""
    if in_databricks():
        from pyspark.sql import SparkSession
        return SparkSession.builder.getOrCreate()
    if ensure_databricks_cli_on_path() is None:
        raise RuntimeError("Databricks CLI not found: install it or the Databricks VS Code extension "
                           "(auth_type=databricks-cli needs it to obtain tokens)")
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.profile(profile).serverless(True).getOrCreate()
