"""The upload watcher's trigger rule (offline: no Databricks)."""
from __future__ import annotations

from types import SimpleNamespace

from etl.watch import due, latest_upload

MINUTE = 60_000


def test_a_new_upload_starts_a_run_once_the_uploads_are_quiet():
    assert due(latest_upload_ms=10 * MINUTE, latest_run_start_ms=5 * MINUTE, run_active=False, now_ms=11 * MINUTE, settle_seconds=30)
    assert not due(10 * MINUTE, 5 * MINUTE, False, 10 * MINUTE + 10_000, 30)          # still uploading
    assert due(10 * MINUTE, None, False, 11 * MINUTE, 30)                              # the job never ran


def test_nothing_new_or_a_running_job_starts_nothing():
    assert not due(4 * MINUTE, 5 * MINUTE, False, 11 * MINUTE, 30)                     # already processed
    assert not due(10 * MINUTE, 5 * MINUTE, True, 11 * MINUTE, 30)                     # a run is active (it is queued)
    assert not due(None, None, False, 11 * MINUTE, 30)                                 # empty Volume


def test_the_newest_file_is_found_in_sub_folders():
    tree = {"/v": [SimpleNamespace(is_directory=True, path="/v/icr", last_modified=None),
                   SimpleNamespace(is_directory=False, path="/v/a.pdf", last_modified=5)],
            "/v/icr": [SimpleNamespace(is_directory=False, path="/v/icr/new.pdf", last_modified=9)]}
    assert latest_upload(lambda folder: tree[folder], "/v") == (9, "/v/icr/new.pdf")
