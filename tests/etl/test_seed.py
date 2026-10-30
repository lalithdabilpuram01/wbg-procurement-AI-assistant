"""The demo hold-out of contract awards (offline, a tiny synthetic CSV)."""
from __future__ import annotations

import datetime as dt

from etl.seed import ensure_folder, split_contracts, upload

HEADER = "﻿Project ID,Contract Description,Contract Signing Date\r\n"
ROWS = ['P000001,"Road works, lot 1\r\nwith a line break",08/31/2026\r\n', "P000002,Laptops,09/01/2026\r\n", "P000003,Audit,10/13/2026\r\n"]


def test_awards_from_the_holdout_date_go_to_the_demo_file_byte_for_byte(tmp_path):
    source, seed, demo = tmp_path / "ds00005.csv", tmp_path / "seed.csv", tmp_path / "demo" / "new.csv"
    source.write_text(HEADER + "".join(ROWS), encoding="utf-8", newline="")
    assert split_contracts(source, seed, demo, dt.date(2026, 9, 1)) == (1, 2)
    assert seed.read_bytes().decode("utf-8") == HEADER + ROWS[0]
    assert demo.read_bytes().decode("utf-8") == HEADER + ROWS[1] + ROWS[2]


class FakeFiles:
    """The two Files API calls the seed makes, over an in-memory set of file and folder paths."""

    def __init__(self, files=(), folders=()):
        self.files, self.folders = set(files), set(folders)

    def get_metadata(self, path):
        from databricks.sdk.errors import NotFound
        if path not in self.files:
            raise NotFound("not a file")

    def delete(self, path):
        self.files.remove(path)

    def create_directory(self, path):
        assert path not in self.files, "a file is in the way"
        self.folders.add(path)


def test_a_missing_folder_is_created():
    files = FakeFiles()
    ensure_folder(files, "/Volumes/c/etl/landing/wgi_estimates", dry_run=False)
    assert files.folders == {"/Volumes/c/etl/landing/wgi_estimates"}


def test_a_file_left_where_a_folder_belongs_is_replaced_by_the_folder():
    path = "/Volumes/c/etl/landing/projects_v3"
    files = FakeFiles(files={path})
    ensure_folder(files, path, dry_run=False)
    assert files.files == set() and files.folders == {path}


def test_a_dry_run_changes_nothing():
    path = "/Volumes/c/etl/landing/projects_v3"
    files = FakeFiles(files={path})
    ensure_folder(files, path, dry_run=True)
    assert files.files == {path} and files.folders == set()


def test_files_are_copied_into_the_folder_by_name(capsys, tmp_path):
    upload(tmp_path / "wgi.csv", "/Volumes/c/etl/landing/wgi_estimates", "profile", dry_run=True)
    assert "dbfs:/Volumes/c/etl/landing/wgi_estimates/wgi.csv" in capsys.readouterr().out
