"""Functional tests for the OKP MCP server using Pydantic AI and Vertex AI Gemini."""

import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values, load_dotenv
from functional_cases import FUNCTIONAL_TEST_CASES, FunctionalCase
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

_VERTEX_REGION = "us-central1"

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_DOTENV = dotenv_values(_ENV_PATH)


def _model_name() -> str:
    """Return the Gemini model name from .env, defaulting to gemini-2.5-flash."""
    return _DOTENV.get("OKP_FUNCTIONAL_MODEL") or "gemini-2.5-flash"


pytestmark = pytest.mark.functional

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYSTEM_PROMPT = (_FIXTURES_DIR / "functional_system_prompt.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _require_functional_stack() -> None:
    """Skip all tests in this module if .env credentials or Solr are unavailable."""
    if not _DOTENV:
        pytest.skip(".env file not found or empty — cp .env.example .env and fill in values")
    creds_path = _DOTENV.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        pytest.skip("GOOGLE_APPLICATION_CREDENTIALS not set in .env — see .env.example")
    creds = Path(creds_path).expanduser()
    if not creds.is_absolute():
        creds = (_ENV_PATH.parent / creds).resolve()
    if not creds.exists():
        pytest.skip(f"Credentials file not found: {creds} — check GOOGLE_APPLICATION_CREDENTIALS in .env")
    if not _DOTENV.get("GOOGLE_CLOUD_PROJECT"):
        pytest.skip("GOOGLE_CLOUD_PROJECT not set in .env — see .env.example")
    load_dotenv(_ENV_PATH, override=True)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds)
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get("http://localhost:8983/solr/portal/admin/ping")
            if resp.status_code != 200:
                pytest.skip("SOLR not responding at http://localhost:8983 - run: podman-compose up -d")
    except httpx.RequestError:
        pytest.skip("SOLR not reachable at http://localhost:8983 - run: podman-compose up -d")


def _extract_tool_calls(messages: list) -> list[tuple[str, object]]:
    """Return (tool_name, args) pairs for every MCP tool call found in captured messages.

    Tool calls appear as ToolCallPart objects inside ModelResponse.parts.
    """
    calls = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append((part.tool_name, part.args))
    return calls


def _extract_tool_returns(messages: list) -> list[str]:
    """Return text content for every MCP tool return found in captured messages.

    Tool returns appear as ToolReturnPart objects inside ModelRequest.parts.
    Uses model_response_str() to safely convert multimodal content to plain text.
    """
    returns = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    returns.append(part.model_response_str())
    return returns


def _assert_doc_refs(case: FunctionalCase, tool_calls: list, tool_returns: list[str], response: str) -> None:
    """Assert MCP tools were called and expected document references appear in results."""
    if not case.expected_doc_refs:
        return

    assert len(tool_calls) >= 1, (
        f"Expected at least one MCP tool call, got none.\nQuestion: {case.question}\nResponse: {response[:500]}"
    )

    all_content = " ".join(tool_returns) + " " + response
    found_refs = [ref for ref in case.expected_doc_refs if ref.lower() in all_content.lower()]
    assert len(found_refs) >= 1, (
        f"None of {case.expected_doc_refs!r} found in tool returns or response.\n"
        f"Question: {case.question}\nTool content (first 500): {all_content[:500]}"
    )


def _assert_required_facts(case: FunctionalCase, response: str) -> None:
    """Assert all required factual phrases appear in the response (case-insensitive)."""
    for fact in case.required_facts:
        if isinstance(fact, tuple):
            assert any(alt.lower() in response.lower() for alt in fact), (
                f"None of required alternatives {fact!r} found in response.\n"
                f"Question: {case.question}\nResponse: {response[:1000]}"
            )
        else:
            assert fact.lower() in response.lower(), (
                f"Required fact {fact!r} missing from response.\nQuestion: {case.question}\nResponse: {response[:1000]}"
            )


def _assert_no_forbidden_claims(case: FunctionalCase, response: str) -> None:
    """Assert no known-incorrect claims appear in the response (case-insensitive)."""
    for claim in case.forbidden_claims:
        assert claim.lower() not in response.lower(), (
            f"Forbidden claim {claim!r} found in response (known-incorrect answer).\n"
            f"Question: {case.question}\nResponse: {response[:1000]}"
        )


@pytest.mark.parametrize("case", FUNCTIONAL_TEST_CASES)
async def test_cla_scenario(case: FunctionalCase) -> None:
    """Verify Gemini correctly answers a known CLA incorrect-answer scenario.

    Starts a fresh MCP server subprocess per test to avoid anyio cancel-scope
    conflicts with pytest-asyncio fixture teardown. Each test:
    1. Spawns the OKP MCP server via MCPServerStdio.
    2. Sends the question to Gemini 2.5 Flash with temperature=0.
    3. Asserts at least one MCP tool was called.
    4. Asserts at least one expected document reference appears in tool results or response.
    5. Asserts all required factual phrases appear in the response (case-insensitive).
    6. Asserts no known-incorrect claims appear in the response (case-insensitive).
    """
    server = MCPServerStdio("uv", args=["run", "okp-mcp", "--transport", "stdio"])

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http_client:
        provider = GoogleProvider(location=_VERTEX_REGION, http_client=http_client)
        model = GoogleModel(_model_name(), provider=provider)
        agent = Agent(model, toolsets=[server], instructions=SYSTEM_PROMPT)

        async with server:
            with capture_run_messages() as messages:
                result = await agent.run(case.question, model_settings={"temperature": 0})
    response: str = result.output

    tool_calls = _extract_tool_calls(messages)
    tool_returns = _extract_tool_returns(messages)

    _assert_doc_refs(case, tool_calls, tool_returns, response)
    _assert_required_facts(case, response)
    _assert_no_forbidden_claims(case, response)
