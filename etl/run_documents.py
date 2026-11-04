"""One task of the document ETL job: `python etl/run_documents.py <step> --catalog <catalog> --run-id <job run id>`.

  register   Bronze: new files in /Volumes/<catalog>/etl/inbox/<type folder>/ (pad, isr, icr, procurement_plans,
             contracts, notices, other), stored with their exact bytes
  parse      Silver: one document model per file, or quarantined with a reason
  enrich     Gold: entity links, proposed lesson cards, graph nodes and edges
  embed      Gold: one bge-m3 vector per chunk (downloads the model at the app's revision)
  publish    the subgraph, chunk vectors and lesson cards to Lakebase (development branch): the chunks are searchable in
             chat (pgvector) and the cards appear in the app's Review queue

See etl/documents.py.
"""
from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path

# Databricks runs this file through exec() without __file__; the compiled code still knows its path.
ROOT = Path(inspect.currentframe().f_code.co_filename).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STEPS = ("register", "parse", "enrich", "embed", "publish")


def main(argv: list[str]) -> int:
    """Run one document ETL step for the given catalog and run id; return the exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=STEPS)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--lakebase-endpoint", default="projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary")
    args = ap.parse_args(argv)
    volume = Path(f"/Volumes/{args.catalog}/etl")
    os.environ.setdefault("WBG_RAG_REFERENCE_DIR", str(volume / "reference"))     # contracts snapshot + vocabulary
    if args.step != "embed":                                                    # only embed downloads a model
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    from etl import documents
    from silver.common.session import get_spark

    spark = get_spark()
    if args.step == "register":
        documents.register(spark, args.catalog, volume / "inbox", args.run_id)
    elif args.step == "parse":
        documents.parse(spark, args.catalog, args.run_id)
    elif args.step == "enrich":
        documents.enrich(spark, args.catalog, args.run_id)
    elif args.step == "embed":
        documents.embed(spark, args.catalog, args.run_id)
    else:
        documents.publish(spark, args.catalog, args.run_id, args.lakebase_endpoint)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    if code:
        raise SystemExit(code)
