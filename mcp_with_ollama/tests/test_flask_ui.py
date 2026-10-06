"""Tests for the Flask UI (uses Flask's test client, no live server)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "frontend") not in sys.path:
    sys.path.insert(0, str(ROOT / "frontend"))

import frontend.app as flask_app  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.chdir(ROOT)
    import server
    monkeypatch.setattr(server, "ALLOWED_ROOT", ROOT.resolve())
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"File Explorer" in r.data


def test_api_files_default(client):
    r = client.get("/api/files")
    assert r.status_code == 200
    data = r.get_json()
    assert data["total"] >= 1
    assert "server.py" in {e["name"] for e in data["entries"]}


def test_api_files_pattern_and_sort(client):
    r = client.get("/api/files", query_string={"directory": "sample_files",
                                               "pattern": "*.csv",
                                               "sort_by": "size", "order": "desc"})
    assert r.status_code == 200
    entries = r.get_json()["entries"]
    assert entries and all(e["name"].endswith(".csv") for e in entries)
    sizes = [e["size_bytes"] or 0 for e in entries]
    assert sizes == sorted(sizes, reverse=True)


def test_api_files_bad_dir(client):
    r = client.get("/api/files", query_string={"directory": "nope"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_api_search(client):
    r = client.get("/api/search", query_string={"query": "users", "directory": "."})
    assert r.status_code == 200
    data = r.get_json()
    assert data["total"] >= 1


def test_api_search_missing_query(client):
    r = client.get("/api/search")
    assert r.status_code == 400


def test_api_ask_missing_prompt(client):
    r = client.post("/api/ask", json={"prompt": "  "})
    assert r.status_code == 400
