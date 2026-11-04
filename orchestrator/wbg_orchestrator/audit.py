"""Read the audit trail: recent investigations, or one investigation as a step-by-step timeline.

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.audit list
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.audit show <request_id | investigation_id>

The audit file holds metadata only (ids, statuses, timings, counts, error codes; never question or answer text).
Each line carries the Logfire trace id: search `trace_id = '<id>'` in Logfire to open the spans of that investigation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from .runtime import ORCHESTRATOR_DIR

DEFAULT_FILE = ORCHESTRATOR_DIR / "runs" / "app_audit.jsonl"


def read(path: Path) -> list[dict]:
    """Every event in the audit file, oldest first; an empty list when the file does not exist."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _time(event: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(event["occurred_at"])


def investigations(events: Iterable[dict]) -> list[dict]:
    """One summary per investigation, newest first."""
    runs: dict[str, dict] = {}
    for e in events:
        run = runs.setdefault(e.get("investigation_id", "?"), {"investigation_id": e.get("investigation_id"), "events": []})
        run["events"].append(e)
        run.setdefault("request_id", e.get("request_id"))
    summaries = []
    for run in runs.values():
        first, last = run["events"][0], run["events"][-1]
        finished = next((e for e in reversed(run["events"]) if e["event"] == "investigation.finished"), {})
        summaries.append({"started": first["occurred_at"], "request_id": run["request_id"], "investigation_id": run["investigation_id"],
                          "status": finished.get("status", "running or stopped"), "tasks": finished.get("task_count"),
                          "cost_usd": finished.get("cost_usd"), "seconds": round((_time(last) - _time(first)).total_seconds(), 1)})
    return sorted(summaries, key=lambda s: s["started"], reverse=True)


def timeline(events: Iterable[dict], key: str) -> list[str]:
    """The readable timeline of the investigation whose request or investigation id is `key`."""
    mine = [e for e in events if key in (e.get("request_id"), e.get("investigation_id"))]
    if not mine:
        return [f"No audit events for {key}."]
    start = _time(mine[0])
    head = mine[0]
    lines = [f"Investigation {head.get('investigation_id')}  request {head.get('request_id')}",
             f"  conversation {head.get('conversation_id')}  user (hash) {head.get('owner_hash')}  started {head['occurred_at']}"]
    traces = sorted({e["trace_id"] for e in mine if e.get("trace_id")})
    lines.append("  Logfire trace: " + (", ".join(traces) if traces else "not exported (Logfire off for this run)"))
    for e in mine:
        lines.append(f"  +{(_time(e) - start).total_seconds():6.1f}s  {_describe(e)}")
    return lines


def _describe(e: dict) -> str:
    """One readable line for an audit event; unknown event kinds are shown by name only."""
    kind = e["event"]
    errors = f"  errors: {e['error_codes']}" if e.get("error_codes") else ""
    if kind == "guard.input":
        return f"input guardrail   {e.get('status')}  ({e.get('guard_policy')}{', ' + e['guard_reason'] if e.get('guard_reason') else ''})"
    if kind == "investigation.started":
        return f"started           mode {e.get('mode')}, document release {e.get('rag_release')}"
    if kind == "plan.created":
        return f"plan              {e.get('produced_by')} ({e.get('mode')}), {e.get('task_count')} task(s)  {e.get('plan_id')}"
    if kind == "approval.requested":
        return f"approval asked    {e.get('approval_kind')}  {e.get('approval_id')}"
    if kind == "approval.decided":
        return f"approval          {e.get('approval_decision')}"
    if kind == "task.finished":
        return (f"task {e.get('task_id', ''):<12} {e.get('worker')}.{e.get('operation')} -> {e.get('status')}  "
                f"{e.get('evidence_count', 0)} evidence, {e.get('duration_ms', 0)} ms, attempt {e.get('attempt')}{errors}")
    if kind == "answer.composed":
        return (f"answer            {e.get('status')}: {e.get('claims_kept')} claim(s) kept, {e.get('claims_removed')} removed, "
                f"from {e.get('evidence_count')} evidence{errors}")
    if kind == "investigation.finished":
        cost = f", ${e['cost_usd']}" if e.get("cost_usd") else ""
        return (f"finished          {e.get('status')}: {e.get('task_count')} task(s), {e.get('llm_calls', 0)} Claude call(s), "
                f"{e.get('input_tokens', 0)}+{e.get('output_tokens', 0)} tokens{cost}{errors}")
    return kind


def main(argv: list[str]) -> int:
    """Command line: `list` prints recent investigations, `show <id>` prints one timeline."""
    parser = argparse.ArgumentParser(description="Read the orchestrator audit trail")
    parser.add_argument("command", choices=["list", "show"])
    parser.add_argument("key", nargs="?", help="request_id or investigation_id (for show)")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--last", type=int, default=15)
    args = parser.parse_args(argv)
    events = read(args.file)
    if args.command == "list":
        for s in investigations(events)[:args.last]:
            print(f"{s['started'][:19]}  {s['request_id'] or '-':<26} {s['status']:<22} tasks {s['tasks'] or '-':<3} "
                  f"{s['seconds']:>6}s  {('$' + s['cost_usd']) if s['cost_usd'] else ''}")
        return 0
    if not args.key:
        parser.error("show needs a request_id or investigation_id")
    print("\n".join(timeline(events, args.key)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
