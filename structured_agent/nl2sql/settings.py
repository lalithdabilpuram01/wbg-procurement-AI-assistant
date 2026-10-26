"""Settings for the natural-language-to-SQL workflow.

Portkey values come from the process environment, then from the project's .env file (read without changing
os.environ), reusing the existing checker in app_config/check_env.py. Runtime limits are typed code defaults taken
from docs/llm_resilience_architecture.md; they are not environment variables.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app_config.check_env import check, merged_settings, read_env_file

PORTKEY_CHAT_COMPLETIONS_URL = "https://api.portkey.ai/v1/chat/completions"
APPROVED_CLAUDE_INTEGRATION = "@wbg-anthropic/"   # the only provider the Claude-only route may name


class ConfigurationError(Exception):
    """The Portkey configuration is missing or inconsistent. The message names variables, never values."""


@dataclass(frozen=True)
class ClaudeSettings:
    api_key: str = field(repr=False)        # never printed (repr disabled)
    model: str                              # Portkey Model Catalog form: @wbg-anthropic/<model slug>
    timeout_seconds: float = 45.0           # per LLM call (also sent to Portkey as x-portkey-request-timeout)
    max_output_tokens: int = 16000          # required by Anthropic; caps thinking + answer together (Sonnet 5.5 thinks by default)
    use_json_schema_output: bool = True     # structured outputs via response_format json_schema


@dataclass(frozen=True)
class BreakerSettings:
    failure_threshold: int
    window_seconds: float
    cooldown_seconds: float
    max_cooldown_seconds: float
    half_open_max_probes: int = 1
    success_threshold: int = 2


@dataclass(frozen=True)
class RuntimeSettings:
    """In-memory, single-process controls. Not sufficient for multi-instance production (see the resilience doc)."""

    max_llm_calls_per_request: int = 2          # one generation + at most one correction
    total_deadline_seconds: float = 90.0
    requests_per_minute: int = 30               # per principal
    llm_calls_per_minute: int = 10              # per principal
    databricks_executions_per_minute: int = 10  # per principal
    max_concurrent_llm_calls: int = 4
    max_concurrent_databricks_calls: int = 2
    concurrency_wait_seconds: float = 2.0
    max_tables_in_context: int = 3
    max_examples_in_context: int = 2
    llm_breaker: BreakerSettings = BreakerSettings(5, 60.0, 30.0, 300.0)
    databricks_breaker: BreakerSettings = BreakerSettings(3, 120.0, 60.0, 600.0)


def load_claude_settings() -> ClaudeSettings:
    """Read PORTKEY_API_KEY and PORTKEY_CLAUDE_MODEL; raise ConfigurationError naming any problem."""
    file_values = read_env_file()
    settings = merged_settings(file_values)
    report = check(settings, file_values)
    problems = []
    if report.missing:
        problems.append("missing: " + ", ".join(report.missing))
    if report.invalid:
        problems.append("invalid format: " + ", ".join(report.invalid))
    if report.not_allowed:
        problems.append("must not be in .env (Claude-only routing): " + ", ".join(report.not_allowed))
    if not report.missing and not is_approved_claude_model(settings["PORTKEY_CLAUDE_MODEL"]):
        problems.append(f"PORTKEY_CLAUDE_MODEL must name a Claude model of the {APPROVED_CLAUDE_INTEGRATION.rstrip('/')} integration")
    if problems:
        raise ConfigurationError("Portkey configuration incomplete - " + "; ".join(problems))
    return ClaudeSettings(api_key=settings["PORTKEY_API_KEY"].strip(), model=settings["PORTKEY_CLAUDE_MODEL"].strip())


def is_approved_claude_model(model: str) -> bool:
    """@wbg-anthropic/<claude model>; anything else (another provider, a Grok model) is refused."""
    model = model.strip().lower()
    name = model.removeprefix(APPROVED_CLAUDE_INTEGRATION)
    return model.startswith(APPROVED_CLAUDE_INTEGRATION) and name.startswith("claude") and "grok" not in name
