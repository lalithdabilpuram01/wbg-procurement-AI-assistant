"""The Databricks CLI on this laptop (on PATH, or the copy in the Databricks VS Code extension) and a workspace client
that signs in through it (the profile's `databricks-cli` auth)."""
from __future__ import annotations

import shutil
from pathlib import Path


def cli_path() -> str:
    """Path of the Databricks CLI: the one on PATH, else a copy bundled with the Databricks VS Code extension."""
    found = shutil.which("databricks") or next(iter(sorted(Path.home().glob(".vscode/extensions/databricks.databricks-*/bin/databricks"))), None)
    if not found:
        raise SystemExit("the Databricks CLI was not found: install it, or the Databricks VS Code extension")
    return str(found)


def workspace(profile: str):
    """A WorkspaceClient for `profile` that signs in through the Databricks CLI."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    return WorkspaceClient(config=Config(profile=profile, databricks_cli_path=cli_path()))
