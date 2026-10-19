import sys
import tempfile
from core.hand_link import HandLink

def test_create_and_read():
    with tempfile.TemporaryDirectory() as d:
        h = HandLink(d)
        r = h.execute([{"type": "create_file", "path": "a.txt", "content": "Привет"}])
        assert r["success"] is True
        r = h.execute([{"type": "read_file", "path": "a.txt"}])
        assert r["success"] is True
        assert r["read_results"]["a.txt"] == "Привет"

def test_replace_in_file():
    with tempfile.TemporaryDirectory() as d:
        h = HandLink(d)
        h.execute([{"type": "create_file", "path": "a.txt", "content": "Привет мир"}])
        r = h.execute([{"type": "replace_in_file", "path": "a.txt", "find": "мир", "replace": "AI"}])
        assert r["success"] is True
        content = open(d + "/a.txt").read()
        assert content == "Привет AI"

def test_list_directory():
    with tempfile.TemporaryDirectory() as d:
        h = HandLink(d)
        h.execute([{"type": "create_file", "path": "a.txt", "content": "x"}])
        h.execute([{"type": "create_file", "path": "b.txt", "content": "y"}])
        r = h.execute([{"type": "list_directory", "path": "."}])
        assert r["success"] is True
        items = r["list_results"]["."]
        assert "a.txt" in items
        assert "b.txt" in items

def test_escape_blocked():
    with tempfile.TemporaryDirectory() as d:
        h = HandLink(d)
        r = h.execute([{"type": "create_file", "path": "../evil.txt", "content": "x"}])
        assert r["success"] is False

def test_verified_files():
    with tempfile.TemporaryDirectory() as d:
        h = HandLink(d)
        r = h.execute([{"type": "create_file", "path": "a.txt", "content": "test"}])
        assert r["success"] is True
        assert len(r["verified_files"]) == 1
        assert r["verified_files"][0]["exists"] is True


# ═══════════════════════════════════════════════════════════════
# W3: read_file с offset/limit
# ═══════════════════════════════════════════════════════════════

def _make_big_file(d, n=100):
    """Создаёт файл с n строками 'line 0' ... 'line n-1'."""
    import os as _os
    p = _os.path.join(d, "big.py")
    with open(p, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(f"line {i}\n")


def test_read_file_offset():
    """W3: read_file с offset — читает с нужной строки."""
    with tempfile.TemporaryDirectory() as d:
        _make_big_file(d, 100)
        h = HandLink(d)
        r = h.execute([{
            "type": "read_file", "path": "big.py", "offset": 50,
        }])
        assert r["success"] is True
        content = r["read_results"]["big.py"]
        assert content.startswith("line 50")
        assert content.count("\n") == 50


def test_read_file_limit():
    """W3: read_file с limit — читает N строк с начала."""
    with tempfile.TemporaryDirectory() as d:
        _make_big_file(d, 100)
        h = HandLink(d)
        r = h.execute([{
            "type": "read_file", "path": "big.py", "limit": 5,
        }])
        assert r["success"] is True
        content = r["read_results"]["big.py"]
        assert content.startswith("line 0")
        assert content.count("\n") == 5


def test_read_file_offset_limit():
    """W3: read_file с offset и limit — читает нужный кусок."""
    with tempfile.TemporaryDirectory() as d:
        _make_big_file(d, 100)
        h = HandLink(d)
        r = h.execute([{
            "type": "read_file", "path": "big.py",
            "offset": 20, "limit": 10,
        }])
        assert r["success"] is True
        content = r["read_results"]["big.py"]
        assert content.startswith("line 20")
        assert content.count("\n") == 10


def test_read_file_no_offset_limit():
    """W3: регресс — без offset/limit читает целиком."""
    with tempfile.TemporaryDirectory() as d:
        _make_big_file(d, 100)
        h = HandLink(d)
        r = h.execute([{
            "type": "read_file", "path": "big.py",
        }])
        assert r["success"] is True
        content = r["read_results"]["big.py"]
        assert content.startswith("line 0")
        assert content.count("\n") == 100


# ═══════════════════════════════════════════════════════════════
#  FIX 2026-09-26: append_to_file, move_file, copy_file, file_metadata
# ═══════════════════════════════════════════════════════════════


def test_append_to_file_creates(tmp_path):
    """append_to_file на несуществующем файле — создаёт."""
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "append_to_file",
        "path": "new.py",
        "content": "line1\n",
    }])
    assert r["success"], r.get("error")
    assert (tmp_path / "new.py").exists()
    assert (tmp_path / "new.py").read_text() == "line1\n"


def test_append_to_file_appends(tmp_path):
    """append_to_file дописывает в конец."""
    (tmp_path / "log.txt").write_text("first\n", encoding="utf-8")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "append_to_file",
        "path": "log.txt",
        "content": "second\n",
    }])
    assert r["success"], r.get("error")
    content = (tmp_path / "log.txt").read_text()
    assert content == "first\nsecond\n"


def test_append_to_file_creates_parent_dirs(tmp_path):
    """append_to_file создаёт родительские папки."""
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "append_to_file",
        "path": "sub/dir/file.py",
        "content": "x = 1\n",
    }])
    assert r["success"], r.get("error")
    assert (tmp_path / "sub" / "dir" / "file.py").exists()


def test_move_file(tmp_path):
    """move_file переименовывает."""
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "move_file",
        "source": "old.py",
        "destination": "new.py",
    }])
    assert r["success"], r.get("error")
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "new.py").exists()


def test_move_file_missing_source(tmp_path):
    """move_file на несуществующий — ошибка."""
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "move_file",
        "source": "nope.py",
        "destination": "new.py",
    }])
    assert not r["success"]


def test_copy_file(tmp_path):
    """copy_file копирует."""
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "copy_file",
        "source": "src.py",
        "destination": "dst.py",
    }])
    assert r["success"], r.get("error")
    assert (tmp_path / "src.py").exists()
    assert (tmp_path / "dst.py").exists()
    assert (tmp_path / "src.py").read_text() == (tmp_path / "dst.py").read_text()


def test_copy_file_missing_source(tmp_path):
    """copy_file на несуществующий — ошибка."""
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "copy_file",
        "source": "nope.py",
        "destination": "dst.py",
    }])
    assert not r["success"]


def test_file_metadata_file(tmp_path):
    """file_metadata возвращает size/lines."""
    (tmp_path / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "file_metadata",
        "path": "f.py",
    }])
    assert r["success"], r.get("error")
    meta = r.get("metadata_results", {}).get("f.py", {})
    assert meta.get("size", 0) > 0
    assert meta.get("lines") == 3


def test_file_metadata_dir(tmp_path):
    """file_metadata для папки — items."""
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "d" / "b.py").write_text("y = 2\n", encoding="utf-8")
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "file_metadata",
        "path": "d",
    }])
    assert r["success"], r.get("error")
    meta = r.get("metadata_results", {}).get("d", {})
    assert meta.get("is_dir") is True
    assert meta.get("items") == 2


def test_file_metadata_missing(tmp_path):
    """file_metadata несуществующего — error в meta."""
    hl = HandLink(str(tmp_path))
    r = hl.execute([{
        "type": "file_metadata",
        "path": "nope.py",
    }])
    # execute может быть успешным, но meta содержит ошибку
    meta = r.get("metadata_results", {}).get("nope.py", {})
    assert meta.get("error") or not r["success"]
