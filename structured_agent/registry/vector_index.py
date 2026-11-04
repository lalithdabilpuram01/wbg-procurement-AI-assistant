"""Embedding model and vector index for registry descriptions.

The embedding model is the existing local gte-base-en-v1.5 (offline Hugging Face cache only; nothing is downloaded).
Vectors are stored in PostgreSQL as real[] because pgvector is not installed on this server, and ranked here with
NumPy cosine similarity. The VectorIndex interface lets a pgvector-backed index replace NumpyCosineIndex later
(see registry/sql/pgvector_migration.sql) without changing the search code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class InvalidVectorError(ValueError):
    pass


def validate_vector(vector: np.ndarray, dimensions: int) -> np.ndarray:
    """Reject vectors with the wrong dimension, non-finite values or zero norm; return the unit-normalised vector."""
    vector = np.asarray(vector, dtype=np.float32)
    if vector.ndim != 1 or vector.shape[0] != dimensions:
        raise InvalidVectorError(f"expected {dimensions} dimensions, got shape {vector.shape}")
    if not np.all(np.isfinite(vector)):
        raise InvalidVectorError("vector contains NaN or infinite values")
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise InvalidVectorError("zero-norm vector")
    return vector / norm


def force_hugging_face_offline() -> None:
    """Put huggingface_hub into offline mode for this process, whatever the import order.

    The HF_HUB_OFFLINE environment variable is read only when huggingface_hub is first imported, and
    local_files_only alone still lets transformers check processor config files online. Setting the library's
    offline flags at runtime closes both gaps (verified with HTTP and sockets blocked). Kept in step with
    rag/retrieval/core.py, which has the same function so RAG does not depend on this package.
    """
    import huggingface_hub.constants as hf_constants
    import transformers.utils.hub as transformers_hub

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    hf_constants.HF_HUB_OFFLINE = True
    # transformers copies the offline flag once at import (private `_is_offline_mode`); without this it still asks
    # huggingface_hub to look up processor config files online. Set only if this transformers version has it.
    if hasattr(transformers_hub, "_is_offline_mode"):
        transformers_hub._is_offline_mode = True


class EmbeddingModel:
    """Lazy wrapper around the local gte-base model (offline only)."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model = None

    def _load(self):
        """Load the model from the local cache on first use; raises RuntimeError when it is not cached."""
        if self._model is None:
            force_hugging_face_offline()
            from sentence_transformers import SentenceTransformer

            try:
                self._model = SentenceTransformer(self.model_id, trust_remote_code=True, device="cpu", local_files_only=True)
            except OSError as error:
                raise RuntimeError(f"Embedding model '{self.model_id}' is not in the local Hugging Face cache; "
                                   "downloading it requires approval") from error
        return self._model

    @property
    def dimensions(self) -> int:
        return self._load().get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed texts as unit-length float32 vectors, one row per text."""
        vectors = self._load().encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)


@dataclass(frozen=True)
class VectorHit:
    doc_id: str
    score: float


class VectorIndex(Protocol):
    """Any vector index the search can use: cosine search limited to allowed documents."""

    model_id: str
    dimensions: int

    def search(self, query_vector: np.ndarray, allowed_doc_ids: set[str], top_k: int) -> list[VectorHit]:
        """Cosine search restricted to allowed documents (authorization filtering happens before ranking)."""
        ...


class NumpyCosineIndex:
    """In-memory cosine index over vectors loaded from registry.description_embeddings."""

    def __init__(self, model_id: str, dimensions: int, doc_ids: list[str], vectors: np.ndarray):
        """Check the matrix shape and normalise every stored vector."""
        if vectors.shape != (len(doc_ids), dimensions):
            raise InvalidVectorError(f"matrix shape {vectors.shape} does not match {len(doc_ids)} x {dimensions}")
        self.model_id = model_id
        self.dimensions = dimensions
        self._doc_ids = doc_ids
        self._matrix = np.vstack([validate_vector(v, dimensions) for v in vectors]) if doc_ids else vectors

    def search(self, query_vector: np.ndarray, allowed_doc_ids: set[str], top_k: int) -> list[VectorHit]:
        """Top-k allowed documents by cosine similarity to the query vector."""
        query = validate_vector(query_vector, self.dimensions)
        allowed = [i for i, doc_id in enumerate(self._doc_ids) if doc_id in allowed_doc_ids]
        if not allowed:
            return []
        scores = self._matrix[allowed] @ query
        order = np.argsort(-scores)[:top_k]
        return [VectorHit(self._doc_ids[allowed[i]], float(scores[i])) for i in order]
