"""
Simple MCP client: calls the weather-search server over stdio and displays results.

Usage:
  python client.py London
  python client.py "New York" --with-sources
  python client.py Mumbai --tool search_web --query "Mumbai rain alert"
"""

import argparse
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = os.path.join(os.path.dirname(__file__), "server.py")


async def call_tool(tool: str, arguments: dict):
    params = StdioServerParameters(command=sys.executable, args=[SERVER_PATH])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            # result.content is a list of TextContent blocks
            return "\n".join(
                block.text for block in result.content if hasattr(block, "text")
            )


async def amain() -> None:
    p = argparse.ArgumentParser(description="Weather MCP client (display results)")
    p.add_argument("city", nargs="?", default="London", help="City name")
    p.add_argument("--tool", default="get_weather",
                   choices=["get_weather", "search_web", "get_weather_with_sources"],
                   help="Which MCP tool to call")
    p.add_argument("--query", default=None, help="Custom web query (for search_web)")
    p.add_argument("--with-sources", action="store_true", help="Use get_weather_with_sources")
    args = p.parse_args()

    tool = "get_weather_with_sources" if args.with_sources else args.tool
    if tool == "search_web":
        arguments = {"query": args.query or f"{args.city} weather today", "max_results": 5}
    else:
        arguments = {"city": args.city}

    print(f"Calling '{tool}' with {arguments}...\n" + "-" * 60)
    text = await call_tool(tool, arguments)
    print(text)


if __name__ == "__main__":
    asyncio.run(amain())
