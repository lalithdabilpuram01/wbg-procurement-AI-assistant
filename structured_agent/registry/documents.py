"""Text documents describing registry objects, columns and metrics, used for lexical and embedding search."""
from __future__ import annotations

from dataclasses import dataclass

from structured_agent.registry.models import RegistryBundle


@dataclass(frozen=True)
class SearchDocument:
    doc_id: str
    doc_kind: str          # object | column | metric
    object_fqn: str
    text: str
    column_name: str | None = None
    metric_id: str | None = None


def _humanise(identifier: str) -> str:
    return identifier.replace("_", " ")


def build_documents(bundle: RegistryBundle) -> list[SearchDocument]:
    """One document per object, per non-prohibited column and per metric.

    Prohibited columns are never described in searchable text, so search can never surface them.
    """
    synonyms_by_target: dict[str, list[str]] = {}
    for s in bundle.synonyms:
        synonyms_by_target.setdefault(s.target_ref, []).append(s.term)

    docs: list[SearchDocument] = []
    for obj in bundle.objects:
        terms = " ".join(synonyms_by_target.get(obj.fqn, []))
        docs.append(SearchDocument(
            doc_id=f"object:{obj.fqn}", doc_kind="object", object_fqn=obj.fqn,
            text=f"{obj.logical_name}. {_humanise(obj.name)}. {obj.short_description}. Grain: {obj.grain}. {terms}"))
        for col in obj.columns:
            if col.classification == "prohibited":
                continue
            ref = f"{obj.fqn}.{col.name}"
            terms = " ".join(synonyms_by_target.get(ref, []))
            docs.append(SearchDocument(
                doc_id=f"column:{ref}", doc_kind="column", object_fqn=obj.fqn, column_name=col.name,
                text=f"{_humanise(col.name)} ({obj.logical_name}). {col.description or ''} {terms}".strip()))
    for metric in bundle.metrics:
        terms = " ".join(synonyms_by_target.get(metric.metric_id, []))
        docs.append(SearchDocument(
            doc_id=f"metric:{metric.metric_id}", doc_kind="metric", object_fqn=metric.object_fqn, metric_id=metric.metric_id,
            text=f"{metric.name}. {metric.description} {terms}".strip()))
    return docs
