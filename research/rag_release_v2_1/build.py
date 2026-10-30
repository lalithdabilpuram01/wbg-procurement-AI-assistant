"""Build the pilot_v2_1 processed corpus (extraction 2.1.0) and prove the default pipeline is unchanged.

    /opt/anaconda3/bin/python3 -B -m rag.release_v2_1.build          (from procurement-intelligence/)

1. Runs `pipeline_v2.run(profile="2.1.0")` -> rag/corpus_v3/processed/pilot_v2_1/ (new directory; pilot_v2 untouched).
2. Runs the DEFAULT profile into a temporary directory and compares the SHA-256 of every output file with the hashes
   recorded in pilot_v2/run_manifest.json (restricted files are compared by hash only, never read), then deletes the
   temporary directory.
Network sockets are blocked throughout; the chunking tokenizer must be the cached tiktoken cl100k_base.
Writes rag/release_v2_1/results/build.json.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from rag.embedding_cache.verify_compatibility import no_network
from rag.ingestion import config, pipeline_v2

RESULTS = Path(__file__).resolve().parent / "results"
EXPECTED_TOKENIZER = "tiktoken:cl100k_base"


def _checked(manifest: dict[str, Any]) -> dict[str, Any]:
    failed = {d: s for d, s in manifest["document_status"].items() if s != "processed"}
    if failed or manifest["tokenizer"] != EXPECTED_TOKENIZER:
        raise RuntimeError(f"build not clean: failed={failed}, tokenizer={manifest['tokenizer']}")
    return manifest


def build_v2_1() -> dict[str, Any]:
    return _checked(pipeline_v2.run(force=True, profile="2.1.0"))


def default_regression() -> dict[str, Any]:
    """Default profile re-run into a temporary directory: every output file must hash like pilot_v2's."""
    recorded = json.loads((config.OUTPUT_V2 / "run_manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="pilot_v2_default_check_") as tmp:
        rerun = _checked(pipeline_v2.run(force=True, out_dir=Path(tmp), profile="2.0.0"))
    differing = sorted(k for k in recorded["outputs"] if recorded["outputs"][k] != rerun["outputs"].get(k))
    return {"files_compared": len(recorded["outputs"]), "files_identical": len(recorded["outputs"]) - len(differing),
            "differing": differing, "extra_files": sorted(set(rerun["outputs"]) - set(recorded["outputs"])),
            "vocab_sha256_identical": rerun["vocab_sha256"] == recorded["vocab_sha256"],
            "code_hash": {"pilot_v2": recorded["code_hash"], "now": rerun["code_hash"]}}


def run() -> dict[str, Any]:
    with no_network() as attempts:
        manifest = build_v2_1()
        regression = default_regression()
    result = {"pilot_v2_1": {"directory": str(config.OUTPUT_V2_1.relative_to(config.REPO)), "extraction_version": manifest["extraction_version"],
                             "code_hash": manifest["code_hash"], "tokenizer": manifest["tokenizer"], "vocab_sha256": manifest["vocab_sha256"],
                             "document_status": manifest["document_status"], "outputs": manifest["outputs"]},
              "default_profile_regression": regression, "network_attempts_blocked": len(attempts)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "build.json").write_text(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    out = run()
    print(json.dumps({"default_profile_regression": out["default_profile_regression"], "network_attempts_blocked": out["network_attempts_blocked"],
                      "status": out["pilot_v2_1"]["document_status"]}, indent=1))
