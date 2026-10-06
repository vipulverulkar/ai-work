"""Ollama + MCP client: lets a local Ollama model use the file-lister tools.

Features:
  - Persistent MCP connection across turns (no respawn per prompt).
  - Multi-turn history with /clear, /tools, /history, /model commands.
  - Robust tool-arg parsing, MCP error surfacing, output truncation.
  - Configurable model, host, temperature, max tool iterations, verbosity.
  - Single-prompt (scripting) and interactive (REPL) modes.

Usage:
    python client_ollama.py --prompt "search for users"          # model etc. from config.json
    python client_ollama.py -d sample_files                      # browse another folder
    python client_ollama.py --model qwen2.5:7b                   # override config for one run

Settings come from config.json — the model is never asked interactively.
Precedence: CLI flag > environment variable > config.json > built-in default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ollama import Client as OllamaClient

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING"),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
log = logging.getLogger("ollama-mcp")

SERVER_SCRIPT = Path(__file__).resolve().parent / "server.py"
PYTHON = sys.executable

def build_system_prompt(directory: str) -> str:
    return (
        "You are a file-system assistant with MCP tools: "
        "list_files(directory, pattern) and search_files(query, directory, recursive, limit).\n"
        f"The user's selected directory is '{directory}'. "
        "Default the `directory` argument to it unless the user names another folder.\n"
        "Rules:\n"
        "1. When the user asks about files/folders, ALWAYS call the right tool first — never guess. "
        "Use list_files to browse (pattern like \"*.csv\" filters; sort_by modified/size/name "
        "with order desc/asc reorders — default is newest first), search_files to find by name.\n"
        "2. After a tool result, give a concise human summary (names, sizes, counts). "
        "Truncate long listings to the most relevant ~20 items and say how many were omitted.\n"
        "3. If a tool errors (e.g. access denied), explain it plainly and suggest an alternative."
    )


DEFAULT_SYSTEM_PROMPT = build_system_prompt(".")

TOOL_RESULT_PREVIEW = 6000

CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "config.json"

# Final fallbacks. Precedence everywhere else is:
# CLI flag > environment variable > config.json > these defaults.
# The model is NEVER asked interactively — it always comes from this chain.
HARD_DEFAULTS: dict[str, object] = {
    "model": "llama3.1:8b",
    "host": "http://localhost:11434",
    "temperature": 0.1,
    "max_iters": 6,
    "verbose": False,
    "directory": ".",
    "allowed_root": None,
}

ENV_KEYS = {
    "model": "OLLAMA_MODEL",
    "host": "OLLAMA_HOST",
    "temperature": "OLLAMA_TEMPERATURE",
    "max_iters": "MCP_MAX_ITERS",
    "directory": None,
    "allowed_root": "ALLOWED_ROOT",
}


def load_config_file(path: str | Path) -> dict:
    """Load config.json. Returns {} if missing/unreadable (never crashes)."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"[warning] ignoring invalid config file {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _convert(name: str, raw: object) -> object:
    if name == "temperature":
        return float(raw)  # type: ignore[arg-type]
    if name == "max_iters":
        return int(raw)  # type: ignore[arg-type]
    if name == "verbose":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "t", "on")
    return raw


def resolve_settings(args: argparse.Namespace) -> dict:
    """Merge CLI flags > env vars > config.json > hard defaults."""
    file_cfg = load_config_file(args.config or CONFIG_PATH_DEFAULT)
    resolved: dict[str, object] = {}
    for name, fallback in HARD_DEFAULTS.items():
        cli_val = getattr(args, name, None)
        if cli_val is not None:
            resolved[name] = cli_val
            continue
        env_key = ENV_KEYS.get(name)
        if env_key and os.environ.get(env_key):
            try:
                resolved[name] = _convert(name, os.environ[env_key])
            except ValueError:
                print(f"[warning] ignoring invalid {env_key}={os.environ[env_key]!r}")
                resolved[name] = file_cfg.get(name, fallback)
            continue
        resolved[name] = file_cfg.get(name, fallback)
    # Normalise numeric types coming from the JSON file as strings
    for name in ("temperature", "max_iters"):
        try:
            resolved[name] = _convert(name, resolved[name])
        except (ValueError, TypeError):
            print(f"[warning] ignoring invalid config value {name}={resolved[name]!r}")
            resolved[name] = HARD_DEFAULTS[name]
    return resolved


