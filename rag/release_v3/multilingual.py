"""Multilingual embedding test on pilot_v3 (Milestone D): the current English model vs BAAI/bge-m3.

    /opt/anaconda3/bin/python3 -B -m rag.release_v3.multilingual download     (network: Hugging Face, once)
    /opt/anaconda3/bin/python3 -B -m rag.release_v3.multilingual encode <candidate>
    /opt/anaconda3/bin/python3 -B -m rag.release_v3.multilingual evaluate

Everything else stays the same: the corpus (pilot_v3, strategy C with tables), BM25, fusion, project-name scoping and
the relevance rule. Only the embedding model changes. The candidates' vectors are an experiment written to
results/experiments/ (never a registered release); the current model is served from the pilot_v3 release store.
Encoding uses the Apple GPU (MPS) when available. Encoding and evaluation run with network sockets blocked.

Question sets:
- v1:    31 hand-verified English questions (no regression allowed);
- v3:    generated English questions over the 35 projects;
- fr_v3: the same questions in French (French question -> mostly English documents);
- cross: hand-written questions about the six French Senegal documents (5 procurement plans, 1 ICR), in English
         (English question -> French document) and in French. Gold is the page of the plan row they paraphrase.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from rag.embedding_cache.adapter import indexed_text_fingerprint
from rag.embedding_cache.verify_compatibility import no_network
from rag.retrieval import core
from rag.retrieval.versions import build_retriever

from .evaluate import V1, build_benchmark_v3, run_benchmark, summarize

RESULTS = Path(__file__).resolve().parent / "results"
EXPERIMENTS = RESULTS / "experiments"
CANDIDATES = {   # name -> (Hugging Face model id, needs trust_remote_code); gte-multilingual-base was dropped by decision
    "bge-m3": ("BAAI/bge-m3", False),
}
MAX_SEQUENCE_TOKENS = 1024
BATCH_SIZE = 8                 # small batches: bge-m3 at 1024 tokens exhausted a 16 GB machine at 32
PART_SIZE = 1000               # units per saved part, so an interrupted encode resumes
FRENCH_TEMPLATES = {
    "objective": "Quel est l'objectif de développement du projet {title} ?",
    "risks": "Quels sont les principaux risques identifiés pour le projet {title} ({country}) ?",
    "lessons": "Quelles leçons ont été tirées du projet {title} ?",
    "procurement": "Comment la passation des marchés est-elle organisée pour le projet {title} ?",
}
# (id, language, question, document id, page): paraphrases of rows in the French Senegal procurement plans
CROSS = [
    ("en-cetud-works", "en", "What procurement method is planned for the works to extend the CETUD headquarters offices?", "40129158", 9),
    ("en-brt-guarding", "en", "Which procurement approach is used for security guarding of the BRT premises in Dakar?", "40129158", 13),
    ("en-prepaid-meters", "en", "What is the estimated cost of supplying 260,000 prepaid electricity meters in Senegal?", "33774135", 19),
    ("en-karmel-feeders", "en", "Which contract covers securing the power supply of Karmel and improving feeder service quality?", "33774135", 16),
    ("en-sigicmu-servers", "en", "Is a security and systems consultant being hired to install the SIGICMU servers in the data center?",
     "40078588", 11),
    ("en-child-health-eval", "en", "Which consultant assignment covers the external evaluation of the children's health care financing "
     "programme?", "40078588", 10),
    ("en-communication-expert", "en", "What is the estimated budget for recruiting a communication expert for the youth employability "
     "project in Senegal?", "34327467", 16),
    ("en-brt-evaluation-firm", "en", "How will the firm for the pre-evaluation and final evaluation of the Dakar BRT project be selected?",
     "40129158", 18),
    ("en-waste-equipment", "en", "What equipment is planned to help local authorities manage waste for residents along the BRT route?",
     "40129158", 12),
    ("en-grievance-supplies", "en", "How are office supplies for the PROGEP grievance management committees procured?", "31874637", 6),
    ("en-transport-master", "en", "Which assignment supports the inter-university master's programme in transport and digital mobility?",
     "40129158", 21),
    ("fr-brt-guarding", "fr", "Quelle est la méthode de passation pour le gardiennage des locaux du BRT ?", "40129158", 13),
    ("fr-prepaid-meters", "fr", "Quel est le coût estimé de la fourniture de 260 000 compteurs à paiement préalable ?", "33774135", 19),
    ("fr-communication-expert", "fr", "Quelle méthode de sélection pour le recrutement d'un expert en communication ?", "34327467", 16),
    ("fr-cetud-works", "fr", "Quels travaux d'extension du siège du CETUD sont prévus ?", "40129158", 9),
]


def corpus() -> core.Corpus:
    from rag.ingestion import config
    return core.Corpus("C", include_tables=True, processed_dir=config.OUTPUT_V3)


def load_model(name: str):
    from rag.vector_store import encoder
    return encoder.load_model(CANDIDATES[name][0], MAX_SEQUENCE_TOKENS).model


def download() -> dict[str, Any]:
    from huggingface_hub import snapshot_download
    return {name: snapshot_download(model_id) for name, (model_id, _) in CANDIDATES.items()}


def encode(name: str) -> dict[str, Any]:
    """Encode every pilot_v3 unit with a candidate model into results/experiments/ and return its metadata.
    Skips the work when vectors for the same indexed text exist; saved parts are reused after an interruption."""
    units = corpus().units
    fingerprint = indexed_text_fingerprint(units)
    target, meta = EXPERIMENTS / f"{name}.npy", EXPERIMENTS / f"{name}.json"
    if meta.is_file() and json.loads(meta.read_text())["indexed_text_sha256"] == fingerprint and target.is_file():
        return {"status": "already_encoded", "units": len(units)}
    parts = EXPERIMENTS / f"{name}.parts" / fingerprint[:16]
    parts.mkdir(parents=True, exist_ok=True)
    with no_network():
        model = load_model(name)
        for start in range(0, len(units), PART_SIZE):
            _encode_part(model, units[start:start + PART_SIZE], parts / f"{start:06d}.npy")
            print(f"encoded {min(start + PART_SIZE, len(units))}/{len(units)}", flush=True)
    vectors = np.concatenate([np.load(parts / f"{start:06d}.npy") for start in range(0, len(units), PART_SIZE)])
    np.save(target, vectors)
    record = {"model": CANDIDATES[name][0], "units": len(units), "dimension": int(vectors.shape[1]),
              "indexed_text_sha256": fingerprint, "max_sequence_tokens": MAX_SEQUENCE_TOKENS}
    meta.write_text(json.dumps(record, indent=1))
    return record


def _encode_part(model, units, path: Path) -> None:
    """Encode one part unless it is already saved; free GPU memory afterwards."""
    if path.is_file():
        return
    vectors = model.encode([u.text for u in units], batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False)
    np.save(path, vectors.astype(np.float32))
    torch = __import__("torch")
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def question_sets() -> dict[str, list[dict[str, Any]]]:
    """Return the question sets by name: v1, v3, its French version fr_v3, and the cross-language questions."""
    v3 = build_benchmark_v3()["questions"]
    from rag.ingestion import config
    projects = {json.loads(line)["project_id"]: json.loads(line)["api"]
                for line in (config.OUTPUT_V3 / "shared" / "projects.jsonl").read_text().splitlines()}
    french = [{**q, "id": q["id"].replace("V3-", "FR-"), "question": FRENCH_TEMPLATES[q["type"]].format(
        title=projects[q["project_id"]]["project_title"], country=projects[q["project_id"]].get("country_name", ""))}
        for q in v3 if q["type"] in FRENCH_TEMPLATES]
    cross = [{"id": f"X-{qid}", "type": f"cross_{lang}", "project_id": None, "question": text,
              "gold": [{"document_id": doc, "pages": [page]}], "require_all": False} for qid, lang, text, doc, page in CROSS]
    return {"v1": json.loads(V1.read_text())["questions"], "v3": v3, "fr_v3": french, "cross": cross}


def retriever_for(name: str):
    """Return a pilot_v3 retriever whose dense side uses the named model (current model, or a candidate's vectors)."""
    retriever = build_retriever("pilot_v3_gte_local")                                  # current model: verified release store
    if name == "gte-base-en (current)":
        return retriever
    meta = json.loads((EXPERIMENTS / f"{name}.json").read_text())
    if meta["indexed_text_sha256"] != indexed_text_fingerprint(retriever.c.units):
        raise RuntimeError(f"{name} vectors were encoded for different units")
    retriever._emb = np.load(EXPERIMENTS / f"{name}.npy")                     # experiment: index and query use the candidate
    retriever._model = load_model(name)
    return retriever


def evaluate() -> dict[str, Any]:
    """Run every question set for the current model and each encoded candidate, and write the comparison files."""
    sets = question_sets()
    report: dict[str, Any] = {"question_sets": {k: len(v) for k, v in sets.items()}, "models": {}}
    rows = []
    with no_network():
        for name in ("gte-base-en (current)", *CANDIDATES):
            if name in CANDIDATES and not (EXPERIMENTS / f"{name}.npy").is_file():
                report["models"][name] = {"skipped": "not encoded"}
                continue
            retriever = retriever_for(name)
            report["models"][name] = {}
            for set_name, questions in sets.items():
                result = run_benchmark(retriever, questions, rerank=False)
                report["models"][name][set_name] = summarize(result)
                rows += [{"model": name, "set": set_name, **r} for r in result]
            print(name, {s: {k: v for k, v in m.items() if k in ("recall@5", "MRR")} for s, m in report["models"][name].items()},
                  flush=True)
    pd.DataFrame(rows).to_csv(RESULTS / "per_question_multilingual.csv", index=False)
    (RESULTS / "multilingual.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    output = {"download": download, "evaluate": evaluate}.get(step) or (lambda: encode(sys.argv[2]))
    print(json.dumps(output(), indent=1, default=str)[:3000])
