"""Tests for okp_mcp.formatting module."""

import pytest

from okp_mcp.formatting import _format_result


@pytest.mark.parametrize(
    "main_content,max_content,expect_truncated",
    [
        ("x" * 10_000, 200, True),
        ("Brief content about kernels.", 5000, False),
    ],
    ids=["large-content-truncated", "short-content-preserved"],
)
async def test_format_result_content_cap(main_content: str, max_content: int, expect_truncated: bool):
    """_format_result caps content at max_content, appending [...] when truncated."""
    doc = {
        "id": "doc-1",
        "allTitle": "Test Doc",
        "documentKind": "solution",
        "view_uri": "/test-doc",
        "main_content": main_content,
    }
    data: dict = {"highlighting": {}}
    result, _ = await _format_result(doc, data, include_content=True, query="test", max_content=max_content)

    if expect_truncated:
        assert "[...]" in result
        content_start = result.index("Content: ") + len("Content: ")
        assert len(result[content_start:]) < max_content + 100
    else:
        assert "[...]" not in result
        assert main_content in result
