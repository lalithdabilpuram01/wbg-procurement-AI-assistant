"""Typed records of the static Table Registry (one model per PostgreSQL registry table)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MetadataStatus = Literal["verified", "curated", "unverified"]
Classification = Literal["public", "lineage", "restricted", "prohibited"]


class ClassificationInfo(BaseModel):
    classification: Classification
    description: str
    source: str


class RegisteredColumn(BaseModel):
    name: str
    position: int
    data_type: str
    nullable: bool
    description: str | None
    description_source: str
    classification: Classification
    policy_source: str
    unit: str | None = None
    measure_family: str | None = None
    additive: bool = True


class RequiredFilter(BaseModel):
    column: str
    kind: Literal["equality"]
    reason: str


class RegisteredObject(BaseModel):
    fqn: str
    logical_name: str
    layer: Literal["silver", "gold"]
    object_type: Literal["table", "view"]
    certified_aggregate: bool
    short_description: str
    description_source: str
    grain: str
    grain_columns: list[str]
    grain_source: str
    grain_status: MetadataStatus
    row_count: int
    authorized_roles: list[str]
    authorized_operations: list[str]
    required_filters: list[RequiredFilter] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any]
    columns: list[RegisteredColumn]

    @property
    def name(self) -> str:
        return self.fqn.split(".")[-1]

    def column(self, name: str) -> RegisteredColumn | None:
        return next((c for c in self.columns if c.name == name), None)


class RequiredCondition(BaseModel):
    object: str
    column: str
    kind: Literal["equality"]
    reason: str


class Relationship(BaseModel):
    relationship_id: str
    from_object: str
    from_columns: list[str]
    to_object: str
    to_columns: list[str]
    cardinality: Literal["N:1", "1:1", "1:0..1"]
    required_conditions: list[RequiredCondition] = Field(default_factory=list)
    aggregation_rule: str
    source: str
    status: MetadataStatus
    verification: dict[str, Any]

    @property
    def from_side_unique(self) -> bool:
        return self.cardinality in ("1:1", "1:0..1")


class Metric(BaseModel):
    metric_id: str
    name: str
    description: str
    object_fqn: str
    expression: str
    unit: str
    grain_note: str
    ambiguity_group: str | None = None
    caveats: list[str] = Field(default_factory=list)
    certified_source: str | None = None
    source: str
    status: MetadataStatus


class Synonym(BaseModel):
    term: str
    target_kind: Literal["object", "column", "metric"]
    target_ref: str
    note: str | None = None
    source: str
    status: MetadataStatus


class VerifiedSqlExample(BaseModel):
    example_id: str
    question: str
    sql_text: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    objects: list[str]
    metrics: list[str] = Field(default_factory=list)
    expected_result: dict[str, Any] | None = None
    verification_status: MetadataStatus
    verification_note: str


class BuildInfo(BaseModel):
    build_id: str
    built_at: str
    databricks_snapshot: str
    snapshot_sha256: str
    verification_file: str
    curated_sha256: str
    embedding_model: str
    notes: str | None = None


class RegistryBundle(BaseModel):
    """Everything the registry holds, independent of the storage backend."""

    build: BuildInfo
    classifications: list[ClassificationInfo]
    objects: list[RegisteredObject]
    relationships: list[Relationship]
    metrics: list[Metric]
    synonyms: list[Synonym]
    examples: list[VerifiedSqlExample]

    def object(self, fqn: str) -> RegisteredObject | None:
        return next((o for o in self.objects if o.fqn == fqn), None)

    def metric(self, metric_id: str) -> Metric | None:
        return next((m for m in self.metrics if m.metric_id == metric_id), None)
