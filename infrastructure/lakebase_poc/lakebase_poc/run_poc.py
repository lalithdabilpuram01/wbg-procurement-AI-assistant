"""Run the live pgvector proof of concept once and write a results file without identities or credentials.

Usage (from infrastructure/lakebase_poc/, explicit opt-in required):
    LAKEBASE_LIVE_TESTS=1 uv run --locked python -m lakebase_poc.run_poc
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import numpy as np

from lakebase_poc import pgvector_store as store
from lakebase_poc import synthetic
from lakebase_poc.connection import connect
from lakebase_poc.settings import PROJECT_DIR, live_tests_enabled, load_settings

TOP_K = 10
DISTANCE_TOLERANCE = 1e-5             # float32 storage vs float64 reference


def ranking_check(conn, items, pairs, force_index: bool, ef_search: int | None = None) -> dict[str, object]:
    exact_order, top1, recall, max_gap = True, 0, [], 0.0
    for source, query in pairs:
        reference = synthetic.cosine_distance_ranking(items, query, TOP_K)
        result = store.nearest(conn, query, TOP_K, force_index=force_index, ef_search=ef_search)
        exact_order &= [i for i, _ in result] == [i for i, _ in reference]
        top1 += result[0][0] == source
        recall.append(len({i for i, _ in result} & {i for i, _ in reference}) / TOP_K)
        max_gap = max(max_gap, max(abs(a - b) for (_, a), (_, b) in zip(result, reference)))
    return {"queries": len(pairs), "top1_is_source": top1, "same_order_as_numpy": exact_order,
            "recall_at_10_vs_numpy": recall, "max_distance_difference": max_gap,
            "distances_within_tolerance": max_gap <= DISTANCE_TOLERANCE}


def run() -> dict[str, object]:
    settings = load_settings()
    items = synthetic.items()
    pairs = synthetic.queries(items)
    with connect(settings) as conn:
        info = store.server_info(conn)
        store.enable_vector(conn)
        info_after = store.server_info(conn)
        store.create_table(conn)
        inserted_first = store.insert_items(conn, items)
        inserted_retry = store.insert_items(conn, items)              # idempotency: must insert nothing
        stored = store.fetch_embedding(conn, 77)
        exact = ranking_check(conn, items, pairs, force_index=False)  # before the index: exact scan
        store.create_hnsw_index(conn)
        store.create_hnsw_index(conn)                                 # idempotent
        catalog = store.index_catalog(conn)
        store.analyze(conn)
        default_plan = store.plan_nodes(conn, pairs[0][1], TOP_K, force_index=False)
        forced_plan = store.plan_nodes(conn, pairs[0][1], TOP_K, force_index=True)
        via_index = ranking_check(conn, items, pairs, force_index=True)                   # default ef_search (40)
        via_index_wide = ranking_check(conn, items, pairs, force_index=True, ef_search=200)
        result = {
            "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "server": {"version": info.version, "version_num": info.version_num,
                       "vector_available_version": info.vector_available,
                       "vector_installed_before": info.vector_installed, "vector_installed_after": info_after.vector_installed,
                       "lakebase_vector_installed": info_after.lakebase_vector_installed},
            "data": {"items": len(items), "dimensions": synthetic.DIMENSIONS, "seed": synthetic.SEED,
                     "inserted_first_run": inserted_first, "inserted_on_retry": inserted_retry,
                     "rows_in_table": store.row_count(conn),
                     "round_trip_equal": bool(np.allclose(stored, items[76].embedding, atol=1e-7))},
            "exact_search_before_index": exact,
            "hnsw": {"catalog": catalog, "default_plan": default_plan, "plan_with_seqscan_disabled": forced_plan,
                     "search_with_index_default_ef_search": via_index,
                     "search_with_index_ef_search_200": via_index_wide},
            "sizes": store.sizes(conn),
        }
    return result


def main() -> None:
    if not live_tests_enabled():
        sys.exit("Live Lakebase run not enabled: set LAKEBASE_LIVE_TESTS=1 for this invocation.")
    result = run()
    out = PROJECT_DIR / "results" / f"pgvector_poc_{result['run_at_utc'].replace(':', '').replace('-', '')[:15]}Z.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"results written to {out.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
