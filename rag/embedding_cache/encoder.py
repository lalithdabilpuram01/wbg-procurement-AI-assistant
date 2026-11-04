"""Local-model encoder for the incremental writer (Step 6D). Loads only from the local Hugging Face cache.

`LocalModelEncoder(config)` refuses to exist unless the loaded model matches the configuration the store is bound to
(model files fingerprint, max_seq_length, dimension, CLS pooling) and that configuration has a passing compatibility
attestation. `encode` reproduces the retrieval index settings: batch_size and normalisation from the config, no prompt.
Callers should block network access around construction and use (see verify_compatibility.no_network).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .adapter import DEFAULT_ATTESTATION
from .identity import EmbeddingConfig, local_model_config


class EncoderMismatchError(RuntimeError):
    """The loaded model or its settings do not match the store's embedding configuration."""


class LocalModelEncoder:
    """Sentence-transformers encoder that only exists when the local model matches an attested store configuration."""
    def __init__(self, config: EmbeddingConfig, attestation: Path = DEFAULT_ATTESTATION):
        """Load the cached model; EncoderMismatchError unless its files, attestation and settings match `config`."""
        # Refuse to load when the cached model files, the attestation or the loaded settings differ from the config.
        if config.fingerprint() != local_model_config(config.batch_size, config.normalize, config.prompt).fingerprint():
            raise EncoderMismatchError("the local model files do not match the requested configuration")
        record = json.loads(attestation.read_text()) if attestation.is_file() else {}
        if record.get("verdict") != "compatible" or record.get("config_fingerprint") != config.fingerprint():
            raise EncoderMismatchError("no passing compatibility attestation for this configuration")
        from sentence_transformers import SentenceTransformer

        from rag.retrieval import core
        self.model = core.load_cached_model(SentenceTransformer, core.EMBED_MODEL, trust_remote_code=True)
        pooling = getattr(self.model[1], "pooling_mode", None) or self.model[1].get_pooling_mode_str()
        dimension = (getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension)()
        if (self.model.max_seq_length, dimension, str(pooling)) != (config.max_seq_length, config.dimension, "cls"):
            raise EncoderMismatchError(f"loaded model settings {(self.model.max_seq_length, dimension, pooling)} differ from the config")
        self.config = config

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts with the configuration's batch size, normalisation and prompt; returns one row per text."""
        vectors = self.model.encode(texts, batch_size=self.config.batch_size, normalize_embeddings=self.config.normalize,
                                    prompt=self.config.prompt or None, show_progress_bar=False, convert_to_numpy=True)
        return np.asarray(vectors, dtype=self.config.dtype)
