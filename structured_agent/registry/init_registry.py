"""Explicit, repeatable initialisation of the static Table Registry in PostgreSQL.

Steps:
  1. build the RegistryBundle from the latest discovery snapshot, live verification and curated inputs;
  2. save the bundle as a reviewable JSON file next to the snapshots;
  3. embed object, column and metric descriptions with the local gte-base model (offline);
  4. recreate the `registry` schema and load everything in one transaction (owner role);
  5. reconnect with the read-only role and report the row count of every registry table.

There is no scheduler and no automatic re-synchronisation: re-run this script after an approved metadata change.
Usage (from procurement-intelligence/):
    /opt/anaconda3/bin/python3 -m structured_agent.registry.init_registry
"""
from __future__ import annotations

from structured_agent.config import load_settings
from structured_agent.metadata.sources import METADATA_DIR
from structured_agent.registry.build import build_bundle
from structured_agent.registry.documents import build_documents
from structured_agent.registry.store import RegistryStore, RegistryWriter
from structured_agent.registry.vector_index import EmbeddingModel


def main() -> None:
    settings = load_settings()
    bundle = build_bundle()
    bundle_path = METADATA_DIR / "bundles" / f"{bundle.build.build_id}.json"
    bundle_path.parent.mkdir(exist_ok=True)
    bundle_path.write_text(bundle.model_dump_json(indent=1))
    print(f"bundle {bundle.build.build_id}: {len(bundle.objects)} objects -> {bundle_path.name}")

    documents = build_documents(bundle)
    model = EmbeddingModel(settings.embedding_model)
    vectors = model.embed([d.text for d in documents])
    print(f"embedded {len(documents)} documents with {model.model_id} ({vectors.shape[1]} dimensions)")

    RegistryWriter(settings.registry_admin_dsn).write(bundle, documents, vectors, model.model_id)

    counts = RegistryStore(settings.registry_dsn).table_counts()
    print("read-only connectivity check (wbg_registry_reader):")
    for table, count in counts.items():
        print(f"  registry.{table}: {count}")


if __name__ == "__main__":
    main()
