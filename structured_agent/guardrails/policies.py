"""Deterministic guardrail policies (pure Python; no NeMo import, no model, no network).

Some run inside NeMo rail flows as custom actions (input policy, model-output policy, final-response policy); the
others are application gates outside NeMo (retrieved-context sanitising, tool authorization, execution). Credential-
request and secret-value patterns are NOT here: they are configured in the NeMo built-in regex rail (config.yml).

Patterns look for structure (an override verb AND an instructions/rules object; a personal-data noun; a request for
supplier identities), not single words, so ordinary procurement analysis is not blocked. They reduce risk; they do
not solve prompt injection, hallucination or semantic content safety.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from pydantic import ValidationError

from structured_agent.guardrails.decisions import GuardDecision, PolicyResult, ReasonCategory
from structured_agent.nl2sql.context import SchemaContext
from structured_agent.nl2sql.sql_generator import SqlDraft

MAX_QUESTION_CHARS = 1000
MIN_QUESTION_CHARS = 1        # any non-blank message: short chat such as "Hi" or "ok" is routed, not blocked
MAX_QUESTION_LINES = 8
MAX_SQL_CHARS = 20_000
APPROVED_TABLE = re.compile(r"^workspace\.wbg_(gold|silver)\.[a-z0-9_]+$")


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]


# Explicit attempts to replace the system's instructions or role.
INSTRUCTION_OVERRIDE = _compile([
    r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.?!\n]{0,40}\b(previous|prior|above|earlier|all|any|your|the|these|"
    r"system|developer|safety)\b[^.?!\n]{0,20}\b(instructions?|rules|prompts?|guardrails?|polic(y|ies)|restrictions|guidelines|"
    r"constraints|validation|validator)\b",
    r"\b(you are now|from now on,? you|pretend (to be|you are)|developer mode|jailbreak|dan mode)\b",
    r"\bact as (an? )?(admin|administrator|developer|system|root|superuser|unrestricted|jailbroken)\b",
    r"\b(reveal|show|print|repeat|output|display|tell me)\b[^.?!\n]{0,30}\b(your|the) (system|hidden|initial|original) "
    r"(prompt|instructions|message)\b",
    r"^\s*(system|assistant|developer)\s*:",
    r"\b(execute|run)\b[^.?!\n]{0,30}\b(without|bypassing|skipping)\b[^.?!\n]{0,20}\b(validation|validator|checks?|approval|guardrails?)\b",
])

# Explicit requests for data the registry classifies as prohibited (supplier identities, contact/personal data,
# masked notice text, raw records, re-identification). Aggregates such as "how many suppliers" stay allowed.
RESTRICTED_DATA_REQUEST = _compile([
    r"\b(names?|identit(y|ies))\b[^.?!\n]{0,40}\b(suppliers?|vendors?|contractors?|bidders?|consultants?|individuals?)\b",
    r"\b(suppliers?|vendors?|contractors?|bidders?|consultants?)['’]?s?\s+(names?|identit(y|ies))\b",
    r"\bwho (are|were|is|was)\b[^.?!\n]{0,30}\b(suppliers?|vendors?|contractors?|bidders?|consultants?)\b",
    r"\b(e-?mail( address(es)?)?s?|phone numbers?|telephone|contact (details|information|persons?)|home address(es)?|"
    r"personal (data|details|information)|passport|national id|bank (account|details))\b",
    r"\b(full|raw|complete|unmasked|unredacted)\b[^.?!\n]{0,20}\b(notice (text|body)|text of (the )?notices?|records?|descriptions?)\b",
    r"\bnotice[_ ]text\b|\bbronze\b|\bcontract descriptions?\b",
    r"\b(de-?anonymi[sz]e|re-?identify|unmask)\b",
])

# Instruction-like text inside retrieved metadata (registry descriptions, examples, future PDF excerpts).
CONTEXT_INSTRUCTION = INSTRUCTION_OVERRIDE + _compile([
    r"<\|?(system|im_start|im_end)\|?>|###\s*(instruction|system)",
    r"\b(grant|give|allow|enable)\b[^.\n]{0,30}\b(access|permissions?|privileges?|all tools|tool access)\b",
    r"\b(disable|skip|bypass|turn off|ignore)\b[^.\n]{0,30}\b(validation|validator|guardrails?|checks?|limits?|authori[sz]ation)\b",
    r"\b(call|invoke|use)\b[^.\n]{0,20}\b(execute_readonly_sql|validate_sql|search_table_registry|the \w+ tool)\b",
    r"\bexecute\b[^.\n]{0,40}\b(directly|immediately|without)\b",
    r"\byou (must|should|are required to)\b",
])

# Claims that something was executed or a result is known (the model never executes anything).
EXECUTION_CLAIM = _compile([
    r"\b(executed|ran)\b[^.\n]{0,30}\b(query|sql|statement)\b",
    r"\b(returned|yielded|found)\s+\d",
    r"\bthe (result|answer|total|count|number|value) (is|was|are|were)\b",
    r"\bthere (are|were)\s+\d",
])


def contains_instruction_like_text(text: str) -> bool:
    """True if retrieved or remembered text looks like an instruction (the CONTEXT_INSTRUCTION patterns)."""
    return _matches(CONTEXT_INSTRUCTION, text)


def _matches(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------------------------------- input
def evaluate_user_input(text: str) -> PolicyResult:
    """Size, format, instruction-override and restricted-data checks on the user's question."""
    stripped = text.strip()
    if not MIN_QUESTION_CHARS <= len(stripped) <= MAX_QUESTION_CHARS:
        return PolicyResult.block(ReasonCategory.INVALID_SIZE)
    if stripped.count("\n") >= MAX_QUESTION_LINES or _has_hidden_or_control_characters(stripped):
        return PolicyResult.block(ReasonCategory.INVALID_FORMAT)
    if _matches(INSTRUCTION_OVERRIDE, stripped):
        return PolicyResult.block(ReasonCategory.INSTRUCTION_OVERRIDE)
    if _matches(RESTRICTED_DATA_REQUEST, stripped):
        return PolicyResult.block(ReasonCategory.RESTRICTED_DATA_REQUEST)
    return PolicyResult.allow()


