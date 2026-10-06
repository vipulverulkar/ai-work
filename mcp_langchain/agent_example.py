"""
LangChain 1.0 agent example: uses the MCP weather-search server via
langchain-mcp-adapters, then displays the answer.

Requires an LLM API key, e.g.:
  export ANTHROPIC_API_KEY=...
  # or OPENAI_API_KEY=...

Usage:
  python agent_example.py "What is the weather in Paris? Should I carry an umbrella?"
"""

import asyncio
import os
import sys

SERVER_PATH = os.path.join(os.path.dirname(__file__), "server.py")


async def amain() -> None:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    try:
        from langchain.agents import create_agent  # LangChain 1.0
    except ImportError:
        from langchain.agents import create_agent  # fallback

    question = sys.argv[1] if len(sys.argv) > 1 else "What is the weather in London right now?"

    client = MultiServerMCPClient(
        {
            "weather-search": {
                "command": sys.executable,
                "args": [SERVER_PATH],
                "transport": "stdio",
            }
        }
    )
    tools = await client.get_tools()
    print(f"Loaded MCP tools: {[t.name for t in tools]}")

    # Pick any chat model you have a key for. Change as needed:
    # "openai:gpt-4o-mini", "anthropic:claude-haiku-4-5", "google_genai:gemini-2.0-flash"
    model = os.environ.get("MODEL", "openai:gpt-4o-mini")
    agent = create_agent(model, tools)

    result = await agent.ainvoke({"messages": question})
    # Display final message
    messages = result.get("messages", [])
    print("\n" + "=" * 60)
    print(messages[-1].content if messages else result)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(amain())
