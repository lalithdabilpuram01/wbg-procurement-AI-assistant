"""Live evaluation of the Structured Data Agent on the 15 scenarios (cases.py).

Every agent step goes through StructuredDataService (the same path as the MCP tools): registry search, reviewed
template retrieval, SQL validation and gateway execution. Baselines are separate, trusted read-only SELECTs run by
this harness (mostly over Silver) to check the executed results. Nothing is written to Databricks.

Requires labelled development mode, because the only Databricks credential is write-capable:
    WBG_ALLOW_DEV_OWNER_CREDENTIALS=true /opt/anaconda3/bin/python3 -m structured_agent.evaluation.run_evaluation
Outputs: structured_agent/evaluation/runs/<UTC>/results.json and EVALUATION_RESULTS.md, plus
         structured_agent/metadata/verification/sql_examples_verification_<UTC>.json
"""
from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from silver.common.session import get_spark
from structured_agent.config import PACKAGE_DIR, load_settings
from structured_agent.evaluation.cases import CASES, Attempt, Case
from structured_agent.execution.executor import to_json_value
from structured_agent.generation import SchemaContext, TemplateSqlGenerator
from structured_agent.metadata.sources import VERIFICATION_DIR
from structured_agent.service import RequestContext, StructuredDataService

WORKSPACE = "wbg-procurement-poc"
RUNS_DIR = PACKAGE_DIR / "evaluation" / "runs"


