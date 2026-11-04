"""PostgreSQL persistence for the static Table Registry (psycopg 3, parameterised SQL only).

RegistryWriter  loads a RegistryBundle and its embeddings (owner role, explicit initialisation only).
RegistryStore   reads the registry back for the agent (read-only role).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from structured_agent.registry.documents import SearchDocument
from structured_agent.registry.models import (BuildInfo, ClassificationInfo, Metric, RegisteredColumn, RegisteredObject,
                                              RegistryBundle, Relationship, Synonym, VerifiedSqlExample)
from structured_agent.registry.vector_index import NumpyCosineIndex, validate_vector

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"


class RegistryWriter:
    """Writes a complete registry build with the owner role; used only by init_registry."""

    def __init__(self, admin_dsn: str):
        self._dsn = admin_dsn

    def write(self, bundle: RegistryBundle, documents: list[SearchDocument], vectors: np.ndarray, model_id: str) -> None:
        """Recreate the registry schema and load everything in one transaction (all or nothing)."""
        dimensions = vectors.shape[1]
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL.read_text())
            self._insert_build(cur, bundle.build)
            for c in bundle.classifications:
                cur.execute("INSERT INTO registry.data_classifications VALUES (%s, %s, %s)",
                            (c.classification, c.description, c.source))
            for obj in bundle.objects:
                self._insert_object(cur, obj, bundle.build.build_id)
            for rel in bundle.relationships:
                self._insert_relationship(cur, rel)
            for m in bundle.metrics:
                cur.execute("""INSERT INTO registry.metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (m.metric_id, m.name, m.description, m.object_fqn, m.expression, m.unit, m.grain_note,
                             m.ambiguity_group, m.caveats, m.certified_source, m.source, m.status))
            for s in bundle.synonyms:
                cur.execute("INSERT INTO registry.synonyms VALUES (%s,%s,%s,%s,%s,%s)",
                            (s.term, s.target_kind, s.target_ref, s.note, s.source, s.status))
            for ex in bundle.examples:
                cur.execute("INSERT INTO registry.verified_sql_examples VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (ex.example_id, ex.question, ex.sql_text, Jsonb(ex.parameters), ex.objects, ex.metrics,
                             Jsonb(ex.expected_result) if ex.expected_result else None, ex.verification_status,
                             ex.verification_note))
            for doc, vector in zip(documents, vectors):
                unit_vector = validate_vector(vector, dimensions)
                cur.execute("INSERT INTO registry.description_embeddings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::real[])",
                            (doc.doc_id, doc.doc_kind, doc.object_fqn, doc.column_name, doc.metric_id, doc.text, model_id,
                             dimensions, unit_vector.tolist()))

    @staticmethod
    def _insert_build(cur, build: BuildInfo) -> None:
        cur.execute("INSERT INTO registry.registry_builds VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (build.build_id, build.built_at, build.databricks_snapshot, build.snapshot_sha256,
                     build.verification_file, build.curated_sha256, build.embedding_model, build.notes))

    @staticmethod
    def _insert_object(cur, obj: RegisteredObject, build_id: str) -> None:
        """Insert one object row and all of its column rows."""
        cur.execute("""INSERT INTO registry.registered_objects
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (obj.fqn, obj.logical_name, obj.layer, obj.object_type, obj.certified_aggregate, obj.short_description,
                     obj.description_source, obj.grain, obj.grain_columns, obj.grain_source, obj.grain_status, obj.row_count,
                     obj.authorized_roles, obj.authorized_operations,
                     Jsonb([f.model_dump() for f in obj.required_filters]), obj.limitations, Jsonb(obj.provenance), build_id))
        for col in obj.columns:
            cur.execute("INSERT INTO registry.registered_columns VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (obj.fqn, col.name, col.position, col.data_type, col.nullable, col.description,
                         col.description_source, col.classification, col.policy_source, col.unit, col.measure_family,
                         col.additive))

    @staticmethod
    def _insert_relationship(cur, rel: Relationship) -> None:
        """Insert one relationship row; required conditions and verification are stored as JSONB."""
        cur.execute("INSERT INTO registry.relationships VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (rel.relationship_id, rel.from_object, rel.from_columns, rel.to_object, rel.to_columns,
                     rel.cardinality, Jsonb([c.model_dump() for c in rel.required_conditions]), rel.aggregation_rule,
                     rel.source, rel.status, Jsonb(rel.verification)))


class RegistryStore:
    """Read-only access used by the agent at runtime."""

    def __init__(self, reader_dsn: str):
        self._dsn = reader_dsn

    def _fetch(self, cur, sql: str) -> list[dict]:
        """Run a query and return the rows as dicts keyed by column name."""
        cur.execute(sql)
        names = [d.name for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def load_bundle(self) -> RegistryBundle:
        """Load the newest registry build with all its objects, columns, joins, metrics, synonyms and examples."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            build = self._fetch(cur, "SELECT * FROM registry.registry_builds ORDER BY built_at DESC LIMIT 1")[0]
            columns: dict[str, list[RegisteredColumn]] = {}
            for row in self._fetch(cur, "SELECT * FROM registry.registered_columns ORDER BY object_fqn, position"):
                fqn = row.pop("object_fqn")
                columns.setdefault(fqn, []).append(RegisteredColumn(name=row.pop("column_name"), **row))
            objects = []
            for row in self._fetch(cur, "SELECT * FROM registry.registered_objects ORDER BY object_fqn"):
                fqn = row.pop("object_fqn")
                row.pop("build_id")
                objects.append(RegisteredObject(fqn=fqn, columns=columns.get(fqn, []), **row))
            return RegistryBundle(
                build=BuildInfo(**{**build, "built_at": build["built_at"].isoformat()}),
                classifications=[ClassificationInfo(**r) for r in self._fetch(cur, "SELECT * FROM registry.data_classifications")],
                objects=objects,
                relationships=[Relationship(**r) for r in self._fetch(cur, "SELECT * FROM registry.relationships ORDER BY relationship_id")],
                metrics=[Metric(**r) for r in self._fetch(cur, "SELECT * FROM registry.metrics ORDER BY metric_id")],
                synonyms=[Synonym(**r) for r in self._fetch(cur, "SELECT * FROM registry.synonyms")],
                examples=[VerifiedSqlExample(**r) for r in self._fetch(cur, "SELECT * FROM registry.verified_sql_examples ORDER BY example_id")])

    def load_documents_and_index(self) -> tuple[list[SearchDocument], NumpyCosineIndex]:
        """Load the search documents and their vectors; all vectors must come from one model and dimension."""
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            rows = self._fetch(cur, "SELECT * FROM registry.description_embeddings ORDER BY doc_id")
        models = {(r["model_id"], r["dimensions"]) for r in rows}
        if len(models) != 1:
            raise ValueError(f"Embeddings must come from exactly one model and dimension, found {models}")
        model_id, dimensions = models.pop()
        documents = [SearchDocument(doc_id=r["doc_id"], doc_kind=r["doc_kind"], object_fqn=r["object_fqn"], text=r["doc_text"],
                                    column_name=r["column_name"], metric_id=r["metric_id"]) for r in rows]
        vectors = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
        return documents, NumpyCosineIndex(model_id, dimensions, [d.doc_id for d in documents], vectors)

    def table_counts(self) -> dict[str, int]:
        """Row count of every registry table (used as a read-only connectivity check)."""
        tables = ("registry_builds", "data_classifications", "registered_objects", "registered_columns", "relationships",
                  "metrics", "synonyms", "verified_sql_examples", "description_embeddings", "audit_events")
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            counts = {}
            for table in tables:  # fixed allowlist of identifiers, not user input
                cur.execute(f"SELECT count(*) FROM registry.{table}")
                counts[table] = cur.fetchone()[0]
            return counts
