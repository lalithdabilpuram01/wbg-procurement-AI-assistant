"""Application logs: one JSON line per event in orchestrator/runs/logs/app.jsonl, correlated with the Logfire trace.

Every line carries the ids that tie it to one user action, so an engineer can go from a failure to its cause:

    session_id       the sign-in session (one per sign-in; a script token has one fixed id), on every line of the
                     session: its HTTP requests, its security events and the jobs it started
    user_hash        the user, pseudonymous (the same hash as `user.hash` on the spans; never the username)
    http_request_id  the HTTP request (X-Request-ID); on a job's lines, the request that started the job
    job_id           the background job the web app polls (shown in the API's job response)
    request_id       the question's id (one per user message; the investigation id is derived from it)
    conversation_id  the chat thread
    trace_id         the Logfire / OpenTelemetry trace of the same work (open it in Logfire to see the nested spans)
    step             the step the line was written in (the innermost span, e.g. orchestrator.node.execute)
    step_path        every enclosing step, outermost first ("job.message > conversation.message > ...")
    depth            how deep that step is (0 = the job), so the lines can be read as a tree
    service_version  the code that wrote the line (git commit, or WBG_SERVICE_VERSION), and `environment` where it ran

So a session reads as a tree: session > its HTTP requests and jobs > each job's steps (`uv run wbg-logs --session`).

The ids come from `log_context(...)`, which the job runner and the HTTP layer open; code below them only logs.
Messages are short and fixed ("job failed", "planner failed"); free text that may hold personal data (a question,
SQL, an exception message) is passed through `redacted()` first. Tracebacks are kept: their frames are code
locations, and the exception message in them is redacted too.

Security events (sign-in, sign-up, password reset, session end, refused access, reviewer decisions; see the OWASP
Logging Cheat Sheet) are written by `security_event` to the logger `wbg.security`, with `event` and `outcome` fields.

Levels: WBG_LOG_LEVEL (the file, default INFO) and WBG_CONSOLE_LOG_LEVEL (the terminal, default WARNING). Other
libraries (uvicorn, psycopg, httpx, mcp) write their warnings and errors to the same file, as JSON.

To find a failure:   grep '"level": "ERROR"' orchestrator/runs/logs/app.jsonl
To follow one job:   grep '<job_id>' orchestrator/runs/logs/app.jsonl
To read it as a tree: uv run wbg-logs [<job_id>] [--state]   (log_view.py)
To read a session:   uv run wbg-logs --session [<session_id>]
"""
from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import logging.handlers
import os
import subprocess
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

LOGGER_NAME = "wbg"
MAX_TEXT_CHARS = 2000
CONTEXT_KEYS = ("session_id", "user_hash", "http_request_id", "job_id", "job_kind", "request_id", "investigation_id",
                "conversation_id")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
THIRD_PARTY_LEVEL = "WARNING"                   # other libraries: only their warnings and errors reach the file
QUIET_LIBRARIES = {
    "presidio-analyzer": "ERROR",               # warns on every load about languages and settings the app does not use
    "uvicorn.access": "WARNING",                # the API logs its own requests (with their session)
}
REPO = Path(__file__).resolve().parents[2]

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("wbg_log_context", default={})
_steps: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("wbg_log_steps", default=())
_masking: contextvars.ContextVar[bool] = contextvars.ContextVar("wbg_log_masking", default=False)


def get_logger(name: str) -> logging.Logger:
    """A logger under the app's root logger ("wbg.<name>"), so one configuration covers every module."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


# ------------------------------------------------------------------------------------------------ context
@contextmanager
def log_context(**ids: str | None) -> Iterator[None]:
    """Attach ids to every log line written inside this block (in this thread and in tasks started from it)."""
    token = _context.set({**_context.get(), **{k: v for k, v in ids.items() if v}})
    try:
        yield
    finally:
        _context.reset(token)


def bind(**ids: str | None) -> None:
    """Add ids to the current context once they become known (e.g. the request id after routing)."""
    _context.set({**_context.get(), **{k: v for k, v in ids.items() if v}})


def current_context() -> dict[str, str]:
    return dict(_context.get())


@contextmanager
def step_scope(name: str) -> Iterator[None]:
    """Mark the lines written inside this block as belonging to step `name`, nested under the enclosing steps.
    Every tracer span opens one, so the log's step path is the same tree as the Logfire trace."""
    token = _steps.set((*_steps.get(), name))
    try:
        yield
    finally:
        _steps.reset(token)


def current_steps() -> dict[str, Any]:
    steps = _steps.get()
    if not steps:
        return {}
    return {"step": steps[-1], "step_path": " > ".join(steps), "depth": len(steps) - 1}


# ------------------------------------------------------------------------------------------------ service
@dataclass(frozen=True)
class ServiceInfo:
    """Which code wrote a log line or a span, and where it ran (OpenTelemetry service.version and
    deployment.environment.name)."""

    version: str
    environment: str

    @classmethod
    def from_env(cls) -> "ServiceInfo":
        """WBG_SERVICE_VERSION (default: the git commit, `unknown` outside git) and WBG_ENVIRONMENT (default `local`)."""
        return cls(version=os.environ.get("WBG_SERVICE_VERSION") or _git_commit(),
                   environment=os.environ.get("WBG_ENVIRONMENT") or "local")