def _has_hidden_or_control_characters(text: str) -> bool:
    """Control characters (other than newline/tab) or invisible format characters often used to hide instructions."""
    return any((unicodedata.category(ch) == "Cc" and ch not in "\n\t") or unicodedata.category(ch) == "Cf" for ch in text)


# ---------------------------------------------------------------------------------------------- retrieved context
def sanitize_retrieved_context(context: SchemaContext) -> tuple[SchemaContext, PolicyResult, int]:
    """Treat registry metadata as untrusted data: drop unapproved tables and any instruction-like text.

    Returns the sanitised context, the policy result and how many text items were removed. Removing text never grants
    anything: permissions, identity and execution rules are enforced by the MCP tools and the SQL validator.
    """
    tables = [t for t in context.tables if APPROVED_TABLE.match(t.object_fqn)]
    if not tables:
        return context, PolicyResult.block(ReasonCategory.UNAPPROVED_METADATA), 0
    approved = {t.object_fqn for t in tables}
    removed = len(context.tables) - len(tables)

    clean_tables = []
    for table in tables:
        columns = []
        for column in table.columns:
            if column.description and _matches(CONTEXT_INSTRUCTION, column.description):
                column, removed = column.model_copy(update={"description": None}), removed + 1
            columns.append(column)
        limitations = [n for n in table.limitations if not _matches(CONTEXT_INSTRUCTION, n)]
        removed += len(table.limitations) - len(limitations)
        clean_tables.append(table.model_copy(update={"columns": columns, "limitations": limitations}))

    joins = [j for j in context.joins if not _matches(CONTEXT_INSTRUCTION, j.aggregation_rule + " " + " ".join(j.required_conditions))]
    metrics = [m for m in context.metrics if m.object_fqn in approved
               and not _matches(CONTEXT_INSTRUCTION, " ".join([m.name, m.expression, *m.caveats]))]
    examples = [e for e in context.examples
                if not _matches(CONTEXT_INSTRUCTION, " ".join([e.question, e.sql, json.dumps(e.parameters)]))
                and set(re.findall(r"workspace\.wbg_\w+\.\w+", e.sql)) <= approved]
    removed += (len(context.joins) - len(joins)) + (len(context.metrics) - len(metrics)) + (len(context.examples) - len(examples))

    clean = context.model_copy(update={"tables": clean_tables, "joins": joins, "metrics": metrics, "examples": examples})
    reason = ReasonCategory.CONTEXT_INSTRUCTION_REMOVED if removed else None
    return clean, PolicyResult.allow(reason), removed


