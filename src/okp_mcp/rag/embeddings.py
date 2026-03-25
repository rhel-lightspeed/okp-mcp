"""Embedding model integration for text-to-vector encoding."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from sentence_transformers import SentenceTransformer


class Embedder:
    """Text-to-vector encoder using the granite-embedding-30m-english model.

    Wraps SentenceTransformer with a ThreadPoolExecutor (max_workers=1) so the
    synchronous, non-thread-safe Rust tokenizer is serialized during async calls.
    Supports context-manager usage (``with Embedder(...) as emb:``) or manual
    ``close()`` to shut down the executor.

    Thread-safety: ``encode()`` is guarded by an internal lock so concurrent
    calls from multiple threads are serialized automatically.

    Args:
        model_name: HuggingFace model name or local path.
        cache_dir: Local cache directory (empty string = use HF default).
        device: Compute device for the model (default 'cpu').
    """

    def __init__(
        self,
        model_name: str = "ibm-granite/granite-embedding-30m-english",
        *,
        cache_dir: str = "",
        device: str = "cpu",
    ) -> None:
        """Load the embedding model and create the thread pool executor."""
        self._model = SentenceTransformer(
            model_name,
            device=device,
            cache_folder=cache_dir or None,
        )
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._encode_lock = threading.Lock()

    def encode(self, text: str) -> list[float]:
        """Encode text to a flat list of floats (synchronous, blocks the caller).

        Args:
            text: Input text to encode.

        Returns:
            Embedding vector as a list of floats.
        """
        with self._encode_lock:
            return self._model.encode(text, show_progress_bar=False).tolist()

    async def encode_async(self, text: str) -> list[float]:
        """Encode text asynchronously using the thread pool executor.

        Runs encode() in the executor so it doesn't block the event loop.

        Args:
            text: Input text to encode.

        Returns:
            Embedding vector as a list of floats.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.encode, text)

    def close(self) -> None:
        """Shut down the thread pool executor and release resources."""
        self._executor.shutdown(wait=True)

    def __enter__(self) -> Embedder:
        """Return self for context-manager usage."""
        return self

    def __exit__(self, *args: object) -> None:
        """Shut down the executor on context-manager exit."""
        self.close()
