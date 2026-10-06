"""MCP Server: directory lister + file search.

Exposes two tools over MCP (stdio):

  - list_files(directory=".", pattern="*")  list files (default: current folder)
  - search_files(query, directory=".", recursive=True, limit=50)  find files by name

All paths are jailed to ALLOWED_ROOT (env, defaults to the current folder).

Run:
    python server.py
"""

from __future__ import annotations

import fnmatch
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from stat import filemode

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("file-lister")

# Jail root: the folder the server was started in (override with ALLOWED_ROOT).
ALLOWED_ROOT = Path(os.environ.get("ALLOWED_ROOT", os.getcwd())).resolve()

MAX_SEARCH_RESULTS = int(os.environ.get("MAX_SEARCH_RESULTS", "200"))

mcp = FastMCP("file-lister")

log.info("Allowed root: %s", ALLOWED_ROOT)


def _resolve_safe(directory: str) -> Path:
    """Resolve `directory` against ALLOWED_ROOT, blocking escapes. Defaults to cwd."""
    raw = (directory or ".").strip() or "."
    expanded = os.path.expanduser(raw)
    candidate = (
        (ALLOWED_ROOT / expanded).resolve()
        if not os.path.isabs(expanded)
        else Path(expanded).resolve()
    )
    try:
        candidate.relative_to(ALLOWED_ROOT)
    except ValueError:
        raise ValueError(f"Access denied: '{raw}' is outside allowed root {ALLOWED_ROOT}")
    if candidate.is_symlink():
        try:
            if not candidate.resolve().is_relative_to(ALLOWED_ROOT):
                raise ValueError(f"Access denied: symlink '{raw}' points outside allowed root")
        except AttributeError:  # pragma: no cover - Py<3.9 fallback
            if ALLOWED_ROOT not in candidate.resolve().parents:
                raise ValueError(f"Access denied: symlink '{raw}' points outside allowed root")
    return candidate


def _human_size(n: int | None) -> str | None:
    """Format bytes as '669.7 KB'. Returns None for directories."""
    if n is None:
        return None
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _entry_dict(path: Path) -> dict:
    """Build the JSON entry for one file or directory."""
    try:
        st = path.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        permissions = filemode(st.st_mode)  # e.g. "-rw-r--r--" / "drwxr-xr-x"
    except OSError:
        st, mtime, permissions = None, None, None  # type: ignore[assignment]
    kind = "directory" if path.is_dir() else "file"
    size = st.st_size if st is not None and path.is_file() else None
    return {
        "name": path.name,
        "path": str(path.relative_to(ALLOWED_ROOT)),
        "type": kind,
        "size_bytes": size,
        "size_human": _human_size(size),
        "modified": mtime,  # ISO-8601 datetime of last modification
        "extension": path.suffix.lower(),
        "permissions": permissions,  # file attributes, e.g. "-rw-r--r--"
    }


@mcp.tool()
def list_files(
    directory: str = ".",
    pattern: str = "*",
    sort_by: str = "modified",
    order: str = "desc",
) -> list[dict]:
    """List files in a folder, newest first (default: current folder, descending).

    Args:
        directory: Folder to list, relative to the current folder.
            Defaults to "." (the current folder).
        pattern: Glob filter for file names, e.g. "*.csv", "*.txt".
            Defaults to "*" (everything).
        sort_by: Sort key — "modified" (datetime), "size" or "name".
            Defaults to "modified".
        order: "desc" (descending, default) or "asc" (ascending).
    """
    target = _resolve_safe(directory)
    if not target.exists():
        raise ValueError(f"Directory not found: {directory}")
    if not target.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    sort_by = (sort_by or "modified").strip().lower()
    if sort_by not in ("modified", "size", "name"):
        raise ValueError(f"Invalid sort_by={sort_by!r}; use modified, size or name")
    descending = (order or "desc").strip().lower() != "asc"

    entries = [
        _entry_dict(path)
        for path in target.iterdir()
        if not path.name.startswith(".") and fnmatch.fnmatchcase(path.name, pattern or "*")
    ]
    if sort_by == "size":
        entries.sort(key=lambda e: (e["size_bytes"] is None, e["size_bytes"] or 0),
                     reverse=descending)
    elif sort_by == "name":
        entries.sort(key=lambda e: e["name"].lower(), reverse=descending)
    else:  # modified (datetime)
        entries.sort(key=lambda e: e["modified"] or "", reverse=descending)

    log.info("list_files dir=%s pattern=%s sort=%s %s -> %d entries",
             directory, pattern, sort_by, "desc" if descending else "asc", len(entries))
    return entries


@mcp.tool()
def search_files(query: str, directory: str = ".", recursive: bool = True, limit: int = 50) -> list[dict]:
    """Search file names for a substring (default: current folder, recursive).

    Args:
        query: Substring to look for in file names (case-insensitive).
        directory: Folder to search in, relative to the current folder.
            Defaults to "." (the current folder).
        recursive: Also search subfolders. Defaults to True.
        limit: Max matches to return.
    """
    if not (query or "").strip():
        raise ValueError("query must be non-empty")
    target = _resolve_safe(directory)
    if not target.exists():
        raise ValueError(f"Directory not found: {directory}")
    if not target.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    if isinstance(recursive, str):
        recursive = recursive.strip().lower() in ("1", "true", "yes", "y", "t", "on")
    limit = max(1, min(int(limit or 50), MAX_SEARCH_RESULTS))
    needle = query.strip().lower()

    hits: list[dict] = []
    if recursive:
        for root, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and not (Path(root) / d).is_symlink()]
            for name in dirs + files:
                if name.startswith("."):
                    continue
                if needle in name.lower():
                    p = Path(root) / name
                    try:
                        p.relative_to(ALLOWED_ROOT)
                    except ValueError:
                        continue
                    hits.append(_entry_dict(p))
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
    else:
        for path in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not path.name.startswith(".") and needle in path.name.lower():
                hits.append(_entry_dict(path))
                if len(hits) >= limit:
                    break

    log.info("search_files q=%r dir=%s -> %d hits", query, directory, len(hits))
    return hits


if __name__ == "__main__":
    mcp.run()
