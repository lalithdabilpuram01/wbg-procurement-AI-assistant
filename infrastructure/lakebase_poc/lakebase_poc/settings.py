"""Connection settings, read from the environment (optionally pre-loaded from a git-ignored local file).

Nothing secret is configured here: the password is a short-lived OAuth credential generated at run time
(see credentials.py). Host, user, database and endpoint are deployment settings and live outside the code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
LOCAL_ENV_FILE = PROJECT_DIR / "lakebase.local.env"        # git-ignored; see lakebase.env.example
REQUIRED = ("LAKEBASE_HOST", "LAKEBASE_DATABASE", "LAKEBASE_USER", "LAKEBASE_ENDPOINT")
LIVE_OPT_IN = "LAKEBASE_LIVE_TESTS"


class SettingsError(RuntimeError):
    """A required setting is missing or invalid (the message never contains values)."""


@dataclass(frozen=True)
class LakebaseSettings:
    host: str
    database: str
    user: str
    endpoint: str                          # projects/<project>/branches/<branch>/endpoints/<endpoint>
    databricks_profile: str = "wbg-databricks-free"
    port: int = 5432
    sslmode: str = "require"
    connect_timeout_seconds: int = 30      # allows for a scale-to-zero cold start
    statement_timeout_ms: int = 30_000

    def __repr__(self) -> str:             # keep identities out of logs and tracebacks
        return f"LakebaseSettings(endpoint={self.endpoint!r}, database={self.database!r}, sslmode={self.sslmode!r})"


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments and blank lines ignored; no shell expansion)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(environ: dict[str, str] | None = None, env_file: Path | None = LOCAL_ENV_FILE) -> LakebaseSettings:
    """Process environment wins over the local file."""
    values = read_env_file(env_file) if env_file is not None and env_file.is_file() else {}
    values.update(environ if environ is not None else os.environ)
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise SettingsError(f"missing Lakebase settings: {', '.join(missing)}")
    if values.get("LAKEBASE_SSLMODE", "require") not in ("require", "verify-ca", "verify-full"):
        raise SettingsError("LAKEBASE_SSLMODE must enforce TLS (require, verify-ca or verify-full)")
    if not values["LAKEBASE_ENDPOINT"].startswith("projects/") or "/endpoints/" not in values["LAKEBASE_ENDPOINT"]:
        raise SettingsError("LAKEBASE_ENDPOINT must be a resource name projects/<p>/branches/<b>/endpoints/<e>")
    return LakebaseSettings(host=values["LAKEBASE_HOST"], database=values["LAKEBASE_DATABASE"], user=values["LAKEBASE_USER"],
                            endpoint=values["LAKEBASE_ENDPOINT"],
                            databricks_profile=values.get("LAKEBASE_DATABRICKS_PROFILE", "wbg-databricks-free"),
                            port=int(values.get("LAKEBASE_PORT", "5432")),
                            sslmode=values.get("LAKEBASE_SSLMODE", "require"))


def live_tests_enabled(environ: dict[str, str] | None = None) -> bool:
    return (environ if environ is not None else os.environ).get(LIVE_OPT_IN, "").strip() == "1"