@dataclass
class AgentConfig:
    model: str = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    temperature: float = float(os.environ.get("OLLAMA_TEMPERATURE", "0.1"))
    max_iters: int = int(os.environ.get("MCP_MAX_ITERS", "6"))
    verbose: bool = False
    directory: str = "."
    system_prompt: str = ""
    server_env: dict[str, str] | None = None


class MCPConnection:
    """Long-lived stdio MCP session to server.py."""

    def __init__(self, server_script: Path = SERVER_SCRIPT, env: dict[str, str] | None = None):
        self.server_script = server_script
        self.env = env
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: list[dict] = []

    async def connect(self) -> list[dict]:
        params = StdioServerParameters(command=PYTHON, args=[str(self.server_script)], env=self.env)
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        result = await self.session.list_tools()
        self.tools = to_ollama_tools(result.tools)
        log.info("MCP connected: %s", [t["function"]["name"] for t in self.tools])
        return self.tools

    async def call(self, name: str, args: dict) -> str:
        assert self.session is not None, "MCP not connected"
        try:
            result = await self.session.call_tool(name, args)
        except Exception as exc:
            return f"MCP tool error [{name}]: {exc}"
        parts = [getattr(b, "text", str(b)) for b in result.content]
        prefix = "MCP tool error: " if getattr(result, "isError", False) else ""
        return prefix + "\n".join(parts)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self.session = None


def to_ollama_tools(mcp_tools) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        },
    } for t in mcp_tools]


def _parse_tool_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            log.warning("Could not parse tool args JSON: %r", raw[:200])
            return {}
    return {}


def _normalise_message(raw_msg: Any) -> dict:
    """Normalise ollama message (dict or Message object) to a plain dict."""
    if isinstance(raw_msg, dict):
        return raw_msg
    tool_calls = getattr(raw_msg, "tool_calls", None)
    norm_calls = None
    if tool_calls:
        norm_calls = []
        for c in tool_calls:
            fn = getattr(getattr(c, "function", c), "name", "?")
            args = getattr(getattr(c, "function", c), "arguments", {})
            norm_calls.append({"function": {"name": fn, "arguments": args}})
    return {
        "role": getattr(raw_msg, "role", "assistant"),
        "content": getattr(raw_msg, "content", "") or "",
        "tool_calls": norm_calls,
    }


@dataclass
class OllamaMCPAgent:
    config: AgentConfig
    messages: list[dict] = field(default_factory=list)
    mcp: MCPConnection = field(default=None)  # type: ignore[assignment]
    client: OllamaClient = field(default=None)  # type: ignore[assignment]

    def __post_init__(self):
        self.client = OllamaClient(host=self.config.host)
        if not self.config.system_prompt:
            self.config.system_prompt = build_system_prompt(self.config.directory)
        self.messages = [{"role": "system", "content": self.config.system_prompt}]
        self.mcp = MCPConnection(env=self.config.server_env)

    async def __aenter__(self):
        await self.mcp.connect()
        print(f"[mcp] tools: {[t['function']['name'] for t in self.mcp.tools]}")
        return self

    async def __aexit__(self, *exc):
        await self.mcp.close()

    def _chat(self) -> Any:
        return self.client.chat(
            model=self.config.model,
            messages=self.messages,
            tools=self.mcp.tools or None,
            options={"temperature": self.config.temperature},
        )

    async def ask(self, user_text: str) -> str:
        """Full agentic turn: chat -> tool calls -> chat ... -> final answer."""
        self.messages.append({"role": "user", "content": user_text})
        for i in range(self.config.max_iters):
            try:
                resp = await asyncio.to_thread(self._chat)
            except ollama.ResponseError as exc:
                err = f"[ollama error] {exc}"
                print(err)
                return err
            except Exception as exc:
                err = f"[ollama connection error] {exc} (host={self.config.host})"
                print(err)
                return err

            raw = resp["message"] if isinstance(resp, dict) else resp.message
            msg = _normalise_message(raw)
            self.messages.append(msg)

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final = msg.get("content", "") or ""
                print(f"\n[{self.config.model}] {final}")
                return final

            for tc in tool_calls:
                fn = tc["function"]["name"] if isinstance(tc, dict) else tc.function.name
                raw_args = tc["function"].get("arguments", {}) if isinstance(tc, dict) else tc.function.arguments
                args = _parse_tool_args(raw_args)
                print(f"[tool {i + 1}] {fn}({json.dumps(args)[:400]})")
                text = await self.mcp.call(fn, args)
                if self.config.verbose:
                    print(f"[tool_result {fn}] {text[:TOOL_RESULT_PREVIEW]}")
                else:
                    print(f"[tool_result] {len(text)} chars")
                self.messages.append({"role": "tool", "content": text})

        notice = "Max tool iterations reached without a final answer."
        print(notice)
        return notice

    # -- REPL helpers -----------------------------------------------------
    def show_tools(self):
        for t in self.mcp.tools:
            fn = t["function"]
            print(f"  - {fn['name']}: {(fn.get('description') or '')[:120]}")

    def show_history(self, n: int = 6):
        for m in self.messages[-n:]:
            print(f"[{m.get('role', '?')}] {(m.get('content', '') or '')[:300]}")

    def clear(self):
        self.messages = [{"role": "system", "content": self.config.system_prompt}]
        print("[history cleared]")

    def change_directory(self, directory: str):
        """Switch the selected directory and tell the model about it."""
        directory = (directory or "").strip() or "."
        self.config.directory = directory
        self.config.system_prompt = build_system_prompt(directory)
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.config.system_prompt
        else:
            self.messages.insert(0, {"role": "system", "content": self.config.system_prompt})
        print(f"[directory -> {directory}]")


