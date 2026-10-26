"""Databricks Connect session for Silver jobs (profile + Serverless compute).

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


def get_spark(profile: str = DEFAULT_PROFILE):
    """Serverless Spark Connect session for the given CLI profile."""
    if ensure_databricks_cli_on_path() is None:
        raise RuntimeError("Databricks CLI not found: install it or the Databricks VS Code extension "
                           "(auth_type=databricks-cli needs it to obtain tokens)")
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.profile(profile).serverless(True).getOrCreate()
