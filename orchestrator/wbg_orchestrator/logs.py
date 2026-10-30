"""Application logs: one JSON line per event in orchestrator/runs/logs/app.jsonl, correlated with the Logfire trace.

Every line carries the ids that tie it to one user action, so an engineer can go from a failure to its cause:

    job_id           the background job the web app polls (shown in the API's job response)
    request_id       the question's id (one per user message; the investigation id is derived from it)
    conversation_id  the chat thread
    trace_id         the Logfire / OpenTelemetry trace of the same work (open it in Logfire to see the nested spans)

The ids come from `log_context(...)`, which the job runner and the HTTP layer open; code below them only logs.
Messages are short and fixed ("job failed", "planner failed"); free text that may hold personal data (a question,
SQL, an exception message) is passed through `redacted()` first. Tracebacks are kept: their frames are code
locations, and the exception message in them is redacted too.

To find a failure:   grep '"level": "ERROR"' orchestrator/runs/logs/app.jsonl
To follow one job:   grep '<job_id>' orchestrator/runs/logs/app.jsonl
"""
from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import logging.handlers
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LOGGER_NAME = "wbg"
MAX_TEXT_CHARS = 2000
CONTEXT_KEYS = ("job_id", "job_kind", "request_id", "investigation_id", "conversation_id", "http_request_id")

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("wbg_log_context", default={})


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


# ------------------------------------------------------------------------------------------------ redaction
def redacted(text: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    """Text safe to log: secret-shaped values and personal data masked, then truncated."""
    if not text:
        return ""
    try:
        from conversation.pii import redact_personal_data
        return redact_personal_data(str(text))[:limit]
    except Exception:                       # the masker itself failed: log nothing rather than unmasked text
        return "[text withheld: redaction unavailable]"


def describe_error(error: BaseException) -> dict[str, str]:
    """The error's type, a redacted message and where it was raised (the innermost frame), for logs and spans."""
    frames = traceback.extract_tb(error.__traceback__)
    location = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}:{frames[-1].name}" if frames else ""
    return {"error_type": type(error).__name__, "error_message": redacted(str(error), 500), "error_location": location}


# ------------------------------------------------------------------------------------------------ formatting
def _trace_ids() -> dict[str, str]:
    from opentelemetry import trace
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, correlation ids, extra fields and any exception."""

    STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            **current_context(), **_trace_ids(),
        }
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
        text = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[1] is not None:
            error = describe_error(record.exc_info[1])
            text += f" [{error['error_type']} at {error['error_location']}]"
        return " ".join(part for part in (text, ids, f"trace_id={trace_id}" if trace_id else "") if part)


def configure_logging(log_dir: Path, level: str = "INFO", console_level: str = "WARNING") -> Path:
    """Write the app's logs to <log_dir>/app.jsonl (rotated at 10 MB, 5 files kept) and warnings to the terminal.

    Called once by the API at start-up (never by tests, which keep the default: nothing written)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.jsonl"
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(JsonLineFormatter())
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return path