async def run_single_prompt(cfg: AgentConfig, prompt: str) -> str:
    async with OllamaMCPAgent(config=cfg) as agent:
        return await agent.ask(prompt)


async def run_interactive(cfg: AgentConfig) -> None:
    try:
        chosen = input(f"Enter directory to browse [{cfg.directory}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nbye.")
        return
    if chosen:
        if not Path(chosen).expanduser().exists():
            print(f"[warning] '{chosen}' does not exist — using '{cfg.directory}' instead.")
        else:
            cfg.directory = chosen
    async with OllamaMCPAgent(config=cfg) as agent:
        print(f"Ollama+MCP file explorer | model={cfg.model} | host={cfg.host} | directory={cfg.directory}")
        print("Commands: /cd <dir> /tools /history /clear /model <name> /quit")
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye.")
                break
            if not line:
                continue
            if line.startswith("/"):
                cmd, _, rest = line[1:].partition(" ")
                if cmd in ("quit", "exit", "q"):
                    break
                elif cmd == "tools":
                    agent.show_tools()
                elif cmd == "history":
                    agent.show_history()
                elif cmd == "clear":
                    agent.clear()
                elif cmd == "cd":
                    if rest.strip():
                        agent.change_directory(rest.strip())
                    else:
                        print(f"[current directory: {agent.config.directory}]")
                elif cmd == "model" and rest.strip():
                    agent.config.model = rest.strip()
                    print(f"[model -> {agent.config.model}]")
                else:
                    print("Unknown command. Try /cd <dir> /tools /history /clear /model <name> /quit")
                continue
            await agent.ask(line)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ollama client for MCP file-lister server")
    p.add_argument("--prompt", type=str, default=None)
    p.add_argument("--config", type=str, default=None,
                   help="Path to config.json (default: config.json next to this script)")
    p.add_argument("--model", type=str, default=None, help="Ollama model (overrides config)")
    p.add_argument("--host", type=str, default=None, help="Ollama host (overrides config)")
    p.add_argument("--temperature", type=float, default=None, help="Sampling temperature (overrides config)")
    p.add_argument("--max-iters", dest="max_iters", type=int, default=None,
                   help="Max tool-call rounds (overrides config)")
    p.add_argument("--verbose", action="store_true", default=None, help="Print full tool results")
    p.add_argument("--allowed-root", type=str, default=None, help="ALLOWED_ROOT forwarded to MCP server")
    p.add_argument("--directory", "-d", type=str, default=None,
                   help="Directory to browse/search (overrides config)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    settings = resolve_settings(args)
    if settings["verbose"]:
        logging.getLogger().setLevel(logging.INFO)
    server_env = dict(os.environ)
    if settings["allowed_root"]:
        server_env["ALLOWED_ROOT"] = str(settings["allowed_root"])
    cfg = AgentConfig(
        model=str(settings["model"]), host=str(settings["host"]),
        temperature=float(settings["temperature"]),  # type: ignore[arg-type]
        max_iters=int(settings["max_iters"]),  # type: ignore[arg-type]
        verbose=bool(settings["verbose"]), server_env=server_env,
        directory=str(settings["directory"]),
    )
    print(f"[config] model={cfg.model} host={cfg.host} directory={cfg.directory}")
    if args.prompt:
        asyncio.run(run_single_prompt(cfg, args.prompt))
    else:
        asyncio.run(run_interactive(cfg))


if __name__ == "__main__":
    main()
