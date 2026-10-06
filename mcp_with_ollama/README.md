# MCP + Ollama File Explorer

MCP server (`server.py`) exposing file listing + search tools (both default to the current folder), plus an Ollama agent (`client_ollama.py`) that calls them via tool-calling.

## Tools

| Tool | Purpose | Key args |
|---|---|---|
| `list_files` | List files, newest first (default: current folder) | `directory`, `pattern` glob e.g. `"*.csv"`, `sort_by` (modified\|size\|name), `order` (desc\|asc) |
| `search_files` | Find files by name substring (default: current folder, recursive) | `query` (required), `directory`, `recursive`, `limit` |

Each entry shows datetime (`modified`, ISO-8601), file size (`size_bytes` + `size_human` like `"654.0 KB"`) and attributes (`type`, `extension`, `permissions` like `"-rw-r--r--"`). Hidden dotfiles are skipped.

All paths are jailed to `ALLOWED_ROOT` (default: the current folder). Symlinks escaping it are blocked.

## How to run the app

Prerequisites: Python 3.10+ and [Ollama](https://ollama.com) installed.

```bash
# 1. Start Ollama (separate terminal) and pull a model
ollama serve
ollama pull llama3.1:8b

# 2. Install dependencies (project folder)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Sanity check — MCP server without any LLM (should list 2 tools)
python test_mcp_direct.py

# 4. Run the app — enter a directory, then ask about its files in plain English
python client_ollama.py --model llama3.1:8b
# Enter directory to browse [.]: sample_files
# > list all csv files
# > search for users
# Commands: /cd <dir> /tools /history /clear /model <name> /quit

# Or skip the prompt with --directory and ask directly:
python client_ollama.py --directory sample_files --prompt "search for users" --model llama3.1:8b
```

Expected: the agent calls `search_files(query="users", directory="sample_files")`, then prints a summary like
`Found 2 files — users.csv (105 B), large_users.csv (669,717 B)`.

Troubleshooting:

| Symptom | Fix |
|---|---|
| `[ollama connection error]` | `ollama serve` isn't running — start it, check `OLLAMA_HOST` (default `http://localhost:11434`) |
| `model not found` / `pull model` | Run `ollama pull llama3.1:8b` (or set `--model qwen2.5:7b` to use another local model) |
| `Access denied ... outside allowed root` | Path is outside `ALLOWED_ROOT` — `cd` into the right folder or set `ALLOWED_ROOT=/path/to/files` |
| `Directory not found` | The folder doesn't exist under the current folder — check the name with `list_files` first |

See [Setup](#setup) for env vars and [Run](#run) for every command.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # optional
ollama pull llama3.1:8b
```

Env vars:

| Var | Default | Purpose |
|---|---|---|
| `ALLOWED_ROOT` | current folder | Jail root for all file access |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model for the agent |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama daemon URL |
| `OLLAMA_TEMPERATURE` | `0.1` | Generation temperature |
| `MCP_MAX_ITERS` | `6` | Max tool-call rounds per prompt |
| `LOG_LEVEL` | `INFO` | Server logging verbosity |

### Config file (`config.json`)

The model and client settings live in `config.json` — the app never asks for the model interactively:

```json
{
  "model": "llama3.1:8b",
  "host": "http://localhost:11434",
  "temperature": 0.1,
  "max_iters": 6,
  "verbose": false,
  "directory": ".",
  "allowed_root": null
}
```

Precedence: **CLI flag > environment variable > `config.json` > built-in default**.
To switch models permanently, edit `"model"` in `config.json`; for one run only, use `--model <name>` or `--config other.json`.

## Run

```bash
# 1. MCP smoke test (no LLM) — lists current folder + sample_files
python test_mcp_direct.py

# 2. Full pytest suite
pytest -v

# 3. Ollama + MCP, single prompt (uses --directory as the default folder)
python client_ollama.py --directory sample_files --prompt "list all csv files" --model llama3.1:8b --verbose

# 4. Interactive: enter a directory, then browse/search it
python client_ollama.py --model llama3.1:8b
# Commands inside: /cd <dir> /tools /history /clear /model <name> /quit

# 5. Or use the web UI (browse + search + ask in the browser)
python frontend/app.py
# open http://127.0.0.1:5000 — enter a directory, filter by pattern,
# sort newest/biggest/name, search by name, or ask in plain English
# (model comes from config.json; API: /api/files /api/search /api/ask)

# 6. Server standalone (e.g. for MCP Inspector / Claude Desktop)
python server.py
```

## Examples

```bash
python client_ollama.py --directory sample_files --prompt "what csv files are here?" --verbose
python client_ollama.py --prompt "search for users starting from ." --verbose
ALLOWED_ROOT=/tmp python server.py
```

## Sample data (`sample_files/`)

| File | Records | Size | Description |
|---|---|---|---|
| `users.csv` | 5 rows | ~105 B | Small demo (id, name, age, city) |
| `products.csv` | 4 rows | ~89 B | Small demo (product, price, stock) |
| `example.txt` | 1 line | ~26 B | Minimal text sample |
| `notes.txt` | 8 lines | ~232 B | Meeting-notes sample |
| `demo.py` | — | ~27 B | Tiny Python sample |
| `large_users.csv` | **10,000 rows** | ~654 KB | Generated demo (id, name, email, age, city, signup_date) |
| `large_logs.txt` | **10,000 lines** | ~779 KB | Generated log lines (timestamp, level, request_id, latency) |

> Note: `list_files` returns names, sizes and dates only — it does not read file contents.

## Project layout

```
server.py            MCP server (list_files + search_files, stdio)
client_ollama.py     Ollama agent (persistent MCP session, REPL)
frontend/
  app.py               Flask web UI (browse, search, ask) + JSON API
  templates/index.html UI page (no build step)
tests/
  test_server_tools.py    unit tests (direct tool calls)
  test_mcp_transport.py   live MCP stdio integration test
  test_flask_ui.py        Flask route tests (test client)
tests/
  test_server_tools.py    unit tests (direct tool calls)
  test_mcp_transport.py   live MCP stdio integration test
test_mcp_direct.py   quick smoke test (no LLM needed)
sample_files/        demo data (small + 10k-record files)
config.json          client settings (model, host, directory, ...)
pyproject.toml       packaging + pytest config
requirements.txt     mcp, ollama, pydantic, pytest
.env.example         env var template
```