# ------------------------------------------------------------------------------------------------------ comparisons
def _norm(value):
    """Normalise a value for comparison: numbers as exact Decimals (so 1.0 equals 1), anything else as text."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation:
        return str(value)


def compare_rows(agent_rows: list[dict], baseline_rows: list[dict], key: str, fields: list[str]) -> tuple[bool, str]:
    """Compare two result sets keyed by `key` on the given fields; returns (match, readable detail)."""
    agent = {_norm(r[key]): {f: _norm(r[f]) for f in fields} for r in agent_rows}
    baseline = {_norm(r[key]): {f: _norm(r[f]) for f in fields} for r in baseline_rows}
    if agent.keys() != baseline.keys():
        return False, f"keys differ: agent-only {sorted(map(str, agent.keys() - baseline.keys()))[:5]}, baseline-only {sorted(map(str, baseline.keys() - agent.keys()))[:5]}"
    differences = [k for k in agent if agent[k] != baseline[k]]
    if differences:
        k = differences[0]
        return False, f"{len(differences)} row(s) differ, e.g. {k}: agent {agent[k]} vs baseline {baseline[k]}"
    return True, f"{len(agent)} row(s) identical on {key} + {fields}"


def compare(spec: str, agent_rows: list[dict], baseline_rows: list[dict]) -> tuple[bool, str]:
    """Run the comparison named by `spec` (e.g. 'same_rows:key:field1,field2'); returns (match, detail)."""
    kind, _, rest = spec.partition(":")
    if kind == "same_rows":
        key, _, fields = rest.partition(":")
        return compare_rows(agent_rows, baseline_rows, key, fields.split(","))
    if kind == "same_single_row":
        fields = rest.split(",")
        rows = [{"k": 1, **{f: r[f] for f in fields}} for r in agent_rows]
        return compare_rows(rows, [{"k": 1, **{f: r[f] for f in fields}} for r in baseline_rows], "k", fields)
    if kind == "contract_lookup":
        a, b = agent_rows[0], baseline_rows[0]
        ok = _norm(a["contract_amount_usd"]) == _norm(b["contract_amount_usd"]) == _norm(b["lines_total"]) \
            and a["procurement_method"] == b["procurement_method"]
        return ok, f"amount {a['contract_amount_usd']} vs Silver {b['contract_amount_usd']} vs sum of supplier lines {b['lines_total']}"
    raise ValueError(f"Unknown comparison {spec}")


# ------------------------------------------------------------------------------------------------------- execution
class Evaluation:
    """Runs the cases through StructuredDataService and checks executed results against trusted baselines."""

    def __init__(self):
        """Load settings, refuse to run without the dev-owner opt-in, and open the service and a Spark session."""
        self.settings = load_settings()
        if not self.settings.allow_dev_owner_credentials:
            raise SystemExit("Set WBG_ALLOW_DEV_OWNER_CREDENTIALS=true: the only Databricks credential is write-capable")
        self.service = StructuredDataService.from_environment(self.settings)
        self.spark = get_spark(self.settings.databricks_profile)
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def baseline(self, sql: str) -> list[dict]:
        return [{k: to_json_value(v) for k, v in row.asDict().items()} for row in self.spark.sql(sql).collect()]

    def service_for(self, attempt: Attempt) -> StructuredDataService:
        """The shared service, or a fresh one with this attempt's timeout override."""
        if attempt.timeout_seconds is None:
            return self.service
        limits = dataclasses.replace(self.settings.limits, timeout_seconds=attempt.timeout_seconds)
        return StructuredDataService.from_environment(dataclasses.replace(self.settings, limits=limits))

    def run_case(self, case: Case) -> dict:
        """Run discovery and every attempt of one case; returns the case result as a dict."""
        ctx = RequestContext(case.principal, WORKSPACE, f"eval_{self.run_id}_{case.case_id}")
        started = time.perf_counter()
        search = self.service.search_table_registry(ctx, query=case.question, top_k=5)
        top3 = [c.object_fqn for c in search.candidates[:3]]
        discovery = {
            "status": search.status, "top_objects": [c.object_fqn for c in search.candidates],
            "top_metrics": [m.metric_id for m in search.metrics[:3]], "expected_any": case.expected_objects,
            "hit_in_top3": any(o in top3 for o in case.expected_objects),
            "ambiguity": search.ambiguity.model_dump() if search.ambiguity else None,
            "reasons_top1": search.candidates[0].reasons if search.candidates else [], "ms": search.duration_ms}
        attempts = [self.run_attempt(case, attempt, ctx) for attempt in case.attempts]
        if case.case_id == "T12":
            discovery["note"] = "Delta versions recorded before/after in the attempt results"
        passed = all(a["passed"] for a in attempts) and (search.status == "needs_clarification") == case.expect_ambiguity
        return {"case_id": case.case_id, "title": case.title, "question": case.question, "principal": case.principal,
                "discovery": discovery, "attempts": attempts, "passed": passed,
                "total_ms": round(1000 * (time.perf_counter() - started))}

    def run_attempt(self, case: Case, attempt: Attempt, ctx: RequestContext) -> dict:
        """Validate (and, if asked, execute) one attempt's SQL and compare executed rows with the baseline."""
        if attempt.principal:
            ctx = RequestContext(attempt.principal, WORKSPACE, ctx.request_id)
        service = self.service_for(attempt)
        sql, parameters, source = self.sql_for(attempt, ctx, case)

        validation = service.validate_sql(ctx, sql=sql, parameters=parameters)
        report = validation.report
        result = {"label": attempt.label, "sql_source": source, "sql": sql, "parameters": parameters,
                  "expected": attempt.expect,
                  "validation": {"approved": report.approved if report else False, "ms": validation.duration_ms,
                                 "error_codes": [e.code.value for e in validation.errors],
                                 "messages": [e.message for e in validation.errors][:3],
                                 "recoverable": [e.recoverable for e in validation.errors],
                                 "joins": report.joins if report else [],
                                 "checks_failed": [c.name for c in report.checks if not c.passed] if report else []}}

        if attempt.measure_sql:  # quantify what the rejected query would have reported (harness read only)
            result["harm_measurement"] = self.baseline(attempt.measure_sql)[0]

        if attempt.execute:
            # Case T12 records Delta table versions before and after, to prove execution wrote nothing.
            versions_before = self.delta_versions() if case.case_id == "T12" else None
            executed = service.execute_readonly_sql(ctx, sql=sql, parameters=parameters)
            result["execution"] = {
                "status": executed.status, "ms": executed.duration_ms, "error_codes": [e.code.value for e in executed.errors],
                "row_count": executed.row_count, "truncated": executed.truncated, "rows_preview": executed.rows[:5],
                "columns": [c.model_dump() for c in executed.columns],
                "metrics": [m.metric_id for m in executed.metric_definitions], "limitations": executed.limitations,
                "result_checks": [c.model_dump() for c in executed.result_checks],
                "provenance": executed.provenance.model_dump() if executed.provenance else None}
            if versions_before is not None:
                result["delta_versions"] = {"before": versions_before, "after": self.delta_versions()}
            if attempt.baseline_sql and executed.status == "ok":
                baseline_rows = self.baseline(attempt.baseline_sql)
                matched, detail = compare(attempt.compare, executed.rows, baseline_rows)
                result["baseline"] = {"sql": " ".join(attempt.baseline_sql.split()), "matched": matched, "detail": detail}

        result["passed"] = self.attempt_passed(attempt, result)
        return result

    def sql_for(self, attempt: Attempt, ctx: RequestContext, case: Case) -> tuple[str, dict, str]:
        """The attempt's SQL: hand-written test SQL, or the reviewed template filled by TemplateSqlGenerator."""
        if attempt.sql:
            return attempt.sql, attempt.parameters, "hand-written test SQL"
        examples = self.service.get_verified_sql_examples(ctx, query=case.question, limit=10).examples
        context = SchemaContext(question=case.question, candidates=[], column_types={}, metrics=[], examples=examples)
        generated = TemplateSqlGenerator(attempt.example_id, attempt.parameters).generate(context)
        return generated.sql, generated.parameters, f"{generated.generator}: {attempt.example_id}"

    @staticmethod
    def attempt_passed(attempt: Attempt, result: dict) -> bool:
        """An approved attempt must also execute and match its baseline; a rejected one must fail with the
        expected code and return no rows.
        """
        validation = result["validation"]
        if attempt.expect == "approved":
            ok = validation["approved"]
            if attempt.execute:
                ok = ok and result["execution"]["status"] == "ok"
            if attempt.baseline_sql:
                ok = ok and result.get("baseline", {}).get("matched", False)
            return ok
        codes = set(validation["error_codes"]) | set(result.get("execution", {}).get("error_codes", []))
        never_ran = result.get("execution", {}).get("row_count", 0) == 0
        return attempt.expect in codes and never_ran

    def delta_versions(self) -> dict:
        """Current Delta version of the watched Gold tables."""
        versions = {}
        for table in ("gold_project_portfolio",):
            row = self.spark.sql(f"DESCRIBE HISTORY workspace.wbg_gold.{table} LIMIT 1").collect()[0]
            versions[table] = row["version"]
        return versions


