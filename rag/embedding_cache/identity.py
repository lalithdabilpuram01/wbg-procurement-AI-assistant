"""Embedding configuration identity: everything that can change a unit's vector, fingerprinted.

The model identity is read from the local Hugging Face cache metadata only (refs, snapshot files, LFS blob names):
no model is imported, loaded or downloaded. The preprocessing values mirror `rag/retrieval/core.py`
(`Retriever.embeddings`: `encode(texts, batch_size=16, normalize_embeddings=True)`, no prompt or prefix, the unit's
indexed text as is, sentence-transformers' configured max_seq_length).
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HF_HUB = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub")
MODEL_ID = "Alibaba-NLP/gte-base-en-v1.5"
REMOTE_CODE_ID = "Alibaba-NLP/new-impl"          # trust_remote_code implementation named in the model's config auto_map


class ModelIdentityError(RuntimeError):
    """The local model cache does not identify one unambiguous model revision."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """Identity of an embedding space. Two vectors are comparable only when their configs fingerprint the same."""

    model_id: str
    model_revision: str              # commit of the snapshot (refs/main)
    weights_sha256: str              # LFS blob name of model.safetensors = its SHA-256
    remote_code_revision: str        # commit of the trust_remote_code implementation
    model_config_sha256: str         # config.json + modules.json + sentence_bert_config.json + pooling config
    pooling: str
    max_seq_length: int
    dimension: int
    dtype: str
    normalize: bool
    prompt: str                      # prefix added before encoding ('' = none)
    batch_size: int
    library_versions: tuple[tuple[str, str], ...]

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _repo_dir(repo_id: str) -> Path:
    return HF_HUB / f"models--{repo_id.replace('/', '--')}"


def _single_snapshot(repo_id: str) -> tuple[str, Path]:
    """Return (revision, snapshot folder) of a cached model; raises unless refs/main names the only snapshot."""
    repo = _repo_dir(repo_id)
    ref = repo / "refs" / "main"
    snapshots = sorted(p for p in (repo / "snapshots").glob("*") if p.is_dir()) if repo.is_dir() else []
    if not ref.is_file() or len(snapshots) != 1:
        raise ModelIdentityError(f"{repo_id}: expected refs/main and exactly one local snapshot, found {len(snapshots)}")
    revision = ref.read_text().strip()
    if snapshots[0].name != revision:
        raise ModelIdentityError(f"{repo_id}: refs/main {revision} does not match snapshot {snapshots[0].name}")
    return revision, snapshots[0]


def _sha256_files(paths: list[Path]) -> str:
    """SHA-256 over the names and bytes of the given files, in the order given."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def snapshot_mtime(repo_id: str = MODEL_ID) -> float:
    """Latest modification time of the snapshot entries (used as evidence for legacy vectors)."""
    _, snapshot = _single_snapshot(repo_id)
    return max(p.lstat().st_mtime for p in [snapshot, *snapshot.rglob("*")])


def library_versions() -> tuple[tuple[str, str], ...]:
    return tuple((name, importlib.metadata.version(name)) for name in ("sentence-transformers", "transformers", "torch"))


def local_model_config(batch_size: int = 16, normalize: bool = True, prompt: str = "") -> EmbeddingConfig:
    """The active retrieval embedding configuration, identified from local files only."""
    revision, snapshot = _single_snapshot(MODEL_ID)
    remote_revision, _ = _single_snapshot(REMOTE_CODE_ID)
    weights = (snapshot / "model.safetensors").resolve().name
    if len(weights) != 64:
        raise ModelIdentityError("model.safetensors is not an LFS blob named by its SHA-256")
    st_config = json.loads((snapshot / "sentence_bert_config.json").read_text())
    pooling_config = json.loads((snapshot / "1_Pooling" / "config.json").read_text())
    pooling = next((k.removeprefix("pooling_mode_") for k, v in pooling_config.items() if k.startswith("pooling_mode_") and v), "")
    config_files = [snapshot / "config.json", snapshot / "modules.json", snapshot / "sentence_bert_config.json",
                    snapshot / "1_Pooling" / "config.json"]
    return EmbeddingConfig(
        model_id=MODEL_ID, model_revision=revision, weights_sha256=weights, remote_code_revision=remote_revision,
        model_config_sha256=_sha256_files(config_files), pooling=pooling, max_seq_length=int(st_config["max_seq_length"]),
        dimension=int(pooling_config["word_embedding_dimension"]), dtype="float32", normalize=normalize, prompt=prompt,
        batch_size=batch_size, library_versions=library_versions())
