"""Evaluate the application with LangSmith.

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.run preflight
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.run dataset          # Gold read-only + upload
    WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. \\
        uv run --locked python -B -m evaluation.run run --confirm-live-call                        # paid: app + judge
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.run audit            # offline

Spend: the app runs on its own ledger ($2.40) and the judge on another ($0.60): $3.00 at most, any limit stops the run.
Uploads: the dataset (questions and references) and, per case, the run's inputs and outputs. Every string uploaded with
a run is passed through the secret and personal-data redaction first. LangGraph's internal state is not traced.
The LangSmith key is read inside this process from the environment or `.env` (LANGSMITH_API_KEY, optional
LANGSMITH_ENDPOINT / LANGSMITH_WORKSPACE_ID) and is never printed or written anywhere.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app_config.check_env import read_env_file  # noqa: E402
from conversation.pii import redact_personal_data  # noqa: E402
from conversation.redaction import redact_secrets  # noqa: E402

from wbg_orchestrator.claude import PortkeyJsonClient  # noqa: E402
from wbg_orchestrator.live_llm import BudgetedClaudeClient, LedgerLimits, SpendLedger  # noqa: E402
from wbg_orchestrator.runtime import ORCHESTRATOR_DIR, RuntimeConfig  # noqa: E402

from .cases import DATASET, all_cases  # noqa: E402

RESULTS = ORCHESTRATOR_DIR / "live_results" / "eval"
DATASET_FILE = RESULTS / "dataset.json"
LANGSMITH_SETTINGS = ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID", "LANGSMITH_PROJECT")
APP = RuntimeConfig(ledger_path=RESULTS / "app_ledger.json", max_attempts=200, max_usd=Decimal("2.40"),
                    audit_path=ORCHESTRATOR_DIR / "runs" / "eval_audit.jsonl")
JUDGE_LEDGER, JUDGE_LIMITS = RESULTS / "judge_ledger.json", LedgerLimits(max_attempts=90, max_usd=Decimal("0.60"))
JUDGE_OUTPUT_TOKENS = 1500


# ------------------------------------------------------------------------------------------------- settings
def langsmith_settings() -> dict[str, bool]:
    """Copies the LangSmith settings into this process (from the environment or .env); returns which are present."""
    file_values = read_env_file()
    for name in LANGSMITH_SETTINGS:
        legacy = name.replace("LANGSMITH_", "LANGCHAIN_")                    # the older names LangSmith also accepts
        value = os.environ.get(name) or file_values.get(name) or os.environ.get(legacy) or file_values.get(legacy, "")
        if value:
            os.environ[name] = value
    os.environ["LANGSMITH_TRACING"] = "false"            # no automatic tracing of LangGraph internals
    return {name: bool(os.environ.get(name)) for name in LANGSMITH_SETTINGS}


def redacted(value: Any) -> Any:
    """Every string with secrets and personal data masked (applied to everything uploaded with a run)."""
    if isinstance(value, str):
        return redact_secrets(redact_personal_data(value))
    if isinstance(value, dict):
        return {k: redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value


def client():
    from langsmith import Client
    return Client(hide_inputs=redacted, hide_outputs=redacted)


def code_version() -> str:
    root = ORCHESTRATOR_DIR.parent
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
    return f"{head}+{len(dirty)} uncommitted" if dirty else head


# -------------------------------------------------------------------------------------------------- steps
def preflight() -> dict[str, Any]:
    present = langsmith_settings()
    return {"langsmith_settings_present": present, "ready": present["LANGSMITH_API_KEY"],
            "dataset_built": DATASET_FILE.exists(), "code_version": code_version(),
            "ledgers": {"app": APP.ledger_path.exists() and SpendLedger(APP.ledger_path).summary()["committed_usd"],
                        "judge": JUDGE_LEDGER.exists() and SpendLedger(JUDGE_LEDGER).summary()["committed_usd"]}}


def build_dataset() -> dict[str, Any]:
    """Cases with references (Gold reference values via the read-only gateway, no model), saved and uploaded."""
    from rag.release_v3.multilingual import question_sets
    from structured_agent.config import Settings
    from structured_agent.service import StructuredDataService
    from wbg_orchestrator.runtime import REGISTRY_PRINCIPAL, WORKSPACE
    from wbg_orchestrator.workers.prediction import gateway_sql

    settings = dataclasses.replace(Settings(), databricks_profile=APP.databricks_profile, allow_dev_owner_credentials=False,
                                   audit_log_path=APP.audit_path.with_name("eval_structured_audit.jsonl"))
    run_sql = gateway_sql(StructuredDataService.from_environment(settings), REGISTRY_PRINCIPAL, WORKSPACE)
    examples = [case.example() for case in all_cases(run_sql, question_sets())]
    RESULTS.mkdir(parents=True, exist_ok=True)
    DATASET_FILE.write_text(json.dumps(examples, indent=1, default=str))
    return {"examples": len(examples), "uploaded_as": upload(examples)}


def upload(examples: list[dict[str, Any]]) -> str:
    """One dataset per content version: the name carries a hash of the examples, so a run always names its exact data."""
    if not langsmith_settings()["LANGSMITH_API_KEY"]:
        raise SystemExit("LANGSMITH_API_KEY is not set (environment or .env); the dataset was saved locally only")
    digest = hashlib.sha256(json.dumps(examples, sort_keys=True, default=str).encode()).hexdigest()[:8]
    name, ls = f"{DATASET}-{digest}", client()
    if not ls.has_dataset(dataset_name=name):
        dataset = ls.create_dataset(name, description="WBG Procurement Intelligence: structured, documents, TableLens on/off, "
                                                      "planning, guardrails (see orchestrator/evaluation/cases.py)")
        ls.create_examples(dataset_id=dataset.id, examples=examples)
    return name


def run(confirmed: bool) -> dict[str, Any]:
    from langsmith import evaluate
    from structured_agent.nl2sql.live_guard import authorize_live_access
    from structured_agent.nl2sql.settings import load_claude_settings

    from .evaluators import ClaudeJudge, deterministic
    from .target import AppHarness

    examples = json.loads(DATASET_FILE.read_text())
    dataset = upload(examples)
    settings = dataclasses.replace(load_claude_settings(), timeout_seconds=120.0, max_output_tokens=JUDGE_OUTPUT_TOKENS)
    judge_client = BudgetedClaudeClient(settings, SpendLedger(JUDGE_LEDGER, JUDGE_LIMITS), output_caps={"eval_judge": JUDGE_OUTPUT_TOKENS},
                                        live_access=authorize_live_access(confirmed))
    harness = AppHarness(APP, confirmed)
    try:
        results = evaluate(harness, data=dataset, evaluators=[deterministic, ClaudeJudge(PortkeyJsonClient(judge_client))],
                           experiment_prefix="wbg-app", max_concurrency=0, client=client(),
                           description="Full application through the local API; deterministic checks + Claude judge",
                           metadata={"code_version": code_version(), "model": judge_client.model, "documents": "pilot_v3",
                                     "tablelens": "tablelens_pilot (74 tables)", "app_cap_usd": str(APP.max_usd),
                                     "judge_cap_usd": str(JUDGE_LIMITS.max_usd)})
        rows = [{"case_id": r["example"].inputs["case_id"], "category": r["example"].inputs["category"],
                 "inputs": r["example"].inputs, "reference": r["example"].outputs, "outputs": r["run"].outputs,
                 "error": r["run"].error, "scores": [{"key": e.key, "score": e.score, "comment": e.comment}
                                                     for e in r["evaluation_results"]["results"]]} for r in results]
    finally:
        harness.close()
    (RESULTS / "results.json").write_text(json.dumps({"experiment": results.experiment_name, "dataset": dataset,
                                                       "code_version": code_version(), "rows": rows}, indent=1, default=str))
    return {"experiment": results.experiment_name, "dataset": dataset, "cases": len(rows),
            "app_ledger": SpendLedger(APP.ledger_path).summary(), "judge_ledger": SpendLedger(JUDGE_LEDGER).summary()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="LangSmith evaluation of the application")
    parser.add_argument("step", choices=["preflight", "dataset", "run", "audit"])
    parser.add_argument("--confirm-live-call", action="store_true")
    args = parser.parse_args(argv)
    if args.step == "audit":
        from .audit import audit
        result: dict[str, Any] = audit(RESULTS)
    else:
        result = {"preflight": preflight, "dataset": build_dataset, "run": lambda: run(args.confirm_live_call)}[args.step]()
    print(json.dumps(result, indent=1, default=str)[:5000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
