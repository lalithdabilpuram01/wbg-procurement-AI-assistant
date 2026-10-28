"""Command line.

    cd orchestrator
    env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons <command>

    preview            the cards the pilot corpus would give (no database): counts by source, stage and topic
    init-db            create the lesson_card table in the Knowledge Graph schema (wbg_kg) on the development branch
    backfill           propose the pilot corpus's cards (existing decisions are kept; new documents get theirs on ingestion)
    status             cards by review status

The cards live on the development branch (WBG_KG_ENDPOINT overrides it); writes on `production` are refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from .corpus import corpus_cards
from .store import PostgresLessonStore

DEV_ENDPOINT = "projects/wbg-procurement-dev/branches/dev-orchestrator/endpoints/primary"
KG_SCHEMA = "wbg_kg"


def lakebase_store():
    from wbg_orchestrator.learning_db import dev_schema_pool
    pool = dev_schema_pool(os.environ.get("WBG_KG_ENDPOINT", DEV_ENDPOINT), KG_SCHEMA, "lessons")
    return PostgresLessonStore(pool.connection, KG_SCHEMA), pool


def preview() -> dict:
    cards = corpus_cards()
    return {"cards": len(cards), "projects": len({c.project_id for c in cards}), "by_source": Counter(c.doc_type for c in cards),
            "by_stage": Counter(s for c in cards for s in c.stages), "by_topic": Counter(t for c in cards for t in c.topics)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m lessons", description="Lesson cards (Continuous Learning)")
    parser.add_argument("command", choices=["preview", "init-db", "backfill", "status"])
    args = parser.parse_args(argv)
    if args.command == "preview":
        print(json.dumps(preview(), indent=1))
        return 0
    store, pool = lakebase_store()
    try:
        if args.command == "init-db":
            store.apply_schema()
            print(json.dumps({"schema": KG_SCHEMA, "table": "lesson_card", "counts": store.counts()}, indent=1))
        elif args.command == "backfill":
            print(json.dumps({"proposed_or_refreshed": store.upsert(corpus_cards()), "counts": store.counts()}, indent=1))
        else:
            print(json.dumps(store.counts(), indent=1))
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
