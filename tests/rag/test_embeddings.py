"""Tests for the Embedder class in rag/embeddings.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from okp_mcp.rag.embeddings import Embedder

MODULE = "okp_mcp.rag.embeddings.SentenceTransformer"


@pytest.fixture()
def mock_st():
    """Patch SentenceTransformer so no real model is loaded."""
    with patch(MODULE) as cls:
        cls.return_value = MagicMock()
        yield cls


@pytest.fixture()
def embedder(mock_st: MagicMock) -> Embedder:
    """Create an Embedder with a mocked SentenceTransformer."""
    emb = Embedder(model_name="test-model", cache_dir="", device="cpu")
    yield emb
    emb.close()


def test_encode_returns_list_of_float_length_384(embedder: Embedder):
    """Encode should return a list[float] of length 384 from numpy output."""
    embedder._model.encode.return_value = np.zeros(384)
    result = embedder.encode("test text")
    assert isinstance(result, list)
    assert len(result) == 384
    assert all(isinstance(v, float) for v in result)


def test_encode_passes_show_progress_bar_false(embedder: Embedder):
    """Encode must call SentenceTransformer.encode with show_progress_bar=False."""
    embedder._model.encode.return_value = np.zeros(384)
    embedder.encode("hello")
    embedder._model.encode.assert_called_once_with("hello", show_progress_bar=False)


async def test_encode_async_returns_list_of_float_length_384(embedder: Embedder):
    """Async encode should return the same shape as synchronous encode."""
    embedder._model.encode.return_value = np.zeros(384)
    result = await embedder.encode_async("async test")
    assert isinstance(result, list)
    assert len(result) == 384
    assert all(isinstance(v, float) for v in result)


async def test_encode_async_uses_run_in_executor(embedder: Embedder):
    """Async encode should delegate to run_in_executor with the embedder's executor."""
    embedder._model.encode.return_value = np.zeros(384)
    with patch("okp_mcp.rag.embeddings.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=np.zeros(384).tolist())

        await embedder.encode_async("test")
        mock_loop.return_value.run_in_executor.assert_called_once_with(embedder._executor, embedder.encode, "test")


def test_close_shuts_down_executor(mock_st: MagicMock):
    """Close should call executor.shutdown(wait=True)."""
    emb = Embedder(model_name="test-model", cache_dir="", device="cpu")
    with patch.object(emb._executor, "shutdown") as mock_shutdown:
        emb.close()
        mock_shutdown.assert_called_once_with(wait=True)


@pytest.mark.parametrize(
    ("cache_dir", "device", "expected_cache_folder"),
    [
        ("", "cuda", None),
        ("", "cpu", None),
        ("/tmp/cache", "cpu", "/tmp/cache"),  # noqa: S108 -- test path
    ],
    ids=["empty-cache-cuda", "empty-cache-cpu", "nonempty-cache"],
)
def test_constructor_forwards_args_to_sentence_transformer(
    mock_st: MagicMock, cache_dir: str, device: str, expected_cache_folder: str | None
):
    """Constructor should forward device and cache_dir to SentenceTransformer."""
    emb = Embedder(model_name="test-model", cache_dir=cache_dir, device=device)
    mock_st.assert_called_once_with("test-model", device=device, cache_folder=expected_cache_folder)
    emb.close()
