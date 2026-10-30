"""HTTP client with retry/backoff and an append-only access log.

Every request (success or failure) is written to evidence/access_log.jsonl so
that accessibility claims in the reports are backed by recorded results.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .config import SETTINGS

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_session: requests.Session | None = None


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{status} {url}: {message}")
        self.url, self.status, self.message = url, status, message


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = SETTINGS.user_agent
    return _session


def _log(entry: dict[str, Any]) -> None:
    SETTINGS.evidence_dir.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS.evidence_dir / "access_log.jsonl", "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def fetch(url: str, params: dict[str, Any] | None = None, *, stream: bool = False, purpose: str = "") -> requests.Response:
    """GET with exponential backoff + jitter on network errors and retryable statuses."""
    last_err: Exception | None = None
    for attempt in range(1, SETTINGS.max_retries + 1):
        started = time.monotonic()
        try:
            resp = session().get(url, params=params, timeout=SETTINGS.timeout_s, stream=stream)
            elapsed = round(time.monotonic() - started, 3)
            _log({"ts": utcnow(), "url": resp.url, "status": resp.status_code, "attempt": attempt,
                  "elapsed_s": elapsed, "content_type": resp.headers.get("Content-Type"), "purpose": purpose})
            if resp.status_code in RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else None
                raise FetchError(resp.url, resp.status_code, resp.text[:200]) if attempt == SETTINGS.max_retries else _RetrySignal(wait)
            if resp.status_code >= 400:
                raise FetchError(resp.url, resp.status_code, resp.text[:300])
            time.sleep(SETTINGS.polite_delay_s)
            return resp
        except _RetrySignal as sig:
            last_err = sig
            _sleep_backoff(attempt, sig.wait)
        except (requests.ConnectionError, requests.Timeout) as exc:
            _log({"ts": utcnow(), "url": url, "params": params, "status": None, "attempt": attempt,
                  "error": repr(exc)[:300], "purpose": purpose})
            last_err = exc
            if attempt == SETTINGS.max_retries:
                break
            _sleep_backoff(attempt, None)
    raise FetchError(url, None, f"exhausted retries: {last_err!r}")


class _RetrySignal(Exception):
    def __init__(self, wait: float | None):
        super().__init__("retry")
        self.wait = wait


def _sleep_backoff(attempt: int, server_hint: float | None) -> None:
    delay = server_hint if server_hint is not None else SETTINGS.backoff_base_s * (2 ** (attempt - 1))
    time.sleep(min(delay, 120) + random.uniform(0, 0.5))


def fetch_json(url: str, params: dict[str, Any] | None = None, *, purpose: str = "") -> Any:
    resp = fetch(url, params, purpose=purpose)
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(resp.url, resp.status_code, f"non-JSON body: {resp.text[:200]}") from exc


def download(url: str, dest, *, purpose: str = "", chunk: int = 1 << 20) -> int:
    """Stream a file to disk; returns bytes written. Writes to .part then renames."""
    from pathlib import Path

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    resp = fetch(url, stream=True, purpose=purpose)
    n = 0
    with open(tmp, "wb") as fh:
        for block in resp.iter_content(chunk):
            fh.write(block)
            n += len(block)
    tmp.rename(dest)
    return n
