"""Stage the uploaded files for Bronze and write their provenance manifests (pure Python; no Spark).

For every upload folder:
  * procurement data: the cohort extract is cut exactly as the original staging did (discovery/wbdisc/bronze.py:
    row selection by project ID, bytes unchanged), so identical uploads give byte-identical extracts;
  * reference data: the file is copied unchanged.

Three manifests are written in the formats the Silver jobs already read:
  bronze_inputs_manifest.json      cohort extracts (output file, SHA-256, records, source file and SHA-256)
  source_pulls.jsonl               one line per uploaded procurement file (retrieval time, method, source)
  ref_bronze_inputs_manifest.json  reference files (SHA-256, records, source vintage, retrieval time, URL)

When an uploaded file is byte-identical to a Phase 1 download, its original provenance (retrieval time, vintage, URL) is
carried over; otherwise the upload time is recorded as the retrieval time.
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, COHORT_MANIFEST, REF_BRONZE_INPUTS_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST, SOURCE_PULLS_SAMPLES_MANIFEST
import csv
import gzip
import hashlib
import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from etl.datasets import DATASETS, PROCUREMENT, REFERENCE, SERIES_URLS, Dataset, series_code

REPO = Path(__file__).resolve().parents[1]
from wbdisc.bronze import iter_csv_records, parse_record, select_csv, select_jsonl  # noqa: E402  (the original selection)

COHORT = COHORT_MANIFEST
COHORT_SIZE = 2000
ORIGINAL_BRONZE = BRONZE_INPUTS_MANIFEST
ORIGINAL_REF = REF_BRONZE_INPUTS_MANIFEST
ORIGINAL_PULLS = (SOURCE_PULLS_CACHE_MANIFEST, SOURCE_PULLS_SAMPLES_MANIFEST)
SELECTION_RULE = ("exact string equality between the raw source key value and a cohort project_id "
                  "(no trimming, case-folding or other normalisation)")
MANIFEST_NAMES = {"bronze": "bronze_inputs_manifest.json", "pulls": "source_pulls.jsonl", "ref": "ref_bronze_inputs_manifest.json"}


class MissingUpload(Exception):
    """A dataset folder has no file: the job cannot build its Bronze table."""


@dataclass
class Staged:
    """Everything the Bronze loader needs: the staged file per table, and the three manifests."""
    files: dict[str, list[Path]] = field(default_factory=dict)  # dataset folder -> staged files
    bronze: dict = field(default_factory=dict)
    pulls: list[dict] = field(default_factory=list)
    ref: dict = field(default_factory=dict)

    def write_manifests(self, folder: Path) -> dict[str, Path]:
        folder.mkdir(parents=True, exist_ok=True)
        paths = {k: folder / name for k, name in MANIFEST_NAMES.items()}
        paths["bronze"].write_text(json.dumps(self.bronze, indent=1))
        paths["pulls"].write_text("".join(json.dumps(p) + "\n" for p in self.pulls))
        paths["ref"].write_text(json.dumps(self.ref, indent=1))
        return paths


# ------------------------------------------------------------------------------------------------ helpers
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc(ts: float | None = None) -> str:
    when = datetime.fromtimestamp(ts, timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return when.isoformat(timespec="seconds")


def uploaded(folder: Path, ds: Dataset) -> list[Path]:
    """The files that make up the dataset, oldest upload first.

    Procurement data: every file counts, so a small file of new rows adds to the dataset already there. Reference data:
    every file of an indicator series counts; for a single-file source (country list, income workbook) the newest wins.
    """
    files = sorted((p for p in folder.glob("*") if p.is_file() and not p.name.startswith((".", "_"))),
                   key=lambda p: (p.stat().st_mtime, p.name))
    if not files:
        raise MissingUpload(f"no file in {folder}: upload the {ds.folder} dataset ({ds.description})")
    return files if ds.cohort_bounded or ds.series else files[-1:]


def load_cohort(path: Path = COHORT) -> set[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        ids = [r["project_id"] for r in csv.DictReader(fh)]
    if len(ids) != COHORT_SIZE or len(set(ids)) != COHORT_SIZE:
        raise ValueError(f"cohort manifest must hold {COHORT_SIZE} unique project IDs, found {len(ids)} ({len(set(ids))} unique)")
    return set(ids)


@dataclass(frozen=True)
class Originals:
    """Provenance of the Phase 1 downloads, keyed by SHA-256, to carry over for byte-identical uploads."""
    phase1_names: dict[str, str]  # Bronze table -> name of its Phase 1 download (some Silver jobs look it up by name)
    pulls: dict[str, dict]
    ref: dict[str, dict]

    @classmethod
    def load(cls) -> Originals:
        names = {f["bronze_table"]: Path(f["source_file"]).name for f in json.loads(ORIGINAL_BRONZE.read_text())["files"]}
        pulls = {r["sha256"]: r for p in ORIGINAL_PULLS for r in map(json.loads, p.read_text().splitlines()) if r.get("sha256")}
        ref = {f["sha256"]: f for f in json.loads(ORIGINAL_REF.read_text())["files"]}
        return cls(names, pulls, ref)


# ---------------------------------------------------------------------------------------- procurement data
def stage_procurement(ds: Dataset, sources: list[Path], out_dir: Path, keep: set[str], originals: Originals) -> tuple[Path, dict, list[dict]]:
    """Cut the cohort extract of every uploaded file into ONE staged file (the first upload's name), so Bronze still has
    a single source file per table. Returns it with its bronze_inputs manifest entry and one source-pull line per file."""
    fmt = ds.kind
    dst = out_dir / ds.folder / f"{sources[0].name.split('.')[0]}__cohort{COHORT_SIZE}.{fmt}"
    parts = [dst.with_name(f"{dst.name}.part{i}") for i in range(len(sources))]
    results = [(select_csv if fmt == "csv" else select_jsonl)(src, part, ds.key, keep) for src, part in zip(sources, parts)]
    merge(parts, dst, fmt)
    shas = [sha256(src) for src in sources]
    selected = sum((r["selected_keys"] for r in results), Counter())
    total = {k: sum(r["stats"].get(k, 0) for r in results) for k in ("source_records", "selected_records", "field_count_differs_from_header")}
    entry = {
        "bronze_table": ds.tables[0].split(".")[1], "output_file": str(dst), "volume_path": str(dst), "format": fmt,
        "source_file": str(sources[-1]), "source_description": ds.description,
        "source_sha256": shas[0] if len(shas) == 1 else hashlib.sha256("\n".join(shas).encode()).hexdigest(),
        "source_files": [{"file": str(src), "sha256": sha, "records_selected": r["stats"].get("selected_records", 0)}
                         for src, sha, r in zip(sources, shas, results)],
        "source_manifest_checksum": "match" if all(sha in originals.pulls for sha in shas) else "uploaded (not only Phase 1 downloads)",
        "source_bytes": sum(src.stat().st_size for src in sources), "source_compression": "gzip" if sources[-1].suffix == ".gz" else "none",
        "selection_key": ds.key, "selection_rule": SELECTION_RULE, "coverage": ds.coverage,
        "source_records": total["source_records"], "records_selected": total["selected_records"],
        "cohort_projects_with_records": len(selected), "cohort_projects_without_records": COHORT_SIZE - len(selected),
        "max_records_per_project": max(selected.values()) if selected else 0,
        "output_bytes": dst.stat().st_size, "output_sha256": sha256(dst), "extracted_at_utc": utc(),
    }
    if fmt == "csv":
        entry.update({"columns": results[0]["columns"], "utf8_bom_preserved": results[0]["utf8_bom"],
                      "line_terminator": results[0]["line_terminator"],
                      "records_with_field_count_differing_from_header": total["field_count_differs_from_header"]})
    return dst, entry, [source_pull(ds, src, sha, originals) for src, sha in zip(sources, shas)]


def merge(parts: list[Path], dst: Path, fmt: str) -> None:
    """Concatenate per-file extracts, bytes unchanged. CSV: the first file's header once; every other file must carry
    the same columns (its header is dropped)."""
    columns = csv_columns(parts[0]) if fmt == "csv" else None
    with dst.open("wb") as out:
        for i, part in enumerate(parts):
            if fmt == "csv" and i > 0:
                with part.open("r", encoding="utf-8", newline="") as fh:
                    records = iter_csv_records(fh)
                    header = parse_record(next(records))
                    if [h.lstrip("\ufeff") for h in header] != columns:
                        raise MissingUpload(f"{part.name.split('.part')[0]}: an uploaded file has different columns {header}; "
                                            f"every file of a dataset must have the published header")
                    out.write("".join(records).encode("utf-8"))
            else:
                out.write(part.read_bytes())
            part.unlink()


def csv_columns(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [h.lstrip("\ufeff") for h in parse_record(next(iter_csv_records(fh)))]


def source_pull(ds: Dataset, src: Path, source_sha: str, originals: Originals) -> dict:
    """The Phase 1-style line the Silver jobs read for the source retrieval time. They find it by file name: the uploaded
    name, or (projects) the Phase 1 download name, so both are on the line."""
    names = {"file": f"uploads/{src.name}", "phase1_file": originals.phase1_names.get(ds.tables[0].split(".")[1], "")}
    known = originals.pulls.get(source_sha)
    if known:
        return {**known, **names}
    return {**names, "source": ds.description, "extraction_method": "uploaded to the ETL landing volume",
            "extracted_at_utc": utc(src.stat().st_mtime), "records": None, "bytes": src.stat().st_size, "sha256": source_sha,
            "notes": "uploaded file; retrieval time = upload time"}


# ------------------------------------------------------------------------------------------ reference data
def records_in(ds: Dataset, path: Path) -> int | str:
    """Records the Silver jobs count per file: lines of a JSON Lines series, entries of an API response."""
    if ds.kind == "xlsx":
        return ""
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    if ds.kind == "series":
        return sum(1 for ln in raw.decode("utf-8").splitlines() if ln.strip())
    body = json.loads(raw)
    return len(body[1]) if isinstance(body, list) and len(body) > 1 and isinstance(body[1], list) else 1


def vintage(ds: Dataset, path: Path) -> str:
    """Source "lastupdated" when the file publishes it (WDI response header); else the upload date."""
    if ds.kind == "wdi_observations":
        header = json.loads(path.read_bytes())[0]
        if header.get("lastupdated"):
            return header["lastupdated"]
    return utc(path.stat().st_mtime)[:10]


def stage_reference(ds: Dataset, src: Path, out_dir: Path, originals: Originals) -> tuple[Path, dict]:
    """Copy the file unchanged; return it with its ref_bronze_inputs manifest entry."""
    dst = out_dir / ds.folder / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    digest = sha256(dst)
    known = originals.ref.get(digest)
    code = series_code(ds.folder, src.name) or ""
    entry = {
        "bronze_table": ds.folder, "staged_file": str(dst), "volume_path": str(dst), "original_file": str(src),
        "format": "jsonl.gz" if src.name.endswith(".jsonl.gz") else src.suffix.lstrip("."), "bytes": dst.stat().st_size, "sha256": digest, "records": str(records_in(ds, dst)),
        "source_url": known["source_url"] if known else SERIES_URLS[ds.folder].format(code=code),
        "retrieved_utc": known["retrieved_utc"] if known else utc(src.stat().st_mtime),
        "source_lastupdated": known["source_lastupdated"] if known else vintage(ds, dst),
        "acquisition": known["acquisition"] if known else "uploaded to the ETL landing volume",
    }
    return dst, entry


# ------------------------------------------------------------------------------------------------- the step
def stage_all(landing: Path, out_dir: Path, cohort: Path = COHORT, originals: Originals | None = None) -> Staged:
    """Stage every dataset folder. Raises MissingUpload naming every empty folder."""
    originals = originals or Originals.load()
    keep = load_cohort(cohort)
    missing = [d.folder for d in DATASETS if not any(p.is_file() for p in (landing / d.folder).glob("*"))]
    if missing:
        raise MissingUpload(f"upload a file into each of these folders under {landing}: {', '.join(missing)}")
    staged = Staged(bronze={"purpose": "Cohort-bounded inputs for Bronze, cut by the ETL job (D-26). Row selection only.",
                            "cohort_manifest": "cohort/poc_cohort_2000_manifest.csv", "cohort_manifest_sha256": sha256(cohort),
                            "cohort_size": COHORT_SIZE, "selection_rule": SELECTION_RULE, "run_started_utc": utc(), "files": []},
                    ref={"purpose": "Country-level reference inputs (D-28: not cut to the cohort)", "staged_at_utc": utc(), "files": []})
    for ds in PROCUREMENT:
        dst, entry, pulls = stage_procurement(ds, uploaded(landing / ds.folder, ds), out_dir, keep, originals)
        staged.files[ds.folder] = [dst]
        staged.bronze["files"].append(entry)
        staged.pulls += pulls
    for ds in REFERENCE:
        for src in uploaded(landing / ds.folder, ds):
            dst, entry = stage_reference(ds, src, out_dir, originals)
            staged.files.setdefault(ds.folder, []).append(dst)
            staged.ref["files"].append(entry)
    staged.bronze["run_finished_utc"] = utc()
    return staged
