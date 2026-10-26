"""Short-lived Lakebase OAuth credential, generated at run time through the Databricks CLI.

The CLI uses the existing user login of the configured profile (OAuth U2M; no secret in ~/.databrickscfg). The token
is returned to the caller only and is never printed, logged, written to disk or included in an exception message.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

CLI_ENV = "DATABRICKS_CLI"
VSCODE_CLI_GLOB = str(Path.home() / ".vscode/extensions/databricks.databricks-*/bin/databricks")
CLI_TIMEOUT_SECONDS = 60


class CredentialError(RuntimeError):
    """The credential could not be generated (message contains no token)."""


def databricks_cli() -> str:
    """$DATABRICKS_CLI, else `databricks` on PATH, else the CLI bundled with the VS Code Databricks extension."""
    candidates = [os.environ.get(CLI_ENV), shutil.which("databricks"), *sorted(glob.glob(VSCODE_CLI_GLOB), reverse=True)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise CredentialError("Databricks CLI not found; set DATABRICKS_CLI")


def generate_token(endpoint: str, profile: str) -> str:
    command = [databricks_cli(), "postgres", "generate-database-credential", endpoint, "-p", profile, "-o", "json"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        raise CredentialError("credential request timed out") from None
    if completed.returncode != 0:
        first_line = (completed.stderr.strip().splitlines() or ["unknown error"])[0][:200]
        raise CredentialError(f"credential request failed: {first_line}")
    try:
        token = json.loads(completed.stdout)["token"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise CredentialError("credential response did not contain a token") from None
    if not isinstance(token, str) or not token:
        raise CredentialError("credential response contained an empty token")
    return token
