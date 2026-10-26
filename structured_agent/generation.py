"""Provider-independent SQL generation.

* `TemplateSqlGenerator` (deterministic, NOT an LLM) fills a reviewed SQL template from the registry.
* `LlmSqlGenerator` builds a sanitised, schema-only prompt (selected candidate tables, authorized columns, approved
  joins, metric definitions, reviewed examples; never data values) and calls an `LlmProvider`.
* Provider adapters (Claude / Portkey-routed models) are disabled unless explicitly configured: no provider is
  configured in this environment, so they raise `ProviderNotConfigured` and nothing is sent anywhere.
* `generate_with_correction` allows at most ONE correction, only for recoverable validation errors, and the corrected
  SQL is validated again. Security and authorization failures are never retried, and policy is never broadened.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from structured_agent.contracts import ParameterValue, TableCandidate, ToolError, ValidationReport
from structured_agent.policy import Principal
from structured_agent.registry.models import Metric, VerifiedSqlExample
from structured_agent.sql_validation.validator import SqlValidator


class ProviderNotConfigured(RuntimeError):
    pass


@dataclass
class GeneratedSql:
    sql: str
    parameters: dict[str, ParameterValue]
    generator: str
    is_llm: bool
    example_id: str | None = None
    notes: list[str] = field(default_factory=list)


class SchemaContext(BaseModel):
    """Everything a generator may see: schema metadata of the selected candidates only, no data values."""

    question: str
    candidates: list[TableCandidate]
    column_types: dict[str, dict[str, str]]       # object -> {authorized column: type}
    metrics: list[Metric]
    examples: list[VerifiedSqlExample]


class SqlGenerator(Protocol):
    name: str
    is_llm: bool

    def generate(self, context: SchemaContext, feedback: list[ToolError] | None = None) -> GeneratedSql: ...


# ------------------------------------------------------------------------------------------------- deterministic path
class TemplateSqlGenerator:
    """Uses a reviewed SQL template; the caller supplies parameter values. No language model is involved."""

    name = "template (deterministic; no LLM)"
    is_llm = False

    def __init__(self, example_id: str, parameters: dict[str, ParameterValue] | None = None):
        self._example_id = example_id
        self._parameters = parameters

    def generate(self, context: SchemaContext, feedback: list[ToolError] | None = None) -> GeneratedSql:
        example = next((e for e in context.examples if e.example_id == self._example_id), None)
        if example is None:
            raise LookupError(f"No reviewed SQL template '{self._example_id}' for the selected tables")
        parameters = example.parameters if self._parameters is None else self._parameters
        return GeneratedSql(sql=example.sql_text, parameters=parameters, generator=self.name, is_llm=False,
                            example_id=example.example_id, notes=[f"template status: {example.verification_status}"])


# ---------------------------------------------------------------------------------------------------------- LLM path
class LlmProvider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


SYSTEM_PROMPT = """You write one Databricks SQL SELECT statement for a question, using ONLY the tables, columns,
joins and metric definitions provided. Rules: fully qualified table names; explicit column lists (no *); user values
as :named parameters; join only on the approved joins listed; aggregate each fact table to the join key before joining
facts that share a parent; never add contract values to financing amounts; LIMIT <= 1000.
Reply with JSON: {"sql": "...", "parameters": {"name": value}}"""


def build_prompt(context: SchemaContext, feedback: list[ToolError] | None) -> str:
    lines = [f"Question: {context.question}", "", "Tables:"]
    for candidate in context.candidates:
        types = context.column_types.get(candidate.object_fqn, {})
        lines.append(f"- {candidate.object_fqn} (grain: {candidate.grain}): " +
                     ", ".join(f"{c} {types.get(c, '')}".strip() for c in candidate.authorized_columns))
        for join in candidate.approved_joins:
            lines.append(f"  join: {join.from_object}.{join.from_columns} = {join.to_object}.{join.to_columns} "
                         f"({join.cardinality}); {join.aggregation_rule}")
    lines += ["", "Metrics:"] + [f"- {m.metric_id}: {m.expression} on {m.object_fqn} ({m.unit})" for m in context.metrics]
    lines += ["", "Reviewed examples:"] + [f"- {e.question}: {e.sql_text}" for e in context.examples[:3]]
    if feedback:
        lines += ["", "The previous SQL was rejected. Fix only these problems:"] + [f"- {e.code.value}: {e.message}" for e in feedback]
    return "\n".join(lines)


class LlmSqlGenerator:
    is_llm = True

    def __init__(self, provider: LlmProvider):
        self._provider = provider
        self.name = f"llm ({provider.name})"

    def generate(self, context: SchemaContext, feedback: list[ToolError] | None = None) -> GeneratedSql:
        reply = self._provider.complete(SYSTEM_PROMPT, build_prompt(context, feedback))
        match = re.search(r"\{.*\}", reply, re.S)
        payload = json.loads(match.group(0)) if match else {}
        return GeneratedSql(sql=payload.get("sql", ""), parameters=payload.get("parameters", {}), generator=self.name, is_llm=True)


class ClaudeProvider:
    """Anthropic Claude via the installed SDK. Requires ANTHROPIC_API_KEY and WBG_ALLOW_EXTERNAL_LLM=true."""

    name = "anthropic-claude"

    def __init__(self, model: str = "claude-opus-5-5"):
        if os.environ.get("WBG_ALLOW_EXTERNAL_LLM", "").lower() != "true" or not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderNotConfigured("Claude is not approved/configured (WBG_ALLOW_EXTERNAL_LLM and ANTHROPIC_API_KEY)")
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def complete(self, system: str, user: str) -> str:
        message = self._client.messages.create(model=self._model, max_tokens=1200, system=system,
                                               messages=[{"role": "user", "content": user}])
        return "".join(block.text for block in message.content if block.type == "text")


class PortkeyProvider:
    """Portkey gateway (e.g. routing to Grok). The portkey-ai SDK is not installed in this environment."""

    name = "portkey"

    def __init__(self, virtual_key_env: str = "PORTKEY_VIRTUAL_KEY"):
        if os.environ.get("WBG_ALLOW_EXTERNAL_LLM", "").lower() != "true" or not os.environ.get("PORTKEY_API_KEY"):
            raise ProviderNotConfigured("Portkey is not approved/configured (WBG_ALLOW_EXTERNAL_LLM and PORTKEY_API_KEY)")
        raise ProviderNotConfigured("The portkey-ai SDK is not installed; installation needs approval")

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - unreachable until configured
        raise ProviderNotConfigured("Portkey provider is not available")


# ------------------------------------------------------------------------------------------------ bounded correction
@dataclass
class GenerationOutcome:
    attempts: list[tuple[GeneratedSql, ValidationReport]]

    @property
    def final(self) -> tuple[GeneratedSql, ValidationReport]:
        return self.attempts[-1]


def generate_with_correction(generator: SqlGenerator, context: SchemaContext, validator: SqlValidator,
                             principal: Principal) -> GenerationOutcome:
    generated = generator.generate(context)
    report = validator.validate(generated.sql, generated.parameters, principal)
    attempts = [(generated, report)]
    if report.approved or not report.errors or not all(e.recoverable for e in report.errors):
        return GenerationOutcome(attempts)
    corrected = generator.generate(context, feedback=report.errors)       # the one bounded correction
    attempts.append((corrected, validator.validate(corrected.sql, corrected.parameters, principal)))
    return GenerationOutcome(attempts)
