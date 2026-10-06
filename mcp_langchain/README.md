# Weather + Web Search MCP Server (LangChain 1.0)

MCP server that looks up **live weather for any city** and can **search the web**,
with core logic written as **LangChain 1.0 `@tool` tools**, exposed over MCP via FastMCP.
No API keys needed (Open-Meteo + DuckDuckGo).

## Tools

| MCP tool | Args | What it does |
|---|---|---|
| `get_weather` | `city: str` | Current temp, feels-like, humidity, wind + 3-day forecast (Open-Meteo) |
| `search_web` | `query: str, max_results: int=5` | Web search via DuckDuckGo (`ddgs`) |
| `get_weather_with_sources` | `city: str` | Weather + web results combined (best for display) |

## Setup

```bash
cd /path/to/mcp_langchain
python3 -m venv .venv
source .venv/bin/activate
pip install -e .        # or: pip install mcp langchain langchain-core langchain-community langchain-mcp-adapters langgraph httpx ddgs python-dotenv
```

## Run

```bash
# 1. Direct test (no MCP needed, just LangChain logic)
python -c "import asyncio, server; print(asyncio.run(server._fetch_weather('London')))"

# 2. MCP server over stdio (for Claude Desktop / Cursor / MCP Inspector)
python server.py

# 3. MCP server over HTTP
python server.py --http --port 8000
# -> http://127.0.0.1:8000/mcp

# 4. Client that displays weather (spawns server over stdio)
python client.py London
python client.py "Mumbai" --with-sources
python client.py Tokyo --tool search_web --query "Tokyo typhoon alert"
```

## Use with Claude Desktop / Cursor / MCP Inspector

`mcp.json` (example for Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "weather-search": {
      "command": "/path/to/mcp_langchain/.venv/bin/python",
      "args": ["/path/to/mcp_langchain/server.py"]
    }
  }
}
```

## Use with LangChain 1.0 agent

```bash
export OPENAI_API_KEY=sk-...
python agent_example.py "Weather in Paris? Should I carry an umbrella?"
```

`agent_example.py` loads the MCP tools with `MultiServerMCPClient.get_tools()`
and passes them to LangChain 1.0's `create_agent(model, tools)`.
