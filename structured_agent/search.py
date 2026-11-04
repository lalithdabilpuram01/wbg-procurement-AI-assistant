"""Hybrid semantic discovery over the Table Registry.

Signals, combined per object:
  1. exact identifier match: a table, column or metric id written in the question;
  2. curated business synonyms found as phrases in the question;
  3. BM25 over object / column / metric descriptions;
  4. gte-base embedding similarity over the same descriptions (NumPy cosine; see vector_index.py).
Authorization filtering happens first: documents of objects or columns the principal may not use are never ranked.
BM25 and embedding ranks are fused with reciprocal-rank fusion (k = 60); exact and synonym matches add fixed bonuses.
Ambiguity: when the question only uses generic words for a metric group (e.g. "total value") and several registered
metrics of that group match, the search returns a clarification request instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from structured_agent.contracts import Ambiguity, JoinSummary, MetricMatch, TableCandidate
from structured_agent.policy import AccessPolicy, Principal
from structured_agent.registry.documents import SearchDocument
from structured_agent.registry.models import RegistryBundle
from structured_agent.registry.vector_index import EmbeddingModel, VectorIndex

RRF_K = 60
EXACT_BONUS = 0.05
SYNONYM_BONUS = 0.03
CERTIFIED_BONUS = 0.01        # tie-breaker: prefer certified KPI views for aggregate questions
AGGREGATE_WORDS = re.compile(r"\b(how many|total|count|number of|sum|average|median|by|per|share|trend)\b", re.I)
TOKEN = re.compile(r"[a-z0-9_]+")


def _singular(token: str) -> str:
    """Light plural normalisation so 'invitations for bids' matches 'invitation for bid'."""
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens with light plural normalisation, shared by BM25, exact matching and synonyms."""
    return [_singular(t) for t in TOKEN.findall(text.lower())]


@dataclass
class SynonymMatches:
    metric_terms: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)  # metric -> [(term, is_generic)]
    context_objects: set[str] = field(default_factory=set)                          # objects named by specific terms


@dataclass
class ObjectEvidence:
    """Running score, reasons and matched columns collected for one object during a search."""

    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    matched_columns: set[str] = field(default_factory=set)


