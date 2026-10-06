"""Flask UI for the MCP file explorer.

Pages / endpoints:
  GET  /              browse + search + ask UI (model/directory from config.json)
  GET  /api/files?directory=.&pattern=*&sort_by=modified&order=desc
  GET  /api/search?query=...&directory=.&recursive=true&limit=50
  POST /api/ask       {"prompt": "...", "directory": "..."} -> Ollama answer via tools
  GET  /api/config    current model + default directory (for the UI header)

The UI calls server.list_files / server.search_files directly (same code as
the MCP tools) and uses Ollama tool-calling for the natural-language box.

Run (from the project root or the frontend folder):
    python frontend/app.py [--host 127.0.0.1] [--port 5000]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Make the project root importable (server.py, client_ollama.py live there).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, render_template, request

import server
from client_ollama import CONFIG_PATH_DEFAULT, load_config_file

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
log = logging.getLogger("flask-ui")

# templates/ lives next to this file inside frontend/.
app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / "templates"))

# Model/host come from config.json (same precedence base as the CLI client).
_FILE_CFG = load_config_file(CONFIG_PATH_DEFAULT)
MODEL = os.environ.get("OLLAMA_MODEL", str(_FILE_CFG.get("model", "llama3.1:8b")))
HOST = os.environ.get("OLLAMA_HOST", str(_FILE_CFG.get("host", "http://localhost:11434")))
TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", _FILE_CFG.get("temperature", 0.1)))
MAX_ITERS = int(os.environ.get("MCP_MAX_ITERS", _FILE_CFG.get("max_iters", 6)))
DEFAULT_DIR = str(_FILE_CFG.get("directory", "."))

OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a folder, newest first. Defaults to the selected directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "pattern": {"type": "string"},
                    "sort_by": {"type": "string"},
                    "order": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file names for a substring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "directory": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a file-system assistant with tools list_files and search_files. "
    "Always call a tool before answering file questions, then summarise briefly "
    "(names, sizes, dates; at most ~20 items, say how many were omitted)."
)


def _tobool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("1", "true", "yes", "y", "t", "on")


@app.get("/")
def index():
    return render_template("index.html", model=MODEL, default_dir=DEFAULT_DIR)


@app.get("/api/config")
def api_config():
    return jsonify({"model": MODEL, "default_directory": DEFAULT_DIR})


@app.get("/api/files")
def api_files():
    try:
        entries = server.list_files(
            directory=request.args.get("directory", "."),
            pattern=request.args.get("pattern", "*"),
            sort_by=request.args.get("sort_by", "modified"),
            order=request.args.get("order", "desc"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"directory": request.args.get("directory", "."),
                    "total": len(entries), "entries": entries})


@app.get("/api/search")
def api_search():
    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        hits = server.search_files(
            query=query,
            directory=request.args.get("directory", "."),
            recursive=_tobool(request.args.get("recursive", "true")),
            limit=int(request.args.get("limit", "50")),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"query": query, "total": len(hits), "hits": hits})


def _parse_args(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


@app.post("/api/ask")
def api_ask():
    data = request.get_json(force=True, silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    directory = (data.get("directory") or ".").strip() or "."
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    try:
        from ollama import Client
        client = Client(host=HOST)
    except Exception as exc:
        return jsonify({"error": f"cannot create ollama client: {exc}"}), 502

    messages: list[dict] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT} The user's selected directory is '{directory}'."},
        {"role": "user", "content": prompt},
    ]
    trace: list[dict] = []
    try:
        for _ in range(MAX_ITERS):
            resp = client.chat(model=MODEL, messages=messages, tools=OLLAMA_TOOLS,
                               options={"temperature": TEMPERATURE})
            raw = resp["message"] if isinstance(resp, dict) else resp.message
            msg = raw if isinstance(raw, dict) else {
                "role": getattr(raw, "role", "assistant"),
                "content": getattr(raw, "content", "") or "",
                "tool_calls": [
                    {"function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in (getattr(raw, "tool_calls", None) or [])
                ] or None,
            }
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return jsonify({"answer": msg.get("content", ""), "tool_calls": trace})
            for tc in calls:
                fn = tc["function"]["name"]
                fargs = _parse_args(tc["function"].get("arguments", {}))
                fargs.setdefault("directory", directory)
                try:
                    result = getattr(server, fn)(**{k: v for k, v in fargs.items()
                                                    if k in ("directory", "pattern", "sort_by",
                                                             "order", "query", "recursive", "limit")})
                    text = json.dumps(result)[:8000]
                except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                    text = f"Tool error: {exc}"
                trace.append({"tool": fn, "args": fargs, "result_chars": len(text)})
                messages.append({"role": "tool", "content": text})
        return jsonify({"answer": "Max tool iterations reached.", "tool_calls": trace})
    except Exception as exc:  # noqa: BLE001 - ollama daemon down, model missing, ...
        log.warning("Ollama request failed: %s", exc)
        return jsonify({"error": f"ollama request failed: {exc}"}), 502


def main() -> None:
    parser = argparse.ArgumentParser(description="Flask UI for the MCP file explorer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print(f"File explorer UI on http://{args.host}:{args.port} (model={MODEL})")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
