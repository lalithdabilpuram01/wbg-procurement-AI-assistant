"""Read one job, or one sign-in session, from the JSON log as an indented tree: every step, what it wrote, where it
went next, and what failed.

    uv run wbg-logs                     the latest job
    uv run wbg-logs <job_id>            one job (the id is in the API's job response and on every log line)
    uv run wbg-logs --failed            the latest job that logged an error
    uv run wbg-logs <job_id> --state    also the full state after every graph step
    uv run wbg-logs --list              the latest jobs, one line each
    uv run wbg-logs --sessions          the latest sign-in sessions, one line each
    uv run wbg-logs --session [<id>]    one session (default: the latest): its security events and HTTP requests in
                                        order, and each job it started as a tree of steps

A session reads like this (shortened):

    Session ses_3f2a...  user 9c1e...  (2 jobs, 14 requests)
    10:00:01.002        auth.sign_in  outcome=success
    10:00:04.310        http request  method=POST path=/conversations status_code=201
    10:00:05.120        job job_1 (message), started by http_5c...
                          + job.message ...

Example (shortened):

    12:00:01.120  + job.message  job_id=job_1 job_kind=message
    12:00:01.121    + conversation.message  question=How many bid invitations ...
    12:00:01.300      message routed  route=structured_data reason=data_terms
    12:00:01.301      + orchestrator.node.intake
    12:00:01.330          state: phase=intake_ok, replans=0
    12:00:01.331      - orchestrator.node.intake  done 30 ms  status=allowed
    12:00:01.331      -> next step: supervise
    ...
    12:00:02.010              + worker.structured
    12:00:02.400 ERROR            KeyError at gateway.py:88:run  (in worker.structured)
    12:00:02.401              - worker.structured  FAILED 391 ms

Reads orchestrator/runs/logs/app.jsonl and its rotated files (app.jsonl.1 ... .5), oldest first.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "runs" / "logs" / "app.jsonl"
VALUE_CHARS = 160


# ---------------------------------------------------------------------------------------------------- reading
def read_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Every JSON line of the log and its rotated files, oldest first (lines that are not JSON are skipped)."""
    files = [path.with_name(f"{path.name}.{n}") for n in range(5, 0, -1)] + [path]
    for file in files:
        if not file.is_file():
            continue
        with file.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue


def jobs(lines: Iterable[dict]) -> list[dict[str, Any]]:
    """One entry per job, in start order: id, kind, start time, final status and whether it logged an error."""
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        job_id = line.get("job_id")
        if not job_id:
            continue
        job = found.setdefault(job_id, {"job_id": job_id, "kind": line.get("job_kind"), "started": line.get("time"),
                                        "status": None, "errors": 0})
        if line.get("message") == "job finished":
            job["status"] = line.get("status")
        elif line.get("message") == "job failed":
            job["status"] = "failed"
        if line.get("level") == "ERROR":
            job["errors"] += 1
    return list(found.values())


def sessions(lines: Iterable[dict]) -> list[dict[str, Any]]:
    """One entry per sign-in session, in start order: id, user hash, first and last time, jobs, requests, problems."""
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        session_id = line.get("session_id")
        if not session_id:
            continue
        session = found.setdefault(session_id, {"session_id": session_id, "user_hash": line.get("user_hash"),
                                                "started": line.get("time"), "last": None, "jobs": set(), "requests": 0,
                                                "problems": 0})
        session["last"] = line.get("time")
        if line.get("job_id"):
            session["jobs"].add(line["job_id"])
        if line.get("message") == "http request":
            session["requests"] += 1
        if line.get("level") in ("ERROR", "WARNING"):
            session["problems"] += 1
    return list(found.values())


# -------------------------------------------------------------------------------------------------- rendering
def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS] + "..."


# Shown once in the header instead of on every step line ("owner_hash": logs written before the standard names).
HEADER_IDS = ("session_id", "user_hash", "investigation_id", "conversation_id", "gen_ai.conversation.id", "request_id",
              "user.hash", "owner_hash", "plan_id", "service_version", "environment")
REPEATED = {*HEADER_IDS, "node", "job_id", "job_kind", "state", "state_changes"}


def _pairs(values: dict[str, Any] | None, skip: Iterable[str] = REPEATED) -> str:
    skipped = set(skip)
    shown = {k: (v.rsplit(".", 1)[-1] if k == "task_id" and isinstance(v, str) else v) for k, v in (values or {}).items()}
    return " ".join(f"{k}={_short(v)}" for k, v in shown.items() if k not in skipped and v not in (None, "", [], {}))


