"""Operator commands for the feedback loop (Module 4): a quality report and the export of reviewer-approved items.

    cd orchestrator
    uv run python -m wbg_orchestrator.learning_cli report     # feedback rate, causes, revisions, effect of saved items
    uv run python -m wbg_orchestrator.learning_cli export     # approved evaluation cases and approaches -> runs/learning/

Both read the Lakebase learning store (learning_db: schema wbg_learning on the development branch) across all users,
in a read-only transaction allowed by the `operator_read` policy.
`export` writes:
- feedback_cases.json: evaluation cases; `evaluation.cases` adds them to the app evaluation set when the file exists;
- approach_candidates.json: approved approaches (question, steps, tables, filters, SQL hash). SQL text is never
  stored, so turning one into a verified SQL example (structured_agent/metadata/curated/verified_sql_examples.json,
  then a registry rebuild) is a curator's step; until then approved approaches reach every user as shared guidance.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from conversation.learning import Feedback, LearnedItem, Review, read_all, row_to_feedback, row_to_item, row_to_review

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
EXPORT_DIR = ORCHESTRATOR_DIR / "runs" / "learning"
FEEDBACK_CASES = EXPORT_DIR / "feedback_cases.json"
APPROACH_CANDIDATES = EXPORT_DIR / "approach_candidates.json"


def quality_report(feedback: list[Feedback], items: list[LearnedItem], uses: list[tuple[str, str]],
                   reviews: list[Review]) -> dict[str, Any]:
    """What users said, what was corrected, and how answers that used saved items were rated."""
    latest = {(f.thread_id, f.turn_seq): f for f in sorted(feedback, key=lambda f: f.created_at)}.values()
    ratings = Counter(f.rating for f in latest)
    guided = {request_id for request_id, _ in uses}
    after_guidance = Counter(f.rating for f in latest if f.request_id in guided)
    return {
        "answers_rated": len(latest), "thumbs_up": ratings["up"], "thumbs_down": ratings["down"],
        "down_reasons": dict(Counter(r for f in latest if f.rating == "down" for r in f.reasons).most_common()),
        "corrections": sum(1 for f in feedback if f.correction),
        "revisions": sum(1 for f in feedback if f.status in ("revised", "validated")),
        "validated_corrections": sum(1 for f in feedback if f.status == "validated"),
        "items": dict(Counter(f"{i.scope}_{i.kind}" for i in items if i.status == "active")),
        "items_revoked": sum(1 for i in items if i.status == "revoked"),
        "requests_with_guidance": len(guided),
        "rated_with_guidance": {"up": after_guidance["up"], "down": after_guidance["down"]},
        "reviews": dict(Counter(f"{r.kind}:{r.status}" for r in reviews)),
    }


def evaluation_cases(reviews: list[Review]) -> list[dict[str, Any]]:
    return [{"case_id": f"F-{r.review_id[:8]}", "question": r.payload["question"], "expected_terms": list(r.expected_terms),
             "correction": r.payload.get("correction"), "note": r.note}
            for r in reviews if r.kind == "eval_case" and r.status == "approved" and r.payload.get("question")]


def approach_candidates(reviews: list[Review]) -> list[dict[str, Any]]:
    """Approved data approaches a curator can turn into verified SQL examples."""
    return [{"candidate_id": f"feedback_{r.review_id[:8]}", "question": r.payload["question"],
             **{k: r.payload.get("details", {}).get(k) for k in ("steps", "tables", "filters", "sql_sha256")},
             "approved_by": r.reviewer, "approved_on": r.decided_at.date().isoformat() if r.decided_at else None}
            for r in reviews if r.kind == "share_approach" and r.status == "approved" and r.payload.get("details", {}).get("tables")]


def load_all(connect) -> tuple[list[Feedback], list[LearnedItem], list[tuple[str, str]], list[Review]]:
    rows = read_all(connect)
    return ([row_to_feedback(r) for r in rows["feedback"]], [row_to_item(r) for r in rows["learned_items"]],
            [(r["request_id"], str(r["item_id"])) for r in rows["learning_uses"]], [row_to_review(r) for r in rows["reviews"]])


def export(reviews: list[Review], directory: Path = EXPORT_DIR) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    cases, approaches = evaluation_cases(reviews), approach_candidates(reviews)
    (directory / FEEDBACK_CASES.name).write_text(json.dumps(cases, indent=1))
    (directory / APPROACH_CANDIDATES.name).write_text(json.dumps(approaches, indent=1))
    return {"evaluation_cases": len(cases), "approach_candidates": len(approaches)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Feedback loop: report and export")
    parser.add_argument("command", choices=["report", "export"])
    args = parser.parse_args(argv)
    from .learning_db import learning_endpoint, learning_pool
    pool = learning_pool(learning_endpoint())
    try:
        feedback, items, uses, reviews = load_all(pool.connection)
    finally:
        pool.close()
    result = quality_report(feedback, items, uses, reviews) if args.command == "report" else export(reviews)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