# --------------------------------------------------------------------------------------------------- model output
def evaluate_model_output(draft_json: str, context_tables: Iterable[str]) -> PolicyResult:
    """The model's reply must be a valid SQL draft over the given tables, with no execution or result claims."""
    try:
        draft = SqlDraft.model_validate(json.loads(draft_json))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return PolicyResult.block(ReasonCategory.INVALID_MODEL_OUTPUT)
    if _matches(EXECUTION_CLAIM, draft.explanation):
        return PolicyResult.block(ReasonCategory.UNSUPPORTED_EXECUTION_CLAIM)
    if not draft.answerable:
        return PolicyResult.allow()
    sql = draft.sql.strip()
    if not sql or len(sql) > MAX_SQL_CHARS or not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        return PolicyResult.block(ReasonCategory.INVALID_MODEL_OUTPUT)
    allowed_tables = {t.lower() for t in context_tables}
    if any(t.lower() not in allowed_tables for t in draft.tables_used):
        return PolicyResult.block(ReasonCategory.FABRICATED_TABLE_REFERENCE)
    return PolicyResult.allow()


# ----------------------------------------------------------------------------------------------------- execution
def evaluate_execution(trusted_principal: str, current_principal: str, validation_approved: bool,
                       validation_id: str | None, prior_decisions: Iterable[GuardDecision]) -> PolicyResult:
    """Execution only for the identity fixed at request start, after an approved validation and no earlier block."""
    if current_principal != trusted_principal:
        return PolicyResult.block(ReasonCategory.IDENTITY_MISMATCH)
    if not validation_approved or not validation_id:
        return PolicyResult.block(ReasonCategory.VALIDATION_NOT_APPROVED)
    if any(not d.allowed for d in prior_decisions):
        return PolicyResult.block(ReasonCategory.PRIOR_POLICY_FAILURE)
    return PolicyResult.allow()


# ------------------------------------------------------------------------------------------------ final response
def evaluate_final_response(response_json: str) -> PolicyResult:
    """The response must be internally consistent: only executed results may carry rows or execution claims."""
    try:
        response: dict[str, Any] = json.loads(response_json)
    except json.JSONDecodeError:
        return PolicyResult.block(ReasonCategory.INVALID_MODEL_OUTPUT)
    status = response.get("status")
    validation = response.get("validation") or {}
    executed = response.get("executed_sql")
    if status == "answered" and not (validation.get("approved") and executed and response.get("provenance")):
        return PolicyResult.block(ReasonCategory.INCONSISTENT_RESULT)
    if executed and (status != "answered" or executed != validation.get("validated_sql")):
        return PolicyResult.block(ReasonCategory.INCONSISTENT_RESULT)
    if status != "answered" and (response.get("rows") or response.get("row_count")):
        return PolicyResult.block(ReasonCategory.INCONSISTENT_RESULT)
    if status != "answered" and _matches(EXECUTION_CLAIM, response.get("message") or ""):
        return PolicyResult.block(ReasonCategory.UNSUPPORTED_EXECUTION_CLAIM)
    if _matches(EXECUTION_CLAIM, response.get("model_description_unverified") or ""):
        return PolicyResult.block(ReasonCategory.UNSUPPORTED_EXECUTION_CLAIM)
    return PolicyResult.allow()
