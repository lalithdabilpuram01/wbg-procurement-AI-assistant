"""Minimal data-quality check runner for Silver jobs.

A check is a name, a severity ("error" blocks any write; "warn" is reported only), the observed value,
the expected value or rule, and whether it passed. Checks are evaluated eagerly on Spark and collected
into a JSON-serialisable report.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CheckResult:
    name: str
    severity: str  # "error" | "warn"
    passed: bool
    observed: Any
    expected: Any
    detail: str = ""


@dataclass
class DQReport:
    target: str
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, observed: Any, expected: Any, passed: bool, severity: str = "error", detail: str = "") -> None:
        self.results.append(CheckResult(name, severity, bool(passed), observed, expected, detail))

    def equals(self, name: str, observed: Any, expected: Any, severity: str = "error", detail: str = "") -> None:
        self.add(name, observed, expected, observed == expected, severity, detail)

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == "error" and not r.passed]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == "warn" and not r.passed]

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"target": self.target, "passed": self.passed, "errors": len(self.errors), "warnings": len(self.warnings),
                "results": [asdict(r) for r in self.results]}

    def render(self) -> str:
        lines = [f"DQ report for {self.target}: {'PASSED' if self.passed else 'FAILED'} "
                 f"({len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.results)} checks)"]
        for r in self.results:
            mark = "ok  " if r.passed else ("FAIL" if r.severity == "error" else "warn")
            lines.append(f"  [{mark}] {r.name}: observed={r.observed} expected={r.expected}" + (f"  ({r.detail})" if r.detail else ""))
        return "\n".join(lines)
