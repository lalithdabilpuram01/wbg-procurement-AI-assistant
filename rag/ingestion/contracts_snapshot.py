"""Local, read-only snapshot of DS00005 contract records for the projects in the corpus (Silver contract_awards).

The pipeline links evidence against this snapshot, so a run is deterministic and needs no Databricks session. Refresh
with:  python -m rag.ingestion.contracts_snapshot [--v3]   (read-only SELECT; supplier names and IDs are not copied).
`--v3` writes the separate 35-project snapshot used by pilot_v3; the pilot snapshot is left unchanged.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone

from . import config

FIELDS = ["project_id", "wb_contract_number", "borrower_contract_reference", "procurement_category", "procurement_method", "review_type",
          "contract_signing_date", "contract_amount_usd", "source_as_of_date", "source_dataset"]


def paths(v3: bool = False) -> tuple:
    """(corpus manifest, snapshot file, metadata file) of the pilot corpus or of pilot_v3."""
    if v3:
        return config.CORPUS_MANIFEST_V3, config.CONTRACTS_SNAPSHOT_V3, config.CONTRACTS_SNAPSHOT_V3_META
    return config.CORPUS_MANIFEST, config.CONTRACTS_SNAPSHOT, config.CONTRACTS_SNAPSHOT_META


def refresh(v3: bool = False) -> dict:
    from pyspark.sql import functions as F

    from silver.common.session import get_spark
    manifest, snapshot, meta_path = paths(v3)
    with manifest.open(newline="") as fh:
        projects = sorted({r["project_id"] for r in csv.DictReader(fh)})
    s = get_spark()
    rows = [r.asDict() for r in s.table("workspace.wbg_silver.contract_awards").where(F.col("project_id").isin(projects)).select(*FIELDS).collect()]
    recs = sorted(({k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows), key=lambda r: (r["project_id"], r["wb_contract_number"]))
    config.REFERENCE.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs)
    snapshot.write_text(body)
    meta = {"source": "workspace.wbg_silver.contract_awards (read-only SELECT)", "projects": projects, "rows": len(recs), "fields": FIELDS,
            "sha256": hashlib.sha256(body.encode()).hexdigest(), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_as_of_dates": sorted({r["source_as_of_date"] for r in recs if r["source_as_of_date"]})}
    meta_path.write_text(json.dumps(meta, indent=1))
    return meta


def load(v3: bool = False) -> tuple[list[dict], dict]:
    _, snapshot, meta_path = paths(v3)
    meta = json.loads(meta_path.read_text())
    body = snapshot.read_text()
    if hashlib.sha256(body.encode()).hexdigest() != meta["sha256"]:
        raise RuntimeError("contracts snapshot does not match its recorded SHA-256")
    return [json.loads(x) for x in body.splitlines()], meta


if __name__ == "__main__":
    import sys
    print(json.dumps(refresh(v3="--v3" in sys.argv[1:]), indent=1))
