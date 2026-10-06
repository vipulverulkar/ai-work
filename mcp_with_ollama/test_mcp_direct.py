"""Quick MCP smoke test (no Ollama needed). For full suite: pytest -v."""
import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])
            for name, kwargs in [
                ("list_files", {}),
                ("list_files", {"directory": "sample_files"}),
                ("list_files", {"directory": "sample_files", "pattern": "*.csv"}),
                ("search_files", {"query": "users"}),
            ]:
                r = await session.call_tool(name, kwargs)
                print(f"\n{name}({kwargs}) ->")
                for c in r.content:
                    print(getattr(c, "text", c)[:2000])


if __name__ == "__main__":
    asyncio.run(main())
