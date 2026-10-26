"""Checks the local environment configuration and reports variable NAMES only, never values.

Values come from the process environment first, then from the project's .env file (read with python-dotenv without
changing os.environ). Nothing is printed except variable names; no connection is made.

Usage (from procurement-intelligence/):
    python -m app_config.check_env
Exit code 0 when the configuration is complete and safe, 1 otherwise.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"

REQUIRED = ("PORTKEY_API_KEY", "PORTKEY_CLAUDE_MODEL")
BOOLEAN_SETTINGS = ("WBG_ALLOW_DEV_OWNER_CREDENTIALS",)
# Portkey Model Catalog form: @<provider slug>/<model slug>
MODEL_CATALOG_FORMAT = re.compile(r"^@[\w.-]+/\S+$")
# Raw provider keys are held by Portkey integrations and must not be in the local .env file.
# The fallback Config id is deliberately absent until Grok is verified (docs/llm_resilience_architecture.md).
# WBG_ALLOW_LIVE_LLM_CALLS must be set per invocation in the process environment, never stored in .env.
NOT_ALLOWED_IN_ENV_FILE = ("ANTHROPIC_API_KEY", "XAI_API_KEY", "PORTKEY_CONFIG_ID", "WBG_ALLOW_LIVE_LLM_CALLS")


@dataclass
class EnvCheckReport:
    missing: list[str] = field(default_factory=list)       # required but empty or absent
    invalid: list[str] = field(default_factory=list)       # set, but not in the expected format (value not shown)
    not_allowed: list[str] = field(default_factory=list)   # present in .env although it must not be there

    @property
    def ok(self) -> bool:
        return not (self.missing or self.invalid or self.not_allowed)


def read_env_file(env_file: Path = ENV_FILE) -> dict[str, str]:
    if not env_file.exists():
        return {}
    return {name: value for name, value in dotenv_values(env_file).items() if value is not None}


def merged_settings(file_values: dict[str, str]) -> dict[str, str]:
    """Process environment overrides the .env file. Values stay in memory only."""
    names = set(file_values) | set(REQUIRED) | set(BOOLEAN_SETTINGS)
    return {name: os.environ.get(name, file_values.get(name, "")) for name in names}


def check(settings: dict[str, str], file_values: dict[str, str]) -> EnvCheckReport:
    report = EnvCheckReport()
    report.missing = [name for name in REQUIRED if not settings.get(name, "").strip()]

    model = settings.get("PORTKEY_CLAUDE_MODEL", "").strip()
    if model and not MODEL_CATALOG_FORMAT.match(model):
        report.invalid.append("PORTKEY_CLAUDE_MODEL")
    for name in BOOLEAN_SETTINGS:
        if settings.get(name, "false").strip().lower() not in ("true", "false"):
            report.invalid.append(name)

    report.not_allowed = [name for name in NOT_ALLOWED_IN_ENV_FILE if file_values.get(name, "").strip()]
    return report


def print_report(report: EnvCheckReport, env_file_found: bool) -> None:
    print(f".env file: {'found' if env_file_found else 'NOT FOUND (copy .env.example to .env)'}")
    print("Missing: " + (", ".join(report.missing) or "none"))
    print("Invalid format: " + (", ".join(report.invalid) or "none"))
    print("Must not be in .env: " + (", ".join(report.not_allowed) or "none"))
    print("Configuration OK" if report.ok else "Configuration INCOMPLETE")


def main() -> int:
    file_values = read_env_file()
    report = check(merged_settings(file_values), file_values)
    print_report(report, ENV_FILE.exists())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
