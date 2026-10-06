import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv(Path(__file__).with_name(".env"))

SERVER_PATH = Path(__file__).with_name("postgres_mcp_server.py")

async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            print([t.name for t in (await s.list_tools()).tools])

            print(await s.call_tool("list_tables", {"schema": "public"}))
            tables = await s.call_tool("list_tables", {"schema": "public"})
            # dvdrental has no 'users' table - use first real table (e.g. actor)
            demo_table = "actor"
            try:
                if tables.structured_content and "result" in tables.structured_content:
                    tbls = tables.structured_content["result"]
                    if tbls:
                        demo_table = tbls[0]
            except Exception:
                pass
            print(await s.call_tool("describe_table", {"table": demo_table}))
            r = await s.call_tool("query", {"sql": f"SELECT * FROM {demo_table}", "limit": 10})
            print(r.content[0].text)  # rows as text
            print(r.structured_content)  # rows as dicts

asyncio.run(main())