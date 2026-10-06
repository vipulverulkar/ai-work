# Postgres MCP Server (stdio)

Local Postgres MCP server exposing `list_tables`, `describe_table`, `query` to MCP clients (tested with `dvdrental` DB + opencode).

Files:
- `postgres_mcp_server.py` — MCP server (stdio)
- `client_mcp.py` — minimal test client
- `opencode.json` — opencode wiring for `postgres-local` (no secrets)
- `.env.example` — template; copy to `.env` (gitignored, never commit)
- `.gitignore` — ignores `.env`, `.venv/`, `__pycache__/`
- `requirements.txt` — `mcp>=2`, `psycopg2-binary`, `python-dotenv`

## 1. Prerequisites

- Python 3.10+ (`python3 --version`)
- Postgres running locally with `dvdrental` DB. Verify with credentials from `.env` (no password on the command line):
  ```bash
  set -a; source .env; set +a
  pg_isready -h localhost -p 5432
  psql "$DATABASE_URL" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
  ```

## 2. Setup

Run from this folder (`/home/vipul/Documents/Code/mcp_vipul`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env with real values
```

Secrets live only in `.env` (gitignored). Both server and client auto-load it via `python-dotenv`, so no `export` needed:

| Var | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | yes | — (fails fast if unset) | Postgres connection |
| `PG_MAX_ROWS` | no | `100` | Max rows `query` returns |
| `PG_ALLOW_WRITE` | no | `0` | `0` = read-only (`SELECT/WITH/EXPLAIN/SHOW`), `1` = allow writes |

```bash
# .env
DATABASE_URL="postgresql://user:password@localhost:5432/dvdrental"
PG_MAX_ROWS=100
PG_ALLOW_WRITE=0
```

## 3. Run server (stdio)

Server speaks MCP over stdio — it blocks with no output, that's normal:

```bash
.venv/bin/python postgres_mcp_server.py
# server reads .env itself; shell env overrides .env if both set
```

## 4. Test with client

No need to start the server first — `client_mcp.py` spawns it over stdio itself (and loads `.env` for it):

```bash
.venv/bin/python client_mcp.py
```

Expected:
```
['list_tables', 'describe_table', 'query']
... 15 tables: actor, film, customer, ...
... actor columns: actor_id, first_name, last_name, last_update
... 10 actor rows
```

Note: `client_mcp.py` auto-uses the first table from `list_tables` — dvdrental has no `users` table.

## 5. Use with opencode

`opencode.json` in this folder is already configured — no secrets in it, the server reads `.env` itself:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "postgres-local": {
      "type": "local",
      "command": ["/home/vipul/Documents/Code/mcp_vipul/.venv/bin/python", "/home/vipul/Documents/Code/mcp_vipul/postgres_mcp_server.py"],
      "enabled": true
    }
  }
}
```

Verify (run from this folder so it picks up local `opencode.json`):

```bash
opencode mcp list
# should show: ✓ postgres-local connected
```

If you moved the folder, update the two absolute paths in `opencode.json`. Restart opencode TUI after config changes.

### Desktop app

1. Make sure `.env` exists in this folder (the server reads it itself — no `export` needed).
2. Make sure Postgres is running: `pg_isready -h localhost -p 5432`.
3. In opencode Desktop, open this folder (`/home/vipul/Documents/Code/mcp_vipul`) as the project — the local `opencode.json` is picked up automatically. (Paths in it are absolute, so the desktop sidecar finds the server regardless of its working directory.)
4. Open Settings → MCP and confirm `postgres-local` shows connected. If it doesn't appear, fully quit the app and reopen it, and update the app to the latest version (early v1.15.x desktop builds had a bug where the MCP panel stayed empty while the CLI worked).
5. Cross-check in a terminal from this folder: `opencode mcp list` should show `✓ postgres-local connected`.

Alternative (all projects): copy the `mcp` block into the global config at `~/.config/opencode/opencode.json` instead of using the project-local file.

Tools available in opencode as `postgres-local_list_tables`, `postgres-local_describe_table`, `postgres-local_query`.

## 6. Troubleshooting

- `can't open file '/path/to/...'` → you have an old `client_mcp.py`; pull latest (uses `Path(__file__).with_name(...)` + `sys.executable`).
- `relation "users" does not exist` → use `actor` / `film` — dvdrental has no `users`.
- `connection failed / OperationalError` → check `.env` values, `pg_isready`.
- `opencode mcp list` shows no servers → run from this folder, or copy the `mcp` block to `~/.config/opencode/opencode.json`.
- Write blocked → default is read-only; set `PG_ALLOW_WRITE=1` in `.env` only if you need writes.
- `RuntimeError: DATABASE_URL is not set` → copy `.env.example` to `.env` and fill it in.
