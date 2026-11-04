"""The local embedding model behind the Lakebase store: BAAI/bge-m3 (multilingual, 1024-d), loaded offline."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from rag.retrieval import core

MODEL_ID = "BAAI/bge-m3"
DIMENSION = 1024
MAX_SEQUENCE_TOKENS = 1024


@dataclass(frozen=True)
class LoadedModel:
    """The loaded model with its id and snapshot revision; encode_query returns one normalised float32 vector."""
    model: object            # sentence_transformers.SentenceTransformer
    model_id: str
    revision: str            # Hugging Face snapshot commit: identifies the exact weights

    def encode_query(self, text: str) -> np.ndarray:
        return self.model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0].astype(np.float32)


def snapshot_path(model_id: str = MODEL_ID) -> Path:
    """The local snapshot folder (no network); its name is the snapshot revision."""
    from huggingface_hub import snapshot_download
    core.force_hugging_face_offline()
    return Path(snapshot_download(model_id, local_files_only=True))


@lru_cache(maxsize=2)                    # one copy per process (retriever and TableLens share it)
def load_model(model_id: str = MODEL_ID, max_sequence_tokens: int = MAX_SEQUENCE_TOKENS) -> LoadedModel:
    """Load from the local snapshot path (loading by repo id fails offline); Apple GPU when available."""
    import torch
    from sentence_transformers import SentenceTransformer
    local = snapshot_path(model_id)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(str(local), local_files_only=True, device=device)
    model.max_seq_length = max_sequence_tokens
    return LoadedModel(model, model_id, local.name)
