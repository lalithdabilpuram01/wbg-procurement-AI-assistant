"""Command line.

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest <command>

    init                       create the inbox folders (ingestion_inbox/, one per document type)
    init-db                    create the Knowledge Graph schema wbg_kg on the Lakebase development branch
    run [--dry-run] [--no-embed]   ingest every pending file; --dry-run keeps everything in memory (no database)
    watch                      keep running: ingest files as they land
    status                     what the graph holds (files, nodes and edges by type and match class)
    lessons <contract>         lessons sections linked to a contract (same project, then same sector)
    neighbours <node> [--depth N]  the graph around a node, e.g. project:P148850

The graph lives on the development branch (WBG_KG_ENDPOINT overrides it); writes on `production` are refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from .layout import Inbox  # noqa: E402
from .pipeline import MemorySink, Pipeline  # noqa: E402

DEV_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"


def lakebase_store():
    from wbg_orchestrator.learning_db import dev_schema_pool

    from .store import KG_SCHEMA, KnowledgeGraphStore
    pool = dev_schema_pool(os.environ.get("WBG_KG_ENDPOINT", DEV_ENDPOINT), KG_SCHEMA, "kg")
    return KnowledgeGraphStore(pool.connection), pool


def bge_m3(batch_size: int = 16):
    """The same local bge-m3 model as the document search (normalised 1024-d vectors; no network)."""
    from rag.vector_store import encoder
    model = encoder.load_model()
    return lambda texts: model.model.encode(texts, normalize_embeddings=True, batch_size=batch_size,
                                            show_progress_bar=False).astype("float32").tolist()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingest", description="Automated multimodal ingestion")
    parser.add_argument("command", choices=["init", "init-db", "run", "watch", "status", "lessons", "neighbours"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-embed", action="store_true")
    parser.add_argument("--depth", type=int, default=2)
    args = parser.parse_args(argv)
    inbox = Inbox()
    if args.command == "init":
        print(json.dumps({"inbox": str(inbox.root), "created": [p.name for p in inbox.create()]}, indent=1))
        return 0
    if args.command == "watch" and args.dry_run:
        parser.error("--dry-run is for `run` (watch mode always writes to the graph)")
    if args.command == "run" and args.dry_run:
        report = Pipeline(inbox, MemorySink(), None if args.no_embed else bge_m3()).run()
        print(json.dumps(report, indent=1, default=str))
        return 0
    store, pool = lakebase_store()
    try:
        if args.command == "init-db":
            store.apply_schema()
            print(json.dumps({"schema": "wbg_kg", "tables": store.tables()}, indent=1))
        elif args.command in ("run", "watch"):
            pipeline = Pipeline(inbox, store, None if args.no_embed else bge_m3())
            if args.command == "run":
                print(json.dumps(pipeline.run(), indent=1, default=str))
            else:
                from .watch import watch
                watch(inbox, pipeline.run, on_report=lambda r: print(json.dumps({k: v for k, v in r.items() if k != "outcomes"}, default=str)))
        elif args.command == "status":
            print(json.dumps(store.stats(), indent=1))
        elif args.command == "lessons":
            print(json.dumps(store.lessons_for_contract(args.target), indent=1, default=str))
        else:
            print(json.dumps(store.neighbourhood(args.target, args.depth), indent=1, default=str))
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
