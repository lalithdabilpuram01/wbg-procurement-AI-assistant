"""Loaders for the inputs the registry is built from.

Inputs and what they contribute:
  * snapshots/databricks_metadata_*.json  live Databricks metadata (objects, columns, comments, constraints)
  * verification/metadata_verification_*.json  live checks of grains and relationships
  * curated/*.json  hand-curated fields, each grounded in a cited document
  * silver/runs/silver_audit_20261024T043611Z/audit_results.json  Silver grain and business keys
  * gold/procurement_kpis/kpi_queries.py  KPI view grains
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from structured_agent.config import PROJECT_DIR

METADATA_DIR = Path(__file__).resolve().parent
CURATED_DIR = METADATA_DIR / "curated"
SNAPSHOT_DIR = METADATA_DIR / "snapshots"
VERIFICATION_DIR = METADATA_DIR / "verification"
SILVER_AUDIT_FILE = PROJECT_DIR / "silver/runs/silver_audit_20261024T043611Z/audit_results.json"
CURATED_FILES = ("objects.json", "column_policy.json", "relationships.json", "metrics.json", "synonyms.json", "access_policy.json")


def latest(directory: Path, prefix: str) -> Path:
    files = sorted(directory.glob(f"{prefix}_*.json"))
    if not files:
        raise FileNotFoundError(f"No {prefix}_*.json in {directory}: run the earlier pipeline step first")
    return files[-1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def sha256_of(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def curated(name: str) -> dict:
    return read_json(CURATED_DIR / name)


@dataclass(frozen=True)
class Grain:
    description: str
    columns: list[str]
    source: str


def object_grains(snapshot: dict) -> dict[str, Grain]:
    """Documented grain of every discovered object (re-checked live by verify_metadata.py)."""
    from gold.procurement_kpis.kpi_queries import GRAINS as KPI_GRAINS

    curated_objects = curated("objects.json")["objects"]
    silver_inventory = read_json(SILVER_AUDIT_FILE)["inventory"]
    grains: dict[str, Grain] = {}
    for obj in snapshot["objects"]:
        fqn, name = obj["fqn"], obj["name"]
        entry = curated_objects.get(fqn, {})
        if obj["schema"] == "wbg_silver":
            audit = silver_inventory[name]
            grains[fqn] = Grain(audit["grain"], audit["key"], "Silver audit inventory (silver_audit_20261024T043611Z)")
        elif obj["object_type"] == "view":
            columns = KPI_GRAINS[name]
            description = "one row (headline totals)" if not columns else "one row per " + " x ".join(columns)
            grains[fqn] = Grain(description, columns, "gold/procurement_kpis/kpi_queries.py GRAINS")
        else:
            grains[fqn] = Grain(entry["grain"], entry["grain_columns"], entry["grain_source"])
    return grains
