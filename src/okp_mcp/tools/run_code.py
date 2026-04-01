"""Placeholder code execution MCP tool."""

from fastmcp import Context

from ..config import logger
from ..server import mcp


# KLUDGE: Gemini 2.5 Flash has a built-in code execution capability that it
# attempts to invoke even when not explicitly configured as an available tool.
# When invoked via OpenAI-compatible endpoints through llama-stack, this causes
# "RuntimeError: OpenAI response failed: Unsupported tool call: run_code" and
# returns HTTP 500 to the client.
#
# This placeholder tool prevents the crash by registering run_code as a valid
# (but non-functional) tool. When Gemini attempts code execution, it receives
# feedback that code execution is unavailable rather than causing a server error.
#
# Related issue: https://discuss.ai.google.dev/t/gemini-live-api-unexpectedly-invokes-execute-code-and-other-built-in-tools-even-when-not-configured/87603
@mcp.tool
async def run_code(ctx: Context, language: str, code: str) -> str:
    """Execute code in the specified language.

    NOTE: This is a placeholder tool. Code execution is not available in this environment.
    """
    del ctx
    logger.warning("PLACEHOLDER run_code tool was invoked: language=%r code_length=%d", language, len(code))
    return (
        "Code execution is not available in this environment. "
        "Please provide the answer or code example directly in your response as text, "
        "rather than attempting to execute code."
    )
