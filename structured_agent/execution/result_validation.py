"""Checks on a query result before it is returned.

The validator already decided what may be computed; these checks confirm that what came back matches that decision
and contains nothing that must not be disclosed.
"""
from __future__ import annotations

import re

from structured_agent.contracts import CheckResult, ValidationReport
from structured_agent.execution.executor import RawResult

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+|\b00)\d[\d\s().-]{7,}\d")
DISCLOSABLE = {"public", "lineage"}


def validate_result(raw: RawResult, report: ValidationReport, row_limit: int) -> list[CheckResult]:
    expected = [c.name.lower() for c in report.output_columns]
    returned = [name.lower() for name, _ in raw.columns]
    checks = [
        CheckResult(name="columns_match_validated_query", passed=returned == expected,
                    detail="" if returned == expected else f"expected {expected}, got {returned}"),
        CheckResult(name="row_limit_respected", passed=len(raw.rows) <= row_limit, detail=f"{len(raw.rows)} <= {row_limit}"),
        _classification_check(report),
        _non_negative_counts(raw, report),
        _no_contact_details(raw),
    ]
    return checks


def _classification_check(report: ValidationReport) -> CheckResult:
    blocked = [c.name for c in report.output_columns if c.classification not in DISCLOSABLE]
    return CheckResult(name="output_classification_disclosable", passed=not blocked,
                       detail=f"blocked: {blocked}" if blocked else "all outputs public or lineage")


def _non_negative_counts(raw: RawResult, report: ValidationReport) -> CheckResult:
    count_columns = [c.name for c in report.output_columns if c.unit == "count"]
    negative = [c for c in count_columns for row in raw.rows if isinstance(row.get(c), (int, float)) and row[c] < 0]
    return CheckResult(name="counts_non_negative", passed=not negative, detail=f"negative: {sorted(set(negative))}" if negative else "")


def _no_contact_details(raw: RawResult) -> CheckResult:
    """Defence in depth: no e-mail address or phone number may appear in any returned string."""
    hits = sum(1 for row in raw.rows for v in row.values() if isinstance(v, str) and (EMAIL.search(v) or PHONE.search(v)))
    return CheckResult(name="no_contact_details_in_values", passed=hits == 0, detail=f"{hits} value(s) matched" if hits else "")