# ------------------------------------------------------------------------------------------------------- reporting
def write_outputs(evaluation: Evaluation, results: list[dict]) -> Path:
    """Write results.json, the SQL example verification file and EVALUATION_RESULTS.md; returns the run folder."""
    out = RUNS_DIR / evaluation.run_id
    out.mkdir(parents=True, exist_ok=True)
    summary = {"run_id": evaluation.run_id, "cases": len(results), "passed": sum(r["passed"] for r in results),
               "registry_build_id": evaluation.service.bundle.build.build_id,
               "credential_mode": "dev_owner_credentials", "production_read_only": False}
    (out / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1, default=str))

    # Templates whose executed result matched a baseline become 'verified' at the next registry build.
    examples = {}
    for r in results:
        for a in r["attempts"]:
            if a["sql_source"].startswith("template") and "baseline" in a:
                example_id = a["sql_source"].split(": ")[-1]
                examples[example_id] = {"matched_baseline": a["baseline"]["matched"], "run": evaluation.run_id,
                                        "detail": a["baseline"]["detail"]}
    VERIFICATION_DIR.mkdir(exist_ok=True)
    (VERIFICATION_DIR / f"sql_examples_verification_{evaluation.run_id}.json").write_text(
        json.dumps({"run": evaluation.run_id, "examples": examples}, indent=1))

    lines = [f"# Structured Data Agent evaluation {evaluation.run_id}", "",
             f"Cases passed: **{summary['passed']}/{summary['cases']}**. Credential mode: dev_owner_credentials "
             "(production_read_only = false). SQL came from reviewed templates (deterministic; no LLM) or hand-written test SQL.", "",
             "| Case | Title | Discovery (top-3 hit) | Attempts: decision / execution / baseline | ms | Result |", "|---|---|---|---|---|---|"]
    for r in results:
        parts = []
        for a in r["attempts"]:
            decision = "approved" if a["validation"]["approved"] else ",".join(a["validation"]["error_codes"])
            execution = a.get("execution", {}).get("status", "not executed")
            if a.get("execution", {}).get("error_codes"):
                execution += f" ({','.join(a['execution']['error_codes'])})"
            baseline = "match" if a.get("baseline", {}).get("matched") else ("MISMATCH" if "baseline" in a else "-")
            parts.append(f"{a['label']}: {decision} / {execution} / {baseline}")
        discovery = "clarification requested" if r["discovery"]["ambiguity"] else ("yes" if r["discovery"]["hit_in_top3"] else "NO")
        lines.append(f"| {r['case_id']} | {r['title']} | {discovery} | {'<br>'.join(parts) or '-'} | {r['total_ms']} | "
                     f"{'PASS' if r['passed'] else 'FAIL'} |")
    (out / "EVALUATION_RESULTS.md").write_text("\n".join(lines) + "\n")
    return out


def main() -> None:
    """Run all cases, print progress and write the outputs."""
    evaluation = Evaluation()
    results = []
    for case in CASES:
        print(f"{case.case_id} {case.title}", flush=True)
        results.append(evaluation.run_case(case))
        print(f"   -> {'PASS' if results[-1]['passed'] else 'FAIL'} ({results[-1]['total_ms']} ms)", flush=True)
    out = write_outputs(evaluation, results)
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed -> {out}")


if __name__ == "__main__":
    main()
