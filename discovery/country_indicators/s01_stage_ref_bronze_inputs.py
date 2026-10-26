"""Stage country-level reference files for workspace.wbg_ref_bronze (byte copies; offline).

Sources (read-only):
  discovery/country_indicators/raw/**            R1/R2 downloads (2026-10-22) + retrieval sidecars
  discovery/samples/indicators/*.jsonl.gz        R3: Phase 1 WGI and CPIA files, reused unchanged
Target: procurement-intelligence/databricks_upload/ref_bronze_inputs/ (separate from bronze_inputs/,
which holds the cohort-bounded procurement data). Unfiltered by cohort (D-28).
Every copy is verified by SHA-256; existing targets are never overwritten.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISC = HERE.parent
REPO = DISC.parent
RAW = HERE / "raw"
OUT = REPO / "databricks_upload" / "ref_bronze_inputs"
VOLUME = "/Volumes/workspace/default/wbg_procurement_test/ref_bronze_inputs"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def phase1_manifest() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for line in (DISC / "samples" / "MANIFEST.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "indicators/" in r["file"]:
            latest[r["file"]] = r
    return latest


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit(f"{OUT} is not empty; refusing to overwrite")
    plan: list[tuple[str, Path, dict]] = []
    for p in sorted((RAW / "wdi").glob("*.json")):
        if p.name.endswith(".meta.json"):
            continue
        table = ("wdi_indicator_observations" if p.name.startswith("observations_") else
                 "wdi_indicator_metadata" if p.name.startswith("indicator_") else
                 "wdi_source_metadata" if p.name.startswith("source_") else "wdi_country_metadata")
        plan.append((table, p, json.loads(Path(f"{p}.meta.json").read_text())))
    for p in sorted((RAW / "income_classification").glob("*.xlsx")):
        plan.append(("income_classification_history", p, json.loads(Path(f"{p}.meta.json").read_text())))
    p1 = phase1_manifest()
    for rel, m in sorted(p1.items()):
        p = DISC / rel
        table = "wgi_estimates" if p.name.startswith("GOV_WGI") else "cpia_ratings"
        plan.append((table, p, {"phase1_manifest": m}))

    rows = []
    for table, src, meta in plan:
        dst = OUT / table / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = sha256(src)
        shutil.copyfile(src, dst)
        after = sha256(dst)
        if before != after or sha256(src) != before:
            raise SystemExit(f"checksum mismatch staging {src}")
        if "phase1_manifest" in meta:
            m = meta["phase1_manifest"]
            note = m.get("notes", "")
            row = {"source_url": f"https://api.worldbank.org/v2/{m['extraction_method']}", "request_params": "per_page=20000 (Phase 1)",
                   "retrieved_utc": m["extracted_at_utc"], "source_lastupdated": note.split("'lastupdated': '")[1][:10] if "lastupdated" in note else "",
                   "records": m["records"], "phase1_manifest_sha256_match": m["sha256"] == before, "acquisition": "R3 reuse (Phase 1, unchanged)"}
        else:
            hdr = meta.get("api_page_header") or {}
            row = {"source_url": meta["final_url"], "request_params": json.dumps(meta["request_params"]),
                   "retrieved_utc": meta["retrieval_finished_utc"], "source_lastupdated": hdr.get("lastupdated", ""),
                   "records": meta.get("api_records_in_body", ""), "phase1_manifest_sha256_match": "",
                   "acquisition": "R2 download 2026-10-22" if table == "income_classification_history" else "R1 download 2026-10-22"}
            if meta["sha256"] != before:
                raise SystemExit(f"raw file {src} differs from its retrieval sidecar checksum")
            side = OUT / "_retrieval_metadata" / f"{src.name}.meta.json"
            side.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f"{src}.meta.json", side)
        rows.append({"bronze_table": table, "staged_file": str(dst.relative_to(REPO)), "volume_path": f"{VOLUME}/{table}/{src.name}",
                     "original_file": str(src.relative_to(REPO)),
                     "format": next(f for f in ("jsonl.gz", "json", "xlsx") if src.name.endswith("." + f)),
                     "bytes": dst.stat().st_size, "sha256": after, **row})
    mdir = OUT / "_manifest"
    mdir.mkdir(exist_ok=True)
    manifest = {"purpose": "Country-level reference inputs for workspace.wbg_ref_bronze (D-28: unfiltered by cohort)",
                "staged_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "target_volume": VOLUME,
                "files": rows}
    (mdir / "ref_bronze_inputs_manifest.json").write_text(json.dumps(manifest, indent=1))
    with (mdir / "ref_bronze_inputs_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['bronze_table']:32s} {r['format']:9s} {r['bytes']:>10,}  records={r['records']}  {Path(r['staged_file']).name}")
    print("files:", len(rows), "bytes:", sum(r["bytes"] for r in rows))


if __name__ == "__main__":
    main()
