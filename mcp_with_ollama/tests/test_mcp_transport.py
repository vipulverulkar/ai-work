"""MCP-transport integration test (spawns server.py over stdio)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_transport_list_files():
    async def _run():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"list_files", "search_files"}
                res = await session.call_tool("list_files", {})
                assert not getattr(res, "isError", False)
                assert res.content, "expected content blocks"
                res2 = await session.call_tool("list_files", {"directory": "sample_files"})
                assert not getattr(res2, "isError", False)
                res3 = await session.call_tool("search_files", {"query": "users"})
                assert not getattr(res3, "isError", False)
                assert res3.content, "expected content blocks"

    asyncio.run(_run())
