"""Тесты для core.archive_extract (Этап 3.5, 2026-10-08)."""

import tarfile
import zipfile
from pathlib import Path

from core.archive_extract import (
    detect_archive,
    extract_archive,
    find_archives_in,
    _is_skipped,
)


def test_detect_archive_tar_gz(tmp_path):
    f = tmp_path / "x.tar.gz"
    f.write_bytes(b"")
    assert detect_archive(str(f)) == ".tar.gz"


def test_detect_archive_zip(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"")
    assert detect_archive(str(f)) == ".zip"


def test_detect_archive_7z(tmp_path):
    f = tmp_path / "x.7z"
    f.write_bytes(b"")
    assert detect_archive(str(f)) == ".7z"


def test_detect_archive_not_archive(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("hi")
    assert detect_archive(str(f)) is None


def test_detect_archive_nonexistent(tmp_path):
    assert detect_archive(str(tmp_path / "nope.zip")) is None


def test_extract_tar_gz(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.py").write_text("print('hi')")
    (src / "README.md").write_text("# Test")

    arch = tmp_path / "proj.tar.gz"
    with tarfile.open(str(arch), "w:gz") as tf:
        tf.add(str(src), arcname="proj")

    r = extract_archive(str(arch))
    assert r["ok"] is True, r
    dst = Path(r["extracted_to"])
    assert dst.exists()
    assert (dst / "proj" / "main.py").exists()
    assert (dst / "proj" / "README.md").exists()


def test_extract_zip(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "app.py").write_text("x = 1")

    arch = tmp_path / "proj.zip"
    with zipfile.ZipFile(str(arch), "w") as zf:
        zf.write(str(src / "app.py"), arcname="proj/app.py")

    r = extract_archive(str(arch))
    assert r["ok"] is True
    dst = Path(r["extracted_to"])
    assert (dst / "proj" / "app.py").exists()


def test_extract_skips_macos_garbage(tmp_path):
    arch = tmp_path / "p.zip"
    with zipfile.ZipFile(str(arch), "w") as zf:
        zf.writestr("proj/main.py", "x = 1")
        zf.writestr("__MACOSX/._main.py", "garbage")
        zf.writestr(".DS_Store", "garbage")

    r = extract_archive(str(arch))
    assert r["ok"] is True
    dst = Path(r["extracted_to"])
    assert (dst / "proj" / "main.py").exists()
    assert not (dst / "__MACOSX").exists()
    assert not (dst / ".DS_Store").exists()


def test_extract_refuses_existing_dst(tmp_path):
    src = tmp_path / "p"
    src.mkdir()
    (src / "x.txt").write_text("x")
    arch = tmp_path / "p.tar.gz"
    with tarfile.open(str(arch), "w:gz") as tf:
        tf.add(str(src), arcname="p")

    # Первый раз — ок.
    r1 = extract_archive(str(arch))
    assert r1["ok"] is True

    # Второй раз — dst уже есть → ошибка.
    r2 = extract_archive(str(arch))
    assert r2["ok"] is False
    assert "уже существует" in r2["error"]


def test_extract_not_archive(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("not archive")
    r = extract_archive(str(f))
    assert r["ok"] is False
    assert "не архив" in r["error"]


def test_find_archives_in(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"")
    (tmp_path / "b.tar.gz").write_bytes(b"")
    (tmp_path / "c.txt").write_text("x")
    (tmp_path / "subdir").mkdir()

    found = find_archives_in(str(tmp_path))
    names = [Path(p).name for p in found]
    assert "a.zip" in names
    assert "b.tar.gz" in names
    assert "c.txt" not in names


def test_is_skipped():
    assert _is_skipped("__MACOSX/x") is True
    assert _is_skipped(".DS_Store") is True
    assert _is_skipped("proj/.git/config") is True
    assert _is_skipped("proj/node_modules/x") is True
    assert _is_skipped("proj/main.py") is False