class RegistrySearch:
    """Hybrid search over the registry documents, always filtered to what the principal may use."""

    def __init__(self, bundle: RegistryBundle, documents: list[SearchDocument], index: VectorIndex,
                 embedder: EmbeddingModel, policy: AccessPolicy):
        """Check that the index and query model match, then build the BM25 index over the documents."""
        # Query and document vectors must come from the same model, or the similarity scores mean nothing.
        if index.model_id != embedder.model_id:
            raise ValueError(f"Index built with {index.model_id} but query model is {embedder.model_id}")
        self._bundle = bundle
        self._docs = documents
        self._docs_by_id = {d.doc_id: d for d in documents}
        self._bm25 = BM25Okapi([tokenize(d.text) for d in documents])
        self._index = index
        self._embedder = embedder
        self._policy = policy

    # ------------------------------------------------------------------------------------------------ authorization
    def allowed_doc_ids(self, principal: Principal) -> set[str]:
        """Ids of the documents the principal may see: authorized objects, and only their usable columns."""
        allowed = set()
        for doc in self._docs:
            obj = self._bundle.object(doc.object_fqn)
            if obj is None or not self._policy.can_use_object(principal, obj):
                continue
            if doc.doc_kind == "column":
                column = obj.column(doc.column_name)
                if not (self._policy.can_use_column(principal, column, "output")
                        or self._policy.can_use_column(principal, column, "filter")):
                    continue
            allowed.add(doc.doc_id)
        return allowed

    # --------------------------------------------------------------------------------------------------------- search
    def search(self, question: str, principal: Principal, top_k: int = 5
               ) -> tuple[list[TableCandidate], list[MetricMatch], Ambiguity | None]:
        """Rank objects and metrics for a question; returns (top candidates, top metrics, ambiguity or None)."""
        allowed = self.allowed_doc_ids(principal)
        evidence: dict[str, ObjectEvidence] = {}
        metric_scores: dict[str, MetricMatch] = {}

        self._add_ranked(evidence, metric_scores, self._bm25_ranking(question, allowed), "keyword (BM25)")
        self._add_ranked(evidence, metric_scores, self._dense_ranking(question, allowed), "embedding similarity")
        self._add_exact(evidence, question, allowed)
        synonyms = self._add_synonyms(evidence, metric_scores, question, allowed)

        aggregate_question = bool(AGGREGATE_WORDS.search(question))
        for fqn, ev in evidence.items():
            if aggregate_question and self._bundle.object(fqn).certified_aggregate:
                ev.score += CERTIFIED_BONUS
                ev.reasons.append("certified KPI view preferred for an aggregate question")

        ranked = sorted(evidence.items(), key=lambda item: -item[1].score)[:top_k]
        candidates = [self._candidate(fqn, ev, principal) for fqn, ev in ranked]
        metrics = sorted(metric_scores.values(), key=lambda m: -m.score)[:5]
        return candidates, metrics, self._ambiguity(question, synonyms, metrics)

    def _bm25_ranking(self, question: str, allowed: set[str]) -> list[str]:
        """Document ids ranked by BM25 score, authorized and non-zero only, top 50."""
        scores = self._bm25.get_scores(tokenize(question))
        ranked = sorted(((s, d.doc_id) for s, d in zip(scores, self._docs) if d.doc_id in allowed and s > 0), reverse=True)
        return [doc_id for _, doc_id in ranked[:50]]

    def _dense_ranking(self, question: str, allowed: set[str]) -> list[str]:
        query_vector = self._embedder.embed([question])[0]
        return [hit.doc_id for hit in self._index.search(query_vector, allowed, top_k=50)]

    def _add_ranked(self, evidence: dict[str, ObjectEvidence], metrics: dict[str, MetricMatch], ranking: list[str],
                    method: str) -> None:
        """Reciprocal-rank fusion at object level: an object scores by its best-ranked document per method."""
        seen_objects: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            doc = self._docs_by_id[doc_id]
            contribution = 1.0 / (RRF_K + rank)
            if doc.doc_kind == "metric":
                match = metrics.setdefault(doc.metric_id, self._metric_match(doc.metric_id))
                match.score += contribution
                match.reasons.append(f"{method} rank {rank}")
            ev = evidence.setdefault(doc.object_fqn, ObjectEvidence())
            # Only columns from the 15 best-ranked documents are reported as matched, to keep the list focused.
            if doc.doc_kind == "column" and rank <= 15:
                ev.matched_columns.add(doc.column_name)
            if doc.object_fqn not in seen_objects:
                seen_objects.add(doc.object_fqn)
                ev.score += contribution
                ev.reasons.append(f"{method}: best match '{doc.text[:70]}' (rank {rank})")

    def _add_exact(self, evidence: dict[str, ObjectEvidence], question: str, allowed: set[str]) -> None:
        """Add a fixed bonus for identifiers (snake_case names or a full object name) written in the question."""
        tokens = set(tokenize(question))
        for doc in self._docs:
            if doc.doc_id not in allowed:
                continue
            name = doc.column_name or (doc.object_fqn.split(".")[-1] if doc.doc_kind == "object" else doc.metric_id)
            is_identifier = "_" in name  # ordinary words like 'year' are left to BM25 and embeddings
            if (is_identifier and _singular(name) in tokens) or (doc.doc_kind == "object" and doc.object_fqn in question.lower()):
                ev = evidence.setdefault(doc.object_fqn, ObjectEvidence())
                ev.score += EXACT_BONUS
                ev.reasons.append(f"exact name match: {name}")
                if doc.column_name:
                    ev.matched_columns.add(doc.column_name)

    def _add_synonyms(self, evidence: dict[str, ObjectEvidence], metrics: dict[str, MetricMatch], question: str,
                      allowed: set[str]) -> SynonymMatches:
        """Phrase-match curated synonyms; record what the ambiguity check needs."""
        text = " " + " ".join(tokenize(question)) + " "
        matched = SynonymMatches()
        for synonym in self._bundle.synonyms:
            if f" {' '.join(tokenize(synonym.term))} " not in text:
                continue
            generic = bool(synonym.note and synonym.note.startswith("ambiguous"))
            if synonym.target_kind == "metric":
                metric = self._bundle.metric(synonym.target_ref)
                if f"metric:{metric.metric_id}" not in allowed:
                    continue
                matched.metric_terms.setdefault(metric.metric_id, []).append((synonym.term, generic))
                match = metrics.setdefault(metric.metric_id, self._metric_match(metric.metric_id))
                match.score += SYNONYM_BONUS
                match.reasons.append(f"synonym '{synonym.term}'")
                fqn = metric.object_fqn
                if metric.certified_source:  # the certified KPI view that already holds this metric
                    view = metric.certified_source.rsplit(".", 1)[0]
                    view_ev = evidence.setdefault(view, ObjectEvidence())
                    view_ev.score += SYNONYM_BONUS
                    view_ev.reasons.append(f"certified view for metric {metric.metric_id} (synonym '{synonym.term}')")
            elif synonym.target_kind == "column":
                fqn, _, column = synonym.target_ref.rpartition(".")
                if f"column:{synonym.target_ref}" not in allowed:
                    continue
                evidence.setdefault(fqn, ObjectEvidence()).matched_columns.add(column)
            else:
                fqn = synonym.target_ref
                if f"object:{fqn}" not in allowed:
                    continue
            if not generic:
                matched.context_objects.add(fqn)
            ev = evidence.setdefault(fqn, ObjectEvidence())
            ev.score += SYNONYM_BONUS
            ev.reasons.append(f"business synonym '{synonym.term}' -> {synonym.target_ref.split('.')[-1]}")
        return matched

    def _metric_match(self, metric_id: str) -> MetricMatch:
        metric = self._bundle.metric(metric_id)
        return MetricMatch(metric_id=metric_id, name=metric.name, object_fqn=metric.object_fqn, score=0.0, reasons=[])

    # ---------------------------------------------------------------------------------------------------- ambiguity
    def _ambiguity(self, question: str, synonyms: SynonymMatches, metrics: list[MetricMatch]) -> Ambiguity | None:
        """Ambiguous when 2+ metrics of one group matched only through generic terms, none through a specific term,
        and the rest of the question does not point to exactly one of them (e.g. 'contracts ... what value')."""
        by_group: dict[str, list[str]] = {}
        for metric_id in synonyms.metric_terms:
            group = self._bundle.metric(metric_id).ambiguity_group
            if group:
                by_group.setdefault(group, []).append(metric_id)
        for group, metric_ids in by_group.items():
            specific = [m for m in metric_ids if any(not generic for _, generic in synonyms.metric_terms[m])]
            in_context = [m for m in metric_ids if self._bundle.metric(m).object_fqn in synonyms.context_objects]
            if len(in_context) == 1 and not specific:
                for match in metrics:
                    if match.metric_id == in_context[0]:
                        match.reasons.append("generic wording resolved by the other terms of the question")
                continue
            if len(metric_ids) >= 2 and not specific:
                options = [m for m in metrics if m.metric_id in metric_ids] or [self._metric_match(m) for m in metric_ids]
                return Ambiguity(group=group, options=options,
                                 message=f"'{question}' can mean several different measures ({', '.join(metric_ids)}); "
                                         "they are not interchangeable. Please choose one.")
        return None

    # --------------------------------------------------------------------------------------------------- candidates
    def _candidate(self, fqn: str, ev: ObjectEvidence, principal: Principal) -> TableCandidate:
        """Build the response entry for one object: authorized columns, related metrics and visible approved joins."""
        obj = self._bundle.object(fqn)
        authorized = [c.name for c in obj.columns if self._policy.can_use_column(principal, c, "output")]
        joins = [JoinSummary(relationship_id=r.relationship_id, from_object=r.from_object, from_columns=r.from_columns,
                             to_object=r.to_object, to_columns=r.to_columns, cardinality=r.cardinality,
                             aggregation_rule=r.aggregation_rule,
                             required_conditions=[c.model_dump() for c in r.required_conditions], status=r.status)
                 for r in self._bundle.relationships if fqn in (r.from_object, r.to_object)
                 and all(self._policy.can_use_object(principal, self._bundle.object(o)) for o in (r.from_object, r.to_object))]
        return TableCandidate(
            object_fqn=fqn, logical_name=obj.logical_name, layer=obj.layer, object_type=obj.object_type,
            certified_aggregate=obj.certified_aggregate, short_description=obj.short_description, grain=obj.grain,
            score=round(ev.score, 4), reasons=ev.reasons[:6], matched_columns=sorted(ev.matched_columns & set(authorized))[:12],
            authorized_columns=authorized, related_metrics=[m.metric_id for m in self._bundle.metrics if m.object_fqn == fqn],
            approved_joins=joins, limitations=obj.limitations,
            provenance={"description_source": obj.description_source, "grain_source": obj.grain_source,
                        "grain_status": obj.grain_status, "registry_build_id": self._bundle.build.build_id,
                        "databricks_snapshot": self._bundle.build.databricks_snapshot})
