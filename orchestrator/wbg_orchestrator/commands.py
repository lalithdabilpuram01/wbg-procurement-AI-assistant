"""Short commands for running the app, installed by `uv sync` (see [project.scripts] in the root pyproject.toml).

    uv run wbg-api [--live] [--cap 5] [--port 8080]   the API; Claude is off unless --live (then capped per session)
    uv run wbg-web                                     the web app (npm run dev; npm install on first use)
    uv run wbg-user init-db|migrate|add|recovery-code|list   app users in Lakebase (accounts_db.py)
    uv run wbg-test                                    the offline tests (tests/, never tests/live/)
    uv run wbg-ingest <command>                        document ingestion (python -m ingest)
    uv run wbg-lessons <command>                       lesson cards (python -m lessons)
    uv run wbg-feedback <command>                      feedback reports (python -m wbg_orchestrator.learning_cli)
    uv run wbg-feedback-db init|check                  the feedback store on Lakebase (python -m wbg_orchestrator.learning_db)
    uv run wbg-eval <command>                          golden evaluations (python -m evaluation.golden)
    uv run wbg-logs [<job_id>] [--failed] [--state]    one job from the app log, as a tree of steps (log_view.py)
    uv run wbg-logs --session [<id>] | --sessions      one sign-in session (its requests and jobs), or the list
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO / "orchestrator"
FRONTEND = REPO / "frontend"
DEFAULT_CAP_USD = 5.0
LIVE_MAX_ATTEMPTS = 400          # with --live the dollar cap is the real limit
TESTS = REPO / "tests"


def _argv() -> list[str]:
    return sys.argv[1:]


# --------------------------------------------------------------------------------------------------- app
def api() -> int:
    parser = argparse.ArgumentParser(prog="wbg-api", description="Run the API on this laptop (http://127.0.0.1:<port>).")
    parser.add_argument("--live", action="store_true", help="turn Claude on for this session (answers written by Claude cost money)")
    parser.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="with --live: most this session may spend on Claude, in US$")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(_argv())
    if args.live:
        ledger = ORCHESTRATOR / "runs" / f"llm_ledger_{dt.datetime.now():%Y%m%dT%H%M%S}.json"
        os.environ.update({"WBG_ALLOW_LIVE_LLM_CALLS": "true", "WBG_LLM_MAX_USD": f"{args.cap:.2f}",
                           "WBG_LLM_MAX_ATTEMPTS": str(LIVE_MAX_ATTEMPTS), "WBG_LLM_LEDGER": str(ledger)})
        print(f"Claude: ON for this session, at most US$ {args.cap:.2f} (spend ledger: {ledger.relative_to(REPO)})")
    else:
        os.environ.pop("WBG_ALLOW_LIVE_LLM_CALLS", None)
        print("Claude: OFF. Project watch, monitoring, Check tender and lesson cards work; use --live for Claude answers.")
    from .api import main
    os.chdir(ORCHESTRATOR)                                   # the same working folder as `python -m wbg_orchestrator.api`
    return main(["serve", *(["--confirm-live-call"] if args.live else []), "--port", str(args.port)])


def web() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found: install Node.js (https://nodejs.org) first")
        return 1
    if not (FRONTEND / "node_modules").is_dir():
        subprocess.run([npm, "install"], cwd=FRONTEND, check=True)
    return subprocess.run([npm, "run", "dev", *_argv()], cwd=FRONTEND).returncode


def user() -> int:
    from .accounts_db import main
    return main(_argv())


# -------------------------------------------------------------------------------------------------- tools
def test() -> int:
    """Every offline suite under tests/ (tests/live/ is skipped, see tests/conftest.py); extra arguments go to pytest."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("WBG_RUN_LIVE_TESTS", None)                                     # wbg-test never runs the live suites
    return subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(TESTS), *_argv()],
                          cwd=REPO, env=env).returncode


def ingest() -> int:
    from ingest.__main__ import main
    return main(_argv())


def lessons() -> int:
    from lessons.__main__ import main
    return main(_argv())


def feedback() -> int:
    from .learning_cli import main
    return main(_argv())


def feedback_db() -> int:
    from .learning_db import main
    return main(_argv())


def evaluate() -> int:
    from evaluation.golden import main
    os.chdir(ORCHESTRATOR)
    return main(_argv())


def logs() -> int:
    from .log_view import main
    return main(_argv())
