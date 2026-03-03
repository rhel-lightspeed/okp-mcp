"""Embedding model wrapper for IBM Granite."""

from typing import overload

import torch
from sentence_transformers import SentenceTransformer

# Model name from okp_query.py:14-15
EMBEDDING_MODEL = "ibm-granite/granite-embedding-30m-english"


class EmbeddingModel:
    """Wrapper for IBM Granite embedding model."""

    def __init__(self):
        """Initialize embedding model with auto device selection."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SentenceTransformer(EMBEDDING_MODEL, device=device)
        self.device = device

    @overload
    def encode(self, text: str) -> list[float]: ...

    @overload
    def encode(self, text: list[str]) -> list[list[float]]: ...

    def encode(self, text: str | list[str]) -> list[float] | list[list[float]]:
        """Encode text(s) to embedding vector(s).

        Args:
            text: Input text or list of texts to encode

        Returns:
            Single embedding vector as list[float] if input is str,
            or list of embedding vectors as list[list[float]] if input is list[str]
        """
        if isinstance(text, str):
            return self.model.encode([text])[0].tolist()
        else:
            embeddings = self.model.encode(text)
            return [emb.tolist() for emb in embeddings]
