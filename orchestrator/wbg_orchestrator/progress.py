"""Live events of the running jobs, for the streaming endpoint (GET /jobs/{job_id}/events). In memory, one API process.

Events, in order:
  {"type": "step", "key", "label", "state": running | done | failed | no_result, "ms"?}   a step of the investigation
  {"type": "text", "text"}                                                            a piece of a conversational reply
  {"type": "answer", "text"}                                                          the checked answer of an investigation
  {"type": "done", "state", "status"}                                                 the job ended (always the last event)
Metadata and answer text only: no SQL, no rows, no evidence facts. A reader gets everything from where it left off, so a
dropped connection loses nothing; finished streams are forgotten after a while.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

KEEP_FINISHED = 200                     # finished streams kept for late readers (the oldest are dropped first)


class JobStreams:
    """Thread-safe, in-memory event lists per job; readers block on a condition until new events or the end arrive."""
    def __init__(self) -> None:
        """Keeps the events per job, the closed jobs and the investigation-to-job map, guarded by one condition."""
        self._changed = threading.Condition()
        self._events: OrderedDict[str, list[dict]] = OrderedDict()
        self._closed: set[str] = set()
        self._job_of: dict[str, str] = {}                  # investigation id -> job id

    def open(self, job_id: str) -> None:
        with self._changed:
            self._events[job_id] = []

    def bind(self, investigation_id: str, job_id: str) -> None:
        """Steps reported for this investigation belong to this job."""
        with self._changed:
            self._job_of[investigation_id] = job_id

    def publish(self, job_id: str, event: dict) -> None:
        """Append an event to an open job's stream and wake waiting readers; ignored for unknown or closed jobs."""
        with self._changed:
            if job_id in self._events and job_id not in self._closed:
                self._events[job_id].append(event)
                self._changed.notify_all()

    def publish_investigation(self, investigation_id: str, event: dict) -> None:
        """Publish an event to the job bound to this investigation, if any."""
        job_id = self._job_of.get(investigation_id)
        if job_id:
            self.publish(job_id, event)

    def close(self, job_id: str, final: dict) -> None:
        """End a job's stream with a final "done" event; only the first close counts."""
        with self._changed:
            if job_id in self._events and job_id not in self._closed:
                self._events[job_id].append({"type": "done", **final})
                self._closed.add(job_id)
                self._job_of = {i: j for i, j in self._job_of.items() if j != job_id}
                self._forget_old()
                self._changed.notify_all()

    def read(self, job_id: str, after: int, timeout: float) -> tuple[list[dict], bool]:
        """Events after the first `after`, waiting up to `timeout` seconds for new ones; and whether the stream ended."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                events = self._events.get(job_id)
                if events is None:
                    return [], True
                if len(events) > after or job_id in self._closed:
                    return events[after:], job_id in self._closed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], False
                self._changed.wait(remaining)

    def _forget_old(self) -> None:
        """Drop the oldest finished streams so at most KEEP_FINISHED are kept; running streams are never dropped."""
        finished = [j for j in self._events if j in self._closed]
        for job_id in finished[:-KEEP_FINISHED]:
            del self._events[job_id]
            self._closed.discard(job_id)
