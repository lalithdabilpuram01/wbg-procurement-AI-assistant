"""Writes METADATA_INVENTORY.md from the latest registry bundle (reproducible; no Databricks access needed).

Usage (from procurement-intelligence/):
    /opt/anaconda3/bin/python3 -m structured_agent.metadata.write_inventory_report
"""
from __future__ import annotations

from collections import Counter

from structured_agent.metadata.sources import METADATA_DIR
from structured_agent.registry.models import RegistryBundle


def load_latest_bundle() -> RegistryBundle:
    path = sorted((METADATA_DIR / "bundles").glob("registry_*.json"))[-1]
    return RegistryBundle.model_validate_json(path.read_text())


def object_rows(bundle: RegistryBundle) -> list[str]:
    rows = ["| Object | Type | Rows | Cols | Grain (columns) | Grain status / source | Description source | CHECKs | Non-public columns |",
            "|---|---|---:|---:|---|---|---|---:|---|"]
    for o in sorted(bundle.objects, key=lambda o: (o.layer != "gold", o.object_type, o.fqn)):
        classes = Counter(c.classification for c in o.columns)
        sensitive = ", ".join(f"{k} {v}" for k, v in sorted(classes.items()) if k != "public") or "none"
        rows.append(f"| `{o.fqn.removeprefix('workspace.')}` | {o.layer} {o.object_type}{' (certified KPI)' if o.certified_aggregate else ''} "
                    f"| {o.row_count:,} | {len(o.columns)} | {o.grain} (`{', '.join(o.grain_columns) or '-'}`) "
                    f"| {o.grain_status}: {o.grain_source} | {o.description_source} "
                    f"| {len(o.provenance.get('check_constraints', []))} | {sensitive} |")
    return rows


def sensitive_rows(bundle: RegistryBundle) -> list[str]:
    rows = ["| Column | Classification | Policy source |", "|---|---|---|"]
    for o in bundle.objects:
        for c in o.columns:
            if c.classification in ("prohibited", "restricted"):
                rows.append(f"| `{o.name}.{c.name}` | {c.classification} | {c.policy_source} |")
    return rows


def relationship_rows(bundle: RegistryBundle) -> list[str]:
    rows = ["| Relationship | Join | Cardinality | Live check | Status | Source |", "|---|---|---|---|---|---|"]
    for r in bundle.relationships:
        v = r.verification
        check = (f"orphans {v.get('orphans', v.get('reverse_orphans'))}, max to-rows/key {v['max_to_rows_per_key']}"
                 + (f", unmatched from-rows (coverage) {v['unmatched_from_rows_coverage']:,}" if "unmatched_from_rows_coverage" in v else "")
                 + (f", NULL from-keys {v['from_null_keys']:,}" if v.get("from_null_keys") else ""))
        condition = f" + {', '.join(c.column for c in r.required_conditions)} fixed" if r.required_conditions else ""
        rows.append(f"| `{r.relationship_id}` | `{r.from_object.split('.')[-1]}.{','.join(r.from_columns)}` = "
                    f"`{r.to_object.split('.')[-1]}.{','.join(r.to_columns)}`{condition} | {r.cardinality} | {check} | {r.status} | {r.source} |")
    return rows


def main() -> None:
    bundle = load_latest_bundle()
    columns = [c for o in bundle.objects for c in o.columns]
    lines = [
        "# Metadata inventory (Structured Data Agent)", "",
        f"Generated from registry build `{bundle.build.build_id}` (Databricks snapshot `{bundle.build.databricks_snapshot}`, "
        f"live verification `{bundle.build.verification_file}`).", "",
        "**Status labels.** *verified* = read from Databricks metadata or confirmed by a live read-only data check; "
        "*curated* = written by hand and grounded in the cited code or document; *unverified* = needs review.", "",
        "## Summary", "",
        f"- Objects: {len(bundle.objects)} ({sum(o.layer == 'silver' for o in bundle.objects)} Silver tables, "
        f"{sum(o.layer == 'gold' and o.object_type == 'table' for o in bundle.objects)} Gold tables, "
        f"{sum(o.object_type == 'view' for o in bundle.objects)} Gold KPI views). Bronze and reference-Bronze schemas are not registered.",
        f"- Columns: {len(columns)}; classifications: " + ", ".join(f"{k} {v}" for k, v in Counter(c.classification for c in columns).most_common()),
        f"- Columns with a Databricks comment: {sum(c.description_source == 'databricks_column_comment' for c in columns)} "
        f"(others have no description; none was invented).",
        f"- Grains verified unique on live data: {sum(o.grain_status == 'verified' for o in bundle.objects)}/{len(bundle.objects)}.",
        f"- Relationships verified on live data: {sum(r.status == 'verified' for r in bundle.relationships)}/{len(bundle.relationships)}.",
        f"- Metrics: {len(bundle.metrics)} (status: " + ", ".join(f"{k} {v}" for k, v in Counter(m.status for m in bundle.metrics).items()) + ").",
        f"- Business synonyms: {len(bundle.synonyms)} terms (status: unverified until procurement SME review; every target exists).",
        f"- SQL examples: {len(bundle.examples)} (" + ", ".join(f"{k} {v}" for k, v in Counter(e.verification_status for e in bundle.examples).items()) + ").",
        "- Column masks: `wbg_silver.procurement_notice_details.notice_text` (D-14). Row filters: none.", "",
        "## Objects", "", *object_rows(bundle), "",
        "## Personal, sensitive and restricted columns", "",
        "Prohibited columns are never selectable, filterable or searchable; restricted columns may be used by data stewards in "
        "filters and joins only. Lineage columns are visible to data stewards only.", "", *sensitive_rows(bundle), "",
        "## Approved relationships", "", *relationship_rows(bundle), "",
        "## Metrics", "", "| Metric | Object | Expression | Unit | Ambiguity group | Status | Source |", "|---|---|---|---|---|---|---|",
        *[f"| `{m.metric_id}` | `{m.object_fqn.split('.')[-1]}` | `{m.expression}` | {m.unit} | {m.ambiguity_group or '-'} | {m.status} | {m.source} |"
          for m in bundle.metrics], "",
        "## Items requiring review", "",
        "- All business synonyms (procurement SME review).",
        "- Metric definitions are grounded in `kpi_queries.py` and the Silver relationship report, but the dashboard KPI decision D-23 is still pending.",
        "- Column descriptions missing in Databricks remain empty; adding them needs an approved Databricks comment change.",
        "- Join lags for country context are not approved; the registry requires an explicit year instead of assuming one.",
    ]
    out = METADATA_DIR / "METADATA_INVENTORY.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
