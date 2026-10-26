"""What may be persisted: secret-shaped values are redacted, and results keep metadata only (no rows).

Secret-value patterns are read from the NeMo guardrails configuration (regex_detection.output.patterns), so there is
one list of patterns for both the guardrails and the conversation store.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from conversation.models import EvidenceReference, TurnMetadata

NEMO_CONFIG = Path(__file__).resolve().parents[1] / "structured_agent" / "guardrails" / "nemo_config" / "config.yml"
REDACTED = "[REDACTED]"
MAX_STORED_TEXT_CHARS = 4000


@lru_cache(maxsize=1)
def secret_value_patterns() -> tuple[re.Pattern, ...]:
    config = yaml.safe_load(NEMO_CONFIG.read_text())
    patterns = config["rails"]["config"]["regex_detection"]["output"]["patterns"]
    return tuple(re.compile(p) for p in patterns)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped values; truncate very long texts."""
    for pattern in secret_value_patterns():
        text = pattern.sub(REDACTED, text)
    return text[:MAX_STORED_TEXT_CHARS]


def result_metadata(result: Any, route: str = "structured") -> TurnMetadata:
    """Safe metadata of a WorkflowResult: status, validated SQL and source tables. Rows and columns are never kept."""
    executed = bool(getattr(result, "executed_sql", None))
    validation = getattr(result, "validation", None)
    evidence = []
    if executed and validation is not None and validation.validation_id:
        evidence.append(EvidenceReference(
            source_type="structured_result", source_id=validation.validation_id, objects=list(result.source_objects),
            row_count=result.row_count, verified_by="sqlglot_validator+execution_gateway"))
    return TurnMetadata(
        status=result.status.value, failure_class=result.failure_class.value if result.failure_class else None,
        route=route, guardrail_reason=result.guardrail_reason, validated_sql=result.validated_sql, executed=executed,
        row_count=result.row_count if executed else None, source_objects=list(result.source_objects or result.context_tables),
        evidence=evidence, clarification_options=list(result.clarification_options))
