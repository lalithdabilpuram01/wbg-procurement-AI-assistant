"""Builds the RegistryBundle from the discovery snapshot, the live verification and the curated inputs.

Status rules:
  * object description: 'verified' text from the Databricks table comment;
  * grain: 'verified' when the live check found the documented grain columns unique, otherwise 'unverified';
  * relationship: 'verified' when the live check passed (orphans and cardinality), otherwise 'unverified';
  * metrics: 'curated' (grounded in kpi_queries.py / documentation) unless the object or columns are missing;
  * synonyms: 'unverified' until procurement SME review (targets are checked to exist);
  * SQL examples: 'curated' until the evaluation verifies them against a baseline.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from structured_agent.config import load_settings
from structured_agent.metadata.sources import (CURATED_DIR, CURATED_FILES, SNAPSHOT_DIR, VERIFICATION_DIR, curated, latest,
                                               object_grains, read_json, sha256_of)
from structured_agent.registry.column_policy import ColumnPolicyRules
from structured_agent.registry.models import (BuildInfo, ClassificationInfo, Metric, RegisteredColumn, RegisteredObject,
                                              RegistryBundle, Relationship, RequiredFilter, Synonym, VerifiedSqlExample)


def short_description(comment: str | None) -> str:
    """First sentence of the Databricks comment (the full comment is kept in provenance)."""
    if not comment:
        return "No description in Databricks."
    first = re.split(r"(?<=[a-z0-9)])\.\s", comment, maxsplit=1)[0]
    return first[:400]


def build_columns(snapshot_columns: list[dict], rules: ColumnPolicyRules) -> list[RegisteredColumn]:
    """Registered columns of one object, each with its policy (classification, unit, family, additivity)."""
    columns = []
    for col in snapshot_columns:
        policy = rules.policy_for(col["name"], col["data_type"])
        columns.append(RegisteredColumn(
            name=col["name"], position=col["position"], data_type=col["data_type"], nullable=col["nullable"],
            description=col["comment"],
            description_source="databricks_column_comment" if col["comment"] else "none (no Databricks column comment)",
            classification=policy.classification, policy_source=policy.policy_source, unit=policy.unit,
            measure_family=policy.measure_family, additive=policy.additive))
    return columns


def build_objects(snapshot: dict, verification: dict, rules: ColumnPolicyRules) -> list[RegisteredObject]:
    """One RegisteredObject per discovered table or view, merging curated settings over the layer defaults."""
    curated_objects = curated("objects.json")
    grains = object_grains(snapshot)
    objects = []
    for obj in snapshot["objects"]:
        fqn = obj["fqn"]
        layer = "gold" if obj["schema"] == "wbg_gold" else "silver"
        entry = {**curated_objects["defaults"][layer], **curated_objects["objects"].get(fqn, {})}
        grain = grains[fqn]
        grain_check = verification["grains"][fqn]
        objects.append(RegisteredObject(
            fqn=fqn,
            logical_name=entry.get("logical_name", obj["name"]),
            layer=layer,
            object_type=obj["object_type"],
            certified_aggregate=entry.get("certified_aggregate", False),
            short_description=short_description(obj["comment"]),
            description_source="databricks_table_comment" if obj["comment"] else "none",
            grain=grain.description,
            grain_columns=grain.columns,
            grain_source=grain.source,
            # The grain is only trusted for fan-out checks when the live uniqueness check passed.
            grain_status="verified" if grain_check["unique"] else "unverified",
            row_count=obj["row_count"],
            authorized_roles=entry["authorized_roles"],
            authorized_operations=entry["authorized_operations"],
            required_filters=[RequiredFilter(**f) for f in entry.get("required_filters", [])],
            limitations=entry.get("limitations", []),
            provenance={
                "databricks_comment": obj["comment"],
                "snapshot": snapshot["discovered_at"],
                "documentation_properties": {k: v for k, v in obj["properties"].items() if not k.startswith("delta.constraints")},
                "check_constraints": sorted(k.removeprefix("delta.constraints.") for k in obj["properties"]
                                            if k.startswith("delta.constraints")),
                "column_masks": obj["column_masks"],
                "grain_check": grain_check,
            },
            columns=build_columns(obj["columns"], rules)))
    return objects


def build_relationships(verification: dict) -> list[Relationship]:
    """Curated relationships, marked verified only when the live orphan and cardinality check passed."""
    relationships = []
    for rel in curated("relationships.json")["relationships"]:
        check = verification["relationships"][rel["id"]]
        relationships.append(Relationship(
            relationship_id=rel["id"], from_object=rel["from"], from_columns=rel["from_columns"], to_object=rel["to"],
            to_columns=rel["to_columns"], cardinality=rel["cardinality"], required_conditions=rel.get("required_conditions", []),
            aggregation_rule=rel["aggregation_rule"], source=rel["source"],
            status="verified" if check["passed"] else "unverified",
            verification={**check, "documented_exceptions": rel.get("documented_exceptions")}))
    return relationships


def build_metrics(objects: dict[str, RegisteredObject]) -> list[Metric]:
    """Curated metric definitions; a metric whose object is not registered is marked unverified."""
    metrics = []
    for m in curated("metrics.json")["metrics"]:
        status = "curated" if m["object_fqn"] in objects else "unverified"
        metrics.append(Metric(**m, status=status))
    return metrics


def target_exists(kind: str, ref: str, objects: dict[str, RegisteredObject], metric_ids: set[str]) -> bool:
    """True when a synonym's target (metric id, object fqn or object.column) exists in the registry."""
    if kind == "metric":
        return ref in metric_ids
    if kind == "object":
        return ref in objects
    fqn, _, column = ref.rpartition(".")
    return fqn in objects and objects[fqn].column(column) is not None


