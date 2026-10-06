"""Unit tests for the minimal MCP directory-lister server."""
from __future__ import annotations

from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _cwd(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(server, "ALLOWED_ROOT", ROOT.resolve())


def test_list_files_current_folder_by_default():
    entries = server.list_files()
    assert isinstance(entries, list)
    assert entries, "expected at least one entry in the project folder"
    names = {e["name"] for e in entries}
    assert "server.py" in names
    assert "sample_files" in names


def test_list_files_entry_shape():
    entries = server.list_files(directory=".")
    for e in entries:
        assert set(e) == {"name", "path", "type", "size_bytes", "size_human",
                          "modified", "extension", "permissions"}
        assert e["type"] in ("file", "directory")
        assert e["modified"], "expected ISO datetime"
        assert e["permissions"], "expected file attributes (e.g. -rw-r--r--)"
        if e["type"] == "file":
            assert isinstance(e["size_bytes"], int)
            assert e["size_human"].endswith(("B", "KB", "MB", "GB", "TB"))


def test_list_files_subfolder():
    entries = server.list_files(directory="sample_files")
    names = {e["name"] for e in entries}
    assert "users.csv" in names
    assert "example.txt" in names


def test_list_files_default_is_modified_descending():
    entries = server.list_files(directory=".")
    mtimes = [e["modified"] or "" for e in entries]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_files_sort_by_size_descending():
    entries = server.list_files(directory="sample_files", sort_by="size", order="desc")
    sizes = [e["size_bytes"] or 0 for e in entries]
    assert sizes == sorted(sizes, reverse=True)
    assert entries[0]["name"] == "large_logs.txt"  # biggest file first


def test_list_files_sort_by_name_ascending():
    entries = server.list_files(directory="sample_files", sort_by="name", order="asc")
    names = [e["name"] for e in entries]
    assert names == sorted(names, key=str.lower)


def test_list_files_invalid_sort_rejected():
    with pytest.raises(ValueError, match="sort_by"):
        server.list_files(directory=".", sort_by="bogus")


def test_list_files_skips_hidden(tmp_path):
    (tmp_path / ".secret").write_text("hidden")
    import shutil
    dest = ROOT / "sample_files" / ".tmp_hidden"
    try:
        shutil.copy(tmp_path / ".secret", dest)
        names = {e["name"] for e in server.list_files(directory="sample_files")}
        assert ".tmp_hidden" not in names
    finally:
        if dest.exists():
            dest.unlink()


def test_list_files_pattern_filter():
    entries = server.list_files(directory="sample_files", pattern="*.csv")
    assert entries, "expected csv files in sample_files"
    assert all(e["name"].endswith(".csv") for e in entries)


def test_search_files_by_name():
    hits = server.search_files(query="users", directory=".")
    assert hits, "expected hits for 'users'"
    assert all("users" in h["name"].lower() for h in hits)


def test_search_files_case_insensitive():
    lower = server.search_files(query="users", directory="sample_files")
    upper = server.search_files(query="USERS", directory="sample_files")
    assert [h["name"] for h in lower] == [h["name"] for h in upper]


def test_search_files_non_recursive():
    hits = server.search_files(query="users", directory=".", recursive=False)
    assert all(h["path"].count("/") == 0 for h in hits)


def test_search_files_empty_query_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        server.search_files(query="  ")


def test_path_traversal_blocked():
    with pytest.raises(ValueError, match="[Oo]utside|denied"):
        server.list_files(directory="../..")
    with pytest.raises(ValueError, match="[Oo]utside|denied"):
        server.search_files(query="x", directory="../..")


def test_missing_dir_errors():
    with pytest.raises(ValueError, match="[Nn]ot found|[Nn]ot a directory"):
        server.list_files(directory="no_such_dir_xyz")

    with pytest.raises(ValueError, match="[Nn]ot a directory"):
        server.list_files(directory="server.py")