def _git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def level_from_env(name: str, default: str) -> str:
    """A log level from the environment; an unknown value falls back to the default (and says so on stderr)."""
    value = (os.environ.get(name) or default).strip().upper()
    if value not in LEVELS:
        print(f"{name}={value!r} is not a log level ({', '.join(LEVELS)}); using {default}")
        return default
    return value


# ------------------------------------------------------------------------------------------------ security
_security = logging.getLogger(f"{LOGGER_NAME}.security")


def security_event(event: str, outcome: str, **fields: Any) -> None:
    """One security event (OWASP Logging Cheat Sheet): what happened (`auth.sign_in`, `access.denied`, ...), whether
    it succeeded, and identifier-like details only (a user hash, a session id, a status code, a reason code).
    Failures are warnings, so they also reach the terminal."""
    level = logging.WARNING if outcome == "failure" else logging.INFO
    _security.log(level, event, extra={"event": event, "outcome": outcome, **{k: v for k, v in fields.items() if v is not None}})


# ------------------------------------------------------------------------------------------------ redaction
def redacted(text: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    """Text safe to log: secret-shaped values and personal data masked, then truncated.

    A line logged by the masker itself (Presidio, while it loads) is withheld instead of masked: masking it would
    start the masker again from inside itself."""
    if not text:
        return ""
    if _masking.get():
        return "[text withheld: logged while masking]"
    token = _masking.set(True)
    try:
        from conversation.pii import redact_personal_data
        return redact_personal_data(str(text))[:limit]
    except Exception:                       # the masker itself failed: log nothing rather than unmasked text
        return "[text withheld: redaction unavailable]"
    finally:
        _masking.reset(token)


def describe_error(error: BaseException) -> dict[str, str]:
    """The error's type, a redacted message and where it was raised (the innermost frame), for logs and spans."""
    frames = traceback.extract_tb(error.__traceback__)
    location = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}:{frames[-1].name}" if frames else ""
    return {"error_type": type(error).__name__, "error_message": redacted(str(error), 500), "error_location": location}


# ------------------------------------------------------------------------------------------------ formatting
def _is_app_logger(name: str) -> bool:
    return name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}.")


def _trace_ids() -> dict[str, str]:
    from opentelemetry import trace
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, correlation ids, extra fields and any exception, and
    (when given) the service version and environment."""

    STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

    def __init__(self, service: ServiceInfo | None = None):
        super().__init__()
        self._service = {"service_version": service.version, "environment": service.environment} if service else {}

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            **current_context(), **current_steps(), **_trace_ids(), **self._service,
        }
        if not _is_app_logger(record.name):                                    # another library's message: masked
            line["message"] = redacted(line["message"])
        line.update({k: v for k, v in vars(record).items() if k not in self.STANDARD and not k.startswith("_")})
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            line.update(describe_error(error))
            line["traceback"] = redacted("".join(traceback.format_exception(error)), 8000)
        return json.dumps(line, default=str)


class ConsoleFormatter(logging.Formatter):
    """A short line for the terminal; the JSON log file has the full record."""

    def format(self, record: logging.LogRecord) -> str:
        ids = " ".join(f"{k}={v}" for k, v in current_context().items() if k in ("job_id", "request_id"))
        trace_id = _trace_ids().get("trace_id")
        step = current_steps().get("step")
        text = f"{record.levelname:<7} {record.name}: {record.getMessage()}" + (f" (in {step})" if step else "")
        if record.exc_info and record.exc_info[1] is not None:
            error = describe_error(record.exc_info[1])
            text += f" [{error['error_type']} at {error['error_location']}]"
        return " ".join(part for part in (text, ids, f"trace_id={trace_id}" if trace_id else "") if part)


def configure_logging(log_dir: Path, level: str = "INFO", console_level: str = "WARNING",
                      service: ServiceInfo | None = None) -> Path:
    """Write the app's logs to <log_dir>/app.jsonl (rotated at 10 MB, 5 files kept) and warnings to the terminal.

    The app's own loggers ("wbg.*") log from `level`; every other library (uvicorn, psycopg, httpx, mcp) from
    WARNING, through the root logger, into the same file and terminal. Called once by the API at start-up (never by
    tests, which keep the default: nothing written)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.jsonl"
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(JsonLineFormatter(service))
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(ConsoleFormatter())
    for name, logger_level in ((LOGGER_NAME, level), ("", THIRD_PARTY_LEVEL)):    # the app, then the root logger
        logger = logging.getLogger(name)
        logger.setLevel(logger_level)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.addHandler(file_handler)
        logger.addHandler(console)
    logging.getLogger(LOGGER_NAME).propagate = False                             # its lines are written once
    for name, quiet_level in QUIET_LIBRARIES.items():
        logging.getLogger(name).setLevel(quiet_level)
    return path
