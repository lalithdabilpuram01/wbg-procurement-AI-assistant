"""Dry-run regeneration manifests for the approved Step 6A stacked-value fix (no vectors generated, store read-only).

    /opt/anaconda3/bin/python3 -B -m rag.embedding_cache.dry_run_step6a        (from procurement-intelligence/)

Writes results/dry_run_manifest_<scope>_p1_stacked.json for strategy C (the default) and strategy A, and
results/dry_run_step6a_summary.json. Recovered page-break values are not part of the stacked-only variant; the
summary also counts what the full Step 6A output would add, for information only (not manifested).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import variants
from .adapter import DEFAULT_STORE
from .identity import local_model_config
from .store import file_sha256, text_sha256
from .writer import dry_run, write_manifest

RESULTS = Path(__file__).resolve().parent / "results"
SCOPES = ("C+tables", "A+tables")


def run() -> dict:
    config = local_model_config()
    before = file_sha256(DEFAULT_STORE / "manifest.json")
    summary = {"store_manifest_sha256": before, "p1_rows_sha256": file_sha256(variants.P1_ROWS), "scopes": {}}
    for scope in SCOPES:
        stacked = variants.corpus(scope, "p1_stacked").units
        manifest = dry_run(DEFAULT_STORE, config, scope, stacked, f"step6a-stacked-{scope[0]}")
        write_manifest(manifest, RESULTS / f"dry_run_manifest_{scope[0]}_p1_stacked.json")
        base = {u.unit_id: text_sha256(u.text) for u in variants.corpus(scope, "pilot_v2").units}
        full_changed = sorted(u.unit_id for u in variants.corpus(scope, "p1_full").units if base[u.unit_id] != text_sha256(u.text))
        summary["scopes"][scope] = {"manifest_id": manifest["manifest_id"], "units_total": manifest["units_total"],
                                    "units_reused": manifest["units_reused"], "units_to_generate": manifest["units_to_generate"],
                                    "to_generate": [g["unit_id"] for g in manifest["to_generate"]],
                                    "recovered_value_units_not_manifested": sorted(set(full_changed) - {g["unit_id"] for g in manifest["to_generate"]})}
    summary["store_unchanged"] = file_sha256(DEFAULT_STORE / "manifest.json") == before
    (RESULTS / "dry_run_step6a_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