def _changes(changes: dict[str, Any]) -> str:
    parts = []
    for key, value in changes.items():
        if key == "task_states" and isinstance(value, dict):
            parts.append("tasks " + ", ".join(f"{t}: {s}" for t, s in value.items()))
        elif key == "plan" and isinstance(value, dict):
            tasks = ", ".join(f"{t.get('task')}={t.get('worker')}.{t.get('operation')}" for t in value.get("tasks", [])
                              if isinstance(t, dict))
            parts.append(f"plan={value.get('plan_id')} ({tasks})")
        elif isinstance(value, list):
            parts.append(f"{key} +{len(value)} {_short(value)}")
        else:
            parts.append(f"{key}={_short(value)}")
    return "; ".join(parts) or "nothing"


IDS = ("job_id", "job_kind", "request_id", "investigation_id", "conversation_id", "http_request_id", "trace_id", "span_id",
       "time", "level", "logger", "message", "step", "step_path", "depth", "session_id", "user_hash", "service_version",
       "environment", "event", "gen_ai.conversation.id", "session.id", "user.hash")


def render(lines: list[dict[str, Any]], show_state: bool = False, show_traceback: bool = False) -> list[str]:
    """The job's lines as text, indented by step depth (a step's own lines one level under its start line)."""
    out: list[str] = []
    for line in lines:
        depth = line.get("depth", -1)
        time = (line.get("time") or "")[11:23]
        level = line.get("level", "")
        message = line.get("message", "")
        tag = f"{time} {level:<5}" if level in ("ERROR", "WARNING") else f"{time}      "
        own, inner = "  " * max(depth, 0), "  " * (depth + 1)
        if message == "step started":
            out.append(f"{tag} {own}+ {line.get('step')}  {_pairs(line.get('attributes'))}".rstrip())
        elif message == "step finished":
            outcome = line.get("outcome", "")
            shown = "FAILED" if outcome == "failed" else outcome
            attributes = _pairs(line.get("attributes"), skip=REPEATED | {"question"})
            out.append(f"{tag} {own}- {line.get('step')}  {shown} {line.get('duration_ms', '?')} ms  {attributes}".rstrip())
        elif message in ("state updated", "task state updated"):
            label = "task state" if message == "task state updated" else "state"
            out.append(f"{tag} {inner}{label}: {_changes(line.get('changes') or {})}")
            if show_state:
                body = json.dumps(line.get("state"), indent=2, ensure_ascii=False, default=str)
                out += [f"{' ' * 19}{inner}  {row}" for row in body.splitlines()]
        elif message == "next step":
            out.append(f"{tag} {own}-> next step: {line.get('to_step')}  (phase {line.get('phase')})")
        elif line.get("error_type"):
            where = f"{line.get('error_type')} at {line.get('error_location')}"
            out.append(f"{tag} {inner}{message}: {where}: {line.get('error_message', '')}  (in {line.get('step', '-')})")
            if show_traceback and line.get("traceback"):
                out += [f"{' ' * 19}{inner}  {row}" for row in line["traceback"].rstrip().splitlines()]
        else:
            extra = {k: v for k, v in line.items() if k not in IDS and k != "attributes"} | (line.get("attributes") or {})
            out.append(f"{tag} {inner}{message}  {_pairs(extra)}".rstrip())
    return out


def header(job_id: str, lines: list[dict[str, Any]]) -> str:
    """The job's ids, each found once in its lines (the trace id opens the same run in Logfire)."""
    found: dict[str, Any] = {"trace_id": next((line["trace_id"] for line in lines if line.get("trace_id")), None)}
    for line in lines:
        for source in (line, line.get("attributes") or {}):
            for key in HEADER_IDS:
                if source.get(key) and key not in found:
                    found[key] = source[key]
    ids = "\n".join(f"  {k:<17}{v}" for k, v in found.items() if v)
    return f"Job {job_id}  ({len(lines)} log lines)\n{ids}\n"


