"""Encode the existing benchmark questions whose gold evidence is in the POC document, with the existing retrieval
model and settings (offline, local Hugging Face cache). Run with the Anaconda interpreter, which has
sentence-transformers; the uv environment is not changed:

    HF_HUB_OFFLINE=1 /opt/anaconda3/bin/python3 -B -m lakebase_poc.pdf_queries      (from infrastructure/lakebase_poc/)

Writes results/pdf_poc_queries.npz (question ids, texts, gold pages, 768-d normalised vectors). No document is
re-embedded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO = PROJECT_DIR.parents[1]
DOCUMENT_ID = "40089196"
OUTPUT = PROJECT_DIR / "results" / "pdf_poc_queries.npz"


def benchmark_questions() -> list[dict]:
    """The benchmark questions whose gold evidence is in the POC document, with their gold pages."""
    benchmark = json.loads((REPO / "rag" / "retrieval" / "benchmark_v1.json").read_text())
    selected = []
    for question in benchmark["questions"]:
        pages = sorted({p for gold in question["gold"] if gold["document_id"] == DOCUMENT_ID for p in gold["pages"]})
        if pages:
            selected.append({"id": question["id"], "question": question["question"], "gold_pages": pages})
    return selected


def main() -> None:
    """Encode those questions with the cached retrieval model and save the vectors to OUTPUT."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(REPO))
    from rag.retrieval import core

    core.force_hugging_face_offline()
    from sentence_transformers import SentenceTransformer
    model = core.load_cached_model(SentenceTransformer, core.EMBED_MODEL, trust_remote_code=True)
    questions = benchmark_questions()
    vectors = model.encode([q["question"] for q in questions], normalize_embeddings=True, show_progress_bar=False)
    OUTPUT.parent.mkdir(exist_ok=True)
    np.savez(OUTPUT, ids=np.array([q["id"] for q in questions]), questions=np.array([q["question"] for q in questions]),
             gold_pages=np.array([json.dumps(q["gold_pages"]) for q in questions]), vectors=vectors.astype(np.float32),
             model=np.array(core.EMBED_MODEL))
    print(f"{len(questions)} query vectors ({vectors.shape[1]}-d) -> {OUTPUT.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
