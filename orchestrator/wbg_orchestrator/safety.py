"""Process-level safety switches: LangSmith / LangChain tracing is always off (it would export full inputs and outputs).

`disable_external_tracing()` sets every LangSmith and LangChain tracing flag to false; `no_external_tracing()` also
wraps each graph run in langsmith's own `tracing_context(enabled=False)`, which overrides the environment.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

TRACING_FLAGS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2")


class ExternalTracingEnabledError(RuntimeError):
    """External tracing would export investigation content."""


def disable_external_tracing() -> None:
    """Turn every LangSmith / LangChain tracing flag off for this process and opt out of NeMo usage stats."""
    for flag in TRACING_FLAGS:
        os.environ[flag] = "false"
    os.environ.setdefault("NEMO_GUARDRAILS_NO_USAGE_STATS", "1")


def external_tracing_enabled() -> bool:
    """Return True if LangSmith reports tracing as on; False when langsmith is not installed."""
    try:
        from langsmith.utils import tracing_is_enabled
    except ImportError:
        return False
    return bool(tracing_is_enabled())


@contextmanager
def no_external_tracing() -> Iterator[None]:
    """Run the wrapped block with tracing forced off, and refuse to run if LangSmith still reports it on."""
    disable_external_tracing()
    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:
        yield
        return
    with tracing_context(enabled=False):
        if external_tracing_enabled():
            raise ExternalTracingEnabledError("LangSmith tracing is enabled; refusing to run")
        yield
