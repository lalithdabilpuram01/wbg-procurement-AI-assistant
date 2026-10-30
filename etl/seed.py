"""Fill the ETL's landing Volume once, and set aside a slice of real contract awards for the live demo:
`uv run wbg-etl-seed` (add --dry-run to see what it would do).

  1. DS00005 contract awards are split by signing date: awards signed before --holdout-from go to the landing Volume,
     the rest become demo/structured/ds00005_new_contract_awards.csv (same header, records byte for byte). Uploading
     that file during the demo is "new awards arriving": Bronze -> Silver -> Gold -> the dashboard's FY2027 grows.
  2. Every other dataset is uploaded as it is, from databricks_upload/ (the cohort extracts and reference files).
  3. The document parser's three reference files go to the reference Volume.

Uploads go to /Volumes/<catalog>/etl/ only (never the `workspace` catalog). Re-running overwrites the same files.
Each file is copied to <folder>/<file name>; a file that an earlier run left where a folder belongs is replaced by the folder.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import tempfile
from pathlib import Path

from etl.cli import cli_path, workspace
from etl.datasets import PROCUREMENT, REFERENCE
from etl.stage import iter_csv_records, parse_record

REPO = Path(__file__).resolve().parents[1]
INPUTS = {"procurement": REPO / "databricks_upload" / "bronze_inputs", "reference": REPO / "databricks_upload" / "ref_bronze_inputs"}
CONTRACTS = "contract_awards_ds00005"
DEMO_FILE = REPO / "demo" / "structured" / "ds00005_new_contract_awards.csv"
PARSER_REFERENCE = (REPO / "rag/corpus_v3/processed/reference/contracts_snapshot_v3_35.jsonl",
                    REPO / "rag/corpus_v3/processed/reference/contracts_snapshot_v3_35.meta.json",
                    REPO / "rag/corpus_v3/processed/pilot_v3/reference/vocab.json")


def split_contracts(source: Path, seed: Path, demo: Path, holdout_from: dt.date) -> tuple[int, int]:
    """Records signed before `holdout_from` -> seed, the others -> demo; both keep the header. Returns the counts."""
    with source.open("r", encoding="utf-8", newline="") as fh:
        records = iter_csv_records(fh)
        header = next(records)
        signed = [c.lstrip("﻿") for c in parse_record(header)].index("Contract Signing Date")
        kept, held = [], []
        for raw in records:
            date = dt.datetime.strptime(parse_record(raw)[signed], "%m/%d/%Y").date()
            (held if date >= holdout_from else kept).append(raw)
    seed.write_text(header + "".join(kept), encoding="utf-8", newline="")
    demo.parent.mkdir(parents=True, exist_ok=True)
    demo.write_text(header + "".join(held), encoding="utf-8", newline="")
    return len(kept), len(held)


def ensure_folder(files, folder: str, dry_run: bool) -> None:
    """Make `folder` a directory: `fs cp file dir/` into a missing dir writes the file at the dir's path instead."""
    if is_file(files, folder):
        print(f"  {folder} is a file left by an earlier run: replacing it with a folder")
        if not dry_run:
            files.delete(folder)
    if not dry_run:
        files.create_directory(folder)


def is_file(files, path: str) -> bool:
    from databricks.sdk.errors import NotFound
    try:
        files.get_metadata(path)
        return True
    except NotFound:
        return False


def upload(path: Path, folder: str, profile: str, dry_run: bool) -> None:
    target = f"dbfs:{folder}/{path.name}"
    print(f"  {path.name} -> {target}")
    if not dry_run:
        subprocess.run([cli_path(), "fs", "cp", str(path), target, "-p", profile, "--overwrite"], check=True,
                       stdout=subprocess.DEVNULL)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wbg-etl-seed", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="wbg-databricks-free")
    parser.add_argument("--catalog", default="wbg_etl")
    parser.add_argument("--holdout-from", type=dt.date.fromisoformat, default=dt.date(2026, 9, 1),
                        help="contract awards signed on or after this date are kept for the demo upload")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    landing, reference = f"/Volumes/{args.catalog}/etl/landing", f"/Volumes/{args.catalog}/etl/reference"
    files = workspace(args.profile).files

    with tempfile.TemporaryDirectory() as tmp:
        source = next((INPUTS["procurement"] / CONTRACTS).glob("*.csv"))
        seed = Path(tmp) / source.name
        kept, held = split_contracts(source, seed, DEMO_FILE, args.holdout_from)
        print(f"{CONTRACTS}: {kept:,} records to landing, {held:,} kept for the demo in {DEMO_FILE.relative_to(REPO)}")
        ensure_folder(files, f"{landing}/{CONTRACTS}", args.dry_run)
        upload(seed, f"{landing}/{CONTRACTS}", args.profile, args.dry_run)
        for kind, datasets in (("procurement", PROCUREMENT), ("reference", REFERENCE)):
            for ds in datasets:
                if ds.folder == CONTRACTS:
                    continue
                print(ds.folder)
                ensure_folder(files, f"{landing}/{ds.folder}", args.dry_run)
                for path in sorted(p for p in (INPUTS[kind] / ds.folder).iterdir() if p.is_file() and not p.name.startswith(".")):
                    upload(path, f"{landing}/{ds.folder}", args.profile, args.dry_run)
    print("parser reference files")
    for path in PARSER_REFERENCE:
        upload(path, reference, args.profile, args.dry_run)
    print("done" + (" (dry run: nothing uploaded)" if args.dry_run else "; start the first run with: uv run wbg-etl-watch"))
    return 0


def run() -> int:
    """Console entry point (`uv run wbg-etl-seed`)."""
    import sys
    return main(sys.argv[1:])
