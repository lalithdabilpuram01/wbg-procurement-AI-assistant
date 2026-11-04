"""Starts the Lakeflow jobs as soon as a file is uploaded: `uv run wbg-etl-watch` (keep it running during a demo).

Databricks' own file-arrival trigger only watches external volumes; on Free Edition every volume is managed (Default
Storage), so the trigger is refused. This watcher does the same job from outside: every few seconds it lists the two
upload Volumes with the Files API (no cluster, no cost) and runs a job when

  - a file in its Volume is newer than the job's latest run, and
  - no run of the job is active, and
  - the uploads have been quiet for the settle time (a batch of files starts one run, not one per file).

It keeps no state of its own: the job's run history says what was already processed, so a file uploaded while the
watcher was stopped is picked up when it starts.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Callable, Iterable

ACTIVE_STATES = {"QUEUED", "PENDING", "RUNNING", "TERMINATING", "BLOCKED", "WAITING_FOR_RETRY"}


@dataclass(frozen=True)
class Watched:
    """One upload Volume and the job that a new file in it starts."""
    label: str
    job_name: str               # the job's name prefix (resources/*.yml)
    volume: str                 # upload Volume under /Volumes/<catalog>/etl/
    settle_seconds: int


WATCHED = (
    Watched("documents", "WBG document ingestion", "inbox", 20),
    Watched("data", "WBG procurement ETL", "landing", 90),
)


def due(latest_upload_ms: int | None, latest_run_start_ms: int | None, run_active: bool, now_ms: int, settle_seconds: int) -> bool:
    """Whether to start a run now (pure: the whole trigger rule)."""
    if latest_upload_ms is None or run_active:
        return False
    newer = latest_run_start_ms is None or latest_upload_ms > latest_run_start_ms
    return newer and now_ms - latest_upload_ms >= settle_seconds * 1000


def latest_upload(entries: Callable[[str], Iterable], folder: str) -> tuple[int | None, str | None]:
    """(modification time in ms, path) of the newest file under `folder`, walking sub-folders."""
    newest: tuple[int | None, str | None] = (None, None)
    for entry in entries(folder):
        if entry.is_directory:
            found = latest_upload(entries, entry.path)
        else:
            found = (entry.last_modified, entry.path)
        if found[0] is not None and (newest[0] is None or found[0] > newest[0]):
            newest = found
    return newest


class JobWatcher:
    """Polls the upload Volumes and starts each job when the trigger rule in due() says so."""
    def __init__(self, workspace, catalog: str, say: Callable[[str], None] = print):
        """Look up each watched job's id by its name prefix; exits when a job is not deployed."""
        self.w, self.catalog, self.say = workspace, catalog, say
        self.jobs = {}
        for watched in WATCHED:
            matches = [j for j in workspace.jobs.list() if (j.settings.name or "").startswith(watched.job_name)]
            if not matches:
                raise SystemExit(f"job '{watched.job_name}…' not found: deploy the bundle first (etl/README.md)")
            self.jobs[watched.label] = matches[0].job_id

    def _latest_run(self, job_id: int) -> tuple[int | None, bool, str]:
        """(start time in ms, still active, run page URL) of the job's latest run; (None, False, "") if it never ran."""
        for run in self.w.jobs.list_runs(job_id=job_id, limit=1):
            state = run.state.life_cycle_state.value if run.state and run.state.life_cycle_state else ""
            return run.start_time, state in ACTIVE_STATES, run.run_page_url or ""
        return None, False, ""

    def check(self, watched: Watched, now_ms: int) -> int | None:
        """Start the job when it is due; the new run id, or None."""
        folder = f"/Volumes/{self.catalog}/etl/{watched.volume}"
        uploaded_ms, path = latest_upload(self.w.files.list_directory_contents, folder)
        started_ms, active, _ = self._latest_run(self.jobs[watched.label])
        if not due(uploaded_ms, started_ms, active, now_ms, watched.settle_seconds):
            return None
        run = self.w.jobs.run_now(job_id=self.jobs[watched.label])
        page = self._latest_run(self.jobs[watched.label])[2]
        self.say(f"[{watched.label}] new upload {path} -> started run {run.run_id}  {page}")
        return run.run_id

    def loop(self, poll_seconds: int) -> None:
        """Check every watched Volume each `poll_seconds`, forever; a failed check is reported, not raised."""
        self.say(f"watching /Volumes/{self.catalog}/etl/inbox (documents) and /etl/landing (data); Ctrl+C stops")
        while True:
            for watched in WATCHED:
                try:
                    self.check(watched, int(time.time() * 1000))
                except Exception as error:                        # a network hiccup: report and keep watching
                    self.say(f"[{watched.label}] check failed: {type(error).__name__}: {str(error)[:200]}")
            time.sleep(poll_seconds)


def main(argv: list[str]) -> int:
    """Parse the arguments and watch the Volumes until Ctrl+C; return 0."""
    parser = argparse.ArgumentParser(prog="wbg-etl-watch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="wbg-databricks-free", help="Databricks CLI profile")
    parser.add_argument("--catalog", default="wbg_etl")
    parser.add_argument("--poll", type=int, default=10, help="seconds between checks")
    args = parser.parse_args(argv)
    from etl.cli import workspace
    try:
        JobWatcher(workspace(args.profile), args.catalog).loop(args.poll)
    except KeyboardInterrupt:
        print("stopped")
    return 0


def run() -> int:
    """Console entry point (`uv run wbg-etl-watch`)."""
    import sys
    return main(sys.argv[1:])