def build_synonyms(objects: dict[str, RegisteredObject], metric_ids: set[str]) -> list[Synonym]:
    """One Synonym per curated term; raises ValueError if any target does not exist."""
    config = curated("synonyms.json")
    synonyms = []
    for group in config["synonyms"]:
        if not target_exists(group["target_kind"], group["target_ref"], objects, metric_ids):
            raise ValueError(f"Synonym target does not exist in the registry: {group['target_ref']}")
        for term in group["terms"]:
            synonyms.append(Synonym(term=term, target_kind=group["target_kind"], target_ref=group["target_ref"],
                                    note=group.get("note"), source=config["source"], status="unverified"))
    return synonyms


def build_examples() -> list[VerifiedSqlExample]:
    """Curated SQL templates; a later evaluation run may have verified some of them against baselines."""
    verified = {}
    if any(VERIFICATION_DIR.glob("sql_examples_verification_*.json")):
        verified = read_json(latest(VERIFICATION_DIR, "sql_examples_verification"))["examples"]
    examples = []
    for ex in curated("verified_sql_examples.json")["examples"]:
        outcome = verified.get(ex["example_id"])
        if outcome and outcome["matched_baseline"]:
            status, note = "verified", f"Executed and matched an independent baseline ({outcome['run']})."
        else:
            status, note = "curated", "Reviewed template; not yet verified against a baseline."
        examples.append(VerifiedSqlExample(
            example_id=ex["example_id"], question=ex["question"], sql_text=ex["sql"], parameters=ex["parameters"],
            objects=ex["objects"], metrics=ex["metrics"], expected_result=ex.get("expected_result"),
            verification_status=status, verification_note=note))
    return examples


def build_bundle() -> RegistryBundle:
    """Assemble the full bundle from the latest snapshot and verification; they must belong together."""
    snapshot_path = latest(SNAPSHOT_DIR, "databricks_metadata")
    verification_path = latest(VERIFICATION_DIR, "metadata_verification")
    snapshot, verification = read_json(snapshot_path), read_json(verification_path)
    if verification["snapshot"] != snapshot_path.name:
        raise ValueError("The latest verification was run against a different snapshot: re-run verify_metadata.py")

    policy_config = curated("column_policy.json")
    rules = ColumnPolicyRules(policy_config)
    objects = build_objects(snapshot, verification, rules)
    by_fqn = {o.fqn: o for o in objects}
    metrics = build_metrics(by_fqn)

    built_at = datetime.now(timezone.utc)
    build = BuildInfo(
        build_id="registry_" + built_at.strftime("%Y%m%dT%H%M%SZ"), built_at=built_at.isoformat(timespec="seconds"),
        databricks_snapshot=snapshot_path.name, snapshot_sha256=sha256_of([snapshot_path]),
        verification_file=verification_path.name,
        curated_sha256=sha256_of([CURATED_DIR / f for f in CURATED_FILES + ("verified_sql_examples.json",)]),
        embedding_model=load_settings().embedding_model)
    classifications = [ClassificationInfo(classification=name, **info)
                       for name, info in policy_config["classifications"].items()]
    return RegistryBundle(build=build, classifications=classifications, objects=objects,
                          relationships=build_relationships(verification), metrics=metrics,
                          synonyms=build_synonyms(by_fqn, {m.metric_id for m in metrics}), examples=build_examples())