def render_session(session_id: str, lines: list[dict[str, Any]], show_state: bool = False,
                   show_traceback: bool = False) -> list[str]:
    """The session in time order: its own lines (security events, HTTP requests) one per row, and each job, where it
    started, as a block with its steps as a tree."""
    own = [line for line in lines if line.get("session_id") == session_id]
    out: list[str] = []
    shown: set[str] = set()
    for line in own:
        job_id = line.get("job_id")
        if not job_id:
            out += render([line], show_traceback=show_traceback)
        elif job_id not in shown:
            shown.add(job_id)
            started_by = f", started by {line['http_request_id']}" if line.get("http_request_id") else ""
            out.append(f"{(line.get('time') or '')[11:23]}       job {job_id} ({line.get('job_kind')}){started_by}")
            job_lines = [entry for entry in own if entry.get("job_id") == job_id]
            out += [f"{' ' * 4}{row}" for row in render(job_lines, show_state=show_state, show_traceback=show_traceback)]
    return out


def session_header(session: dict[str, Any]) -> str:
    return (f"Session {session['session_id']}  user {session['user_hash'] or '-'}  ({len(session['jobs'])} jobs, "
            f"{session['requests']} requests, {session['problems']} warnings or errors)\n"
            f"  from {session['started']}  to {session['last']}\n")


def failure_summary(lines: list[dict[str, Any]]) -> list[str]:
    """Where the job failed: the step path of each ERROR line, and the error at its innermost step."""
    errors = [line for line in lines if line.get("level") == "ERROR"]
    if not errors:
        return ["No errors logged for this job."]
    return [f"FAILED in: {e.get('step_path', e.get('step', '-'))}\n           {e.get('error_type')} at {e.get('error_location')}: "
            f"{e.get('error_message', '')}" for e in errors]


# ------------------------------------------------------------------------------------------------------ command
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wbg-logs", description="Show one job from the app's JSON log as a tree of steps.")
    parser.add_argument("job_id", nargs="?", help="the job to show (default: the latest job)")
    parser.add_argument("--failed", action="store_true", help="the latest job that logged an error")
    parser.add_argument("--state", action="store_true", help="also print the full state after every graph step")
    parser.add_argument("--traceback", action="store_true", help="also print the traceback of each error")
    parser.add_argument("--list", action="store_true", help="list the latest jobs instead")
    parser.add_argument("--sessions", action="store_true", help="list the latest sign-in sessions instead")
    parser.add_argument("--session", nargs="?", const="latest", metavar="SESSION_ID",
                        help="show one sign-in session (default: the latest) with its requests and jobs")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help=f"the log file (default: {DEFAULT_LOG})")
    args = parser.parse_args(argv)

    lines = list(read_lines(args.log))
    if args.sessions or args.session:
        return show_sessions(lines, args)
    known = jobs(lines)
    if not known:
        print(f"No jobs in {args.log}. Start the API (uv run wbg-api) and ask a question first.")
        return 1
    if args.list:
        for job in known[-20:]:
            print(f"{job['started']}  {job['job_id']}  {job['kind'] or '-':<8}  {job['status'] or 'running':<24}"
                  f"{'  errors=' + str(job['errors']) if job['errors'] else ''}")
        return 0
    if args.job_id:
        job_id = args.job_id
    elif args.failed:
        failed = [j for j in known if j["errors"] or j["status"] == "failed"]
        if not failed:
            print("No failed jobs in the log.")
            return 0
        job_id = failed[-1]["job_id"]
    else:
        job_id = known[-1]["job_id"]
    selected = [line for line in lines if line.get("job_id") == job_id]
    if not selected:
        print(f"Job {job_id} is not in {args.log}.")
        return 1
    print(header(job_id, selected))
    print("\n".join(render(selected, show_state=args.state, show_traceback=args.traceback)))
    print("\n" + "\n".join(failure_summary(selected)))
    return 0


def show_sessions(lines: list[dict[str, Any]], args: argparse.Namespace) -> int:
    """`--sessions` (a list) or `--session [<id>]` (one session as a tree)."""
    known = sessions(lines)
    if not known:
        print(f"No sessions in {args.log}. Sign in to the web app and ask a question first.")
        return 1
    if args.sessions:
        for session in known[-20:]:
            print(f"{session['started']}  {session['session_id']}  user {(session['user_hash'] or '-')[:12]}  "
                  f"jobs={len(session['jobs'])} requests={session['requests']}"
                  f"{'  warnings/errors=' + str(session['problems']) if session['problems'] else ''}")
        return 0
    session: dict[str, Any] | None = (known[-1] if args.session == "latest"
                                      else next((s for s in known if s["session_id"] == args.session), None))
    if session is None:
        print(f"Session {args.session} is not in {args.log}.")
        return 1
    print(session_header(session))
    print("\n".join(render_session(session["session_id"], lines, show_state=args.state, show_traceback=args.traceback)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
