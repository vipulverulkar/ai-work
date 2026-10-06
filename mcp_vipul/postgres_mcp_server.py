import os
import re
from contextlib import contextmanager
from pathlib import Path
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from mcp.server import MCPServer

load_dotenv(Path(__file__).with_name(".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and set it.")
MAX_ROWS = int(os.getenv("PG_MAX_ROWS", "100"))
ALLOW_WRITE = os.getenv("PG_ALLOW_WRITE", "0") == "1"

mcp = MCPServer(name="postgres", title="Local Postgres", version="1.0.0")

FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|CALL)\b", re.I)

def _check_readonly(sql: str):
    s = sql.strip().rstrip(";")
    if not s:
        raise ValueError("Empty SQL")
    if not ALLOW_WRITE and FORBIDDEN.search(s):
        raise ValueError("Write blocked (set PG_ALLOW_WRITE=1 to allow).")
    if not ALLOW_WRITE and not re.match(r"(?is)^\s*(SELECT|WITH|EXPLAIN|SHOW|DESCRIBE)\b", s):
        raise ValueError("Only SELECT/WITH/EXPLAIN/SHOW allowed.")

@contextmanager
def _conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

@mcp.tool()
def list_tables(schema: str = "public") -> list[str]:
    """List tables in a schema."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,))
            return [r[0] for r in cur.fetchall()]

@mcp.tool()
def describe_table(table: str, schema: str = "public") -> list[dict]:
    """Describe columns for table."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position
            """, (schema, table))
            return [dict(r) for r in cur.fetchall()]

@mcp.tool()
def query(sql: str, limit: int = 50) -> list[dict]:
    """Run a read-only SQL query."""
    _check_readonly(sql)
    limit = max(1, min(limit, MAX_ROWS))
    if re.match(r"(?is)^\s*SELECT\b", sql.strip()) and not re.search(r"(?is)\bLIMIT\b", sql):
        sql = sql.rstrip().rstrip(";") + f" LIMIT {limit}"
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            if cur.description is None:
                conn.commit()
                return [{"status": "ok"}]
            return [dict(r) for r in cur.fetchall()][:limit]

if __name__ == "__main__":
    mcp.run()