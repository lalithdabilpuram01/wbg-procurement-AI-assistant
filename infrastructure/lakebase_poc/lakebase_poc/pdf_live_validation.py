"""Pre-execution checks and post-execution measurements for the approved real-PDF write. Never writes.

    uv run --locked python -m lakebase_poc.pdf_live_validation precheck-offline
    LAKEBASE_LIVE_TESTS=1 uv run --locked python -m lakebase_poc.pdf_live_validation precheck-live
    LAKEBASE_LIVE_TESTS=1 uv run --locked python -m lakebase_poc.pdf_live_validation measure

Results are saved to results/pdf_<command>_<UTC>.json without identities or credentials.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from pgvector.psycopg import register_vector
from psycopg import sql

from lakebase_poc import pdf_evidence, pdf_loader, pdf_retrieval_check as rc
from lakebase_poc.connection import connect
from lakebase_poc.credentials import databricks_cli
from lakebase_poc.pdf_schema import HNSW_INDEX, SCHEMA, TABLES
from lakebase_poc.settings import PROJECT_DIR, live_tests_enabled, load_settings

EXPECTED_COUNTS = {"documents": 1, "pages": 8, "sections": 17, "parent_chunks": 10, "retrieval_units": 39,
                   "unit_embeddings": 39, "citations": 39, "table_schemas": 1, "pdf_tables": 5, "pdf_table_rows": 42,
                   "pdf_table_values": 110}
EXPECTED_ENDPOINT = "projects/wbg-procurement-dev/branches/production/endpoints/primary"
EXPECTED_DATABASE = "databricks_postgres"


# ------------------------------------------------------------------------------------------------------- offline
def opened_files_while(build) -> tuple[Any, list[str]]:
    """Run `build` and record every file it opens below rag/corpus_v3/processed (Python audit hook)."""
    opened: list[str] = []
    root = str(pdf_evidence.PILOT.parent)

    def hook(event: str, args: tuple) -> None:
        if event == "open" and isinstance(args[0], (str, Path)) and str(args[0]).startswith(root):
            opened.append(str(args[0])[len(root) + 1:])
    sys.addaudithook(hook)                       # hooks cannot be removed; it only records paths
    result = build()
    return result, sorted(set(opened))


def precheck_offline() -> dict[str, Any]:
    """Rebuild the bundle offline and confirm the approved counts, hashes, cache key and privacy rules."""
    bundle, opened = opened_files_while(pdf_evidence.build_bundle)
    counts = {t: len(r) for t, r in bundle.records.items()}
    vectors = np.stack(bundle.vectors)
    core = pdf_evidence._retrieval_core()
    corpus = core.Corpus(pdf_evidence.STRATEGY, include_tables=True)
    key = hashlib.sha256(("\n".join(u.unit_id + u.text for u in corpus.units) + core.EMBED_MODEL).encode()).hexdigest()[:16]
    by_unit = {u.unit_id: u for u in corpus.units}
    checks = {
        "identity": bundle.checks["identity"],
        "record_counts": counts, "record_counts_as_approved": counts == EXPECTED_COUNTS, "total": sum(counts.values()),
        "vectors_768_finite_unit_unique": bundle.checks["vectors"],
        "units_text_hash_matches": all(r["indexed_text_sha256"] == hashlib.sha256(by_unit[r["unit_id"]].text.encode()).hexdigest()
                                       for r in bundle.records["retrieval_units"]),
        "embedding_sha256_matches_vectors": all(e["embedding_sha256"] == hashlib.sha256(v.tobytes()).hexdigest()
                                                for e, v in zip(bundle.records["unit_embeddings"], vectors)),
        "cache_key": key, "cache_file": bundle.checks["embedding_cache"],
        "cache_key_matches_file": bundle.checks["embedding_cache"] == f"emb_C_{len(corpus.units)}_{key}.npy",
        "strategy": pdf_evidence.STRATEGY, "model": core.EMBED_MODEL, "preprocessing": pdf_evidence.PREPROCESSING,
        "projects": bundle.checks["only_project"], "single_document_version": bundle.checks["single_document_version"],
        "files_opened_under_processed": opened,
        "restricted_opened": [f for f in opened if "/restricted/" in f or f.startswith("pilot_v2/restricted")],
        "residual_personal_data": bundle.checks["residual_personal_data"],
        "needs_review_rows": sorted({f"{v['table_id']}#{v['row_index']}" for v in bundle.records["pdf_table_values"]
                                     if v["quality"] == "needs_review"}),
        "table_schema_status": [s["status"] for s in bundle.records["table_schemas"]],
    }
    v = checks["vectors_768_finite_unit_unique"]
    vectors_ok = (v["count"] == 39 and v["dimensions"] == 768 and v["all_finite"] and v["unit_norm"]
                  and v["unique_vectors"] and v["aligned_with_units"])
    checks["all_passed"] = bool(
        checks["record_counts_as_approved"] and checks["total"] == 311 and vectors_ok
        and checks["units_text_hash_matches"] and checks["embedding_sha256_matches_vectors"]
        and checks["cache_key_matches_file"] and all(checks["identity"].values())
        and checks["projects"] == ["P148850"] and checks["single_document_version"]
        and not checks["restricted_opened"] and checks["table_schema_status"] == ["draft"]
        and not any(checks["residual_personal_data"].values()))
    return checks


# ---------------------------------------------------------------------------------------------------- live, read-only
def endpoint_host_matches(host: str) -> bool:
    """Read-only: the configured host belongs to the approved endpoint resource."""
    completed = subprocess.run([databricks_cli(), "postgres", "get-endpoint", EXPECTED_ENDPOINT, "-p", load_settings().databricks_profile,
                                "-o", "json"], capture_output=True, text=True, timeout=60, check=False)
    return completed.returncode == 0 and host in completed.stdout


def precheck_live() -> dict[str, Any]:
    """Read-only checks on the live database: approved endpoint and database, extensions, schemas, clean target."""
    settings = load_settings()
    with connect(settings, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_setting('server_version'), "
                        "(SELECT extversion FROM pg_extension WHERE extname = 'vector'), "
                        "(SELECT extversion FROM pg_extension WHERE extname = 'lakebase_vector')")
            database, server_version, vector, lakebase_vector = cur.fetchone()
            cur.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 1")
            schemas = [r[0] for r in cur.fetchall()]
        target = pdf_loader.inspect_target(conn)
    return {"endpoint_setting": settings.endpoint, "endpoint_is_approved": settings.endpoint == EXPECTED_ENDPOINT,
            "host_belongs_to_endpoint": endpoint_host_matches(settings.host), "database": database,
            "database_is_approved": database == EXPECTED_DATABASE, "server_version": server_version,
            "vector_extension": vector, "lakebase_vector_extension": lakebase_vector, "schemas": schemas,
            "target": target, "target_clean": not target["unexpected_relations"]}


def numeric_lookup_sql(conn, name: str) -> list[dict[str, Any]]:
    """Parameterised exact lookup of a typed indicator (complete rows only) with its page."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("""
            SELECT n.value_text AS indicator, v.field_name, v.value_text, v.value_numeric, v.unit, r.page, v.table_id, v.quality
            FROM {s}.pdf_table_values n
            JOIN {s}.pdf_table_values v ON v.table_id = n.table_id AND v.row_index = n.row_index
            JOIN {s}.pdf_table_rows r ON r.table_id = v.table_id AND r.row_index = v.row_index
            WHERE n.field_name = 'indicator_name' AND n.value_text = %s AND n.quality = 'complete_row'
            ORDER BY v.table_id, v.field_name""").format(s=sql.Identifier(SCHEMA)), (name,))
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def measure() -> dict[str, Any]:
    """Read-only measurements after the write: row counts, storage, HNSW index and plans, lookup, content match."""
    bundle = pdf_evidence.build_bundle()
    with connect(load_settings(), autocommit=True) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            counts = {}
            for table in TABLES:
                cur.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table)))
                counts[table] = cur.fetchone()[0]
            cur.execute("SELECT pg_database_size(current_database())")
            database_bytes = cur.fetchone()[0]
            cur.execute("SELECT c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1",
                        (SCHEMA,))
            tables = {name: {"total_bytes": total, "heap_bytes": heap} for name, total, heap in cur.fetchall()}
            cur.execute("SELECT pg_relation_size(%s::regclass)", (f"{SCHEMA}.{HNSW_INDEX}",))
            hnsw_bytes = cur.fetchone()[0]
            cur.execute("""SELECT i.relname, am.amname, ix.indisvalid, ix.indisready, opc.opcname FROM pg_index ix
                           JOIN pg_class i ON i.oid = ix.indexrelid JOIN pg_am am ON am.oid = i.relam
                           JOIN pg_opclass opc ON opc.oid = ix.indclass[0] WHERE i.relname = %s""", (HNSW_INDEX,))
            index = dict(zip(("name", "access_method", "valid", "ready", "operator_class"), cur.fetchone()))
            plans = {}
            query = rc.load_queries()[0].vector
            for label, force in (("default", False), ("seqscan_disabled", True)):
                with conn.transaction():
                    if force:
                        cur.execute("SET LOCAL enable_seqscan = off")
                    cur.execute(sql.SQL("EXPLAIN (FORMAT JSON) SELECT unit_id FROM {}.unit_embeddings ORDER BY embedding <=> %s LIMIT 10")
                                .format(sql.Identifier(SCHEMA)), (query,))
                    plan = cur.fetchone()[0]
                    plan = plan if isinstance(plan, list) else json.loads(plan)
                    nodes, stack = [], [plan[0]["Plan"]]
                    while stack:
                        node = stack.pop(0)
                        nodes.append(node["Node Type"] + (f" using {node['Index Name']}" if "Index Name" in node else ""))
                        stack.extend(node.get("Plans", []))
                    plans[label] = nodes
        lookup = numeric_lookup_sql(conn, "Roads constructed (Kilometers)")
        review = []
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT table_id, row_index, field_name, value_text FROM {}.pdf_table_values "
                                "WHERE quality = 'needs_review' ORDER BY 1, 2, 3").format(sql.Identifier(SCHEMA)))
            review = [dict(zip(("table_id", "row_index", "field_name", "value_text"), r)) for r in cur.fetchall()]
            cur.execute(sql.SQL("SELECT table_schema_id, status FROM {}.table_schemas").format(sql.Identifier(SCHEMA)))
            schemas = cur.fetchall()
        relational = sum(v["total_bytes"] for k, v in tables.items() if k != "unit_embeddings")
        return {"row_counts": counts, "row_counts_as_approved": counts == EXPECTED_COUNTS,
                "storage": {"database_bytes": database_bytes, "schema_tables_total_bytes": sum(v["total_bytes"] for v in tables.values()),
                            "unit_embeddings_total_bytes": tables["unit_embeddings"]["total_bytes"], "hnsw_index_bytes": hnsw_bytes,
                            "relational_tables_total_bytes": relational, "by_table": tables},
                "hnsw_index": index, "plans": plans, "numeric_lookup": lookup, "needs_review_values": review,
                "table_schemas": schemas, "stored_matches_prepared": pdf_loader.stored_matches(conn, bundle) == []}


def main(command: str) -> None:
    """Run one command (precheck-offline, precheck-live or measure), then save and print its result."""
    if command == "precheck-offline":
        result = precheck_offline()
    elif command in ("precheck-live", "measure"):
        if not live_tests_enabled():
            raise SystemExit("set LAKEBASE_LIVE_TESTS=1 for this read-only live command")
        result = precheck_live() if command == "precheck-live" else measure()
    else:
        raise SystemExit(__doc__)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = PROJECT_DIR / "results" / f"pdf_{command.replace('-', '_')}_{stamp}.json"
    out.write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result, indent=1, default=str))
    print(f"-> {out.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
