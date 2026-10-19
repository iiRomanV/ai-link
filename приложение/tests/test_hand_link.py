import sys
import tempfile
from pathlib import Path
from core.hand_link import HandLink, parse_model_response, safe_parse_model_response

def test_hand_link_create_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        hl = HandLink(tmpdir)
        result = hl.execute([{"type": "create_file", "path": "test.py", "content": "print('ok')"}])
        assert result["success"] is True
        assert (Path(tmpdir) / "test.py").exists()

def test_hand_link_run_command():
    with tempfile.TemporaryDirectory() as tmpdir:
        hl = HandLink(tmpdir)
        result = hl.execute([{"type": "run_command", "command": "echo hello", "timeout": 5}])
        assert result["success"] is True

def test_parse_json_response():
    text = '[{"type": "create_file", "path": "a.py", "content": "print(1)"}]'
    actions = parse_model_response(text)
    assert isinstance(actions, list)
    assert actions[0]["type"] == "create_file"

def test_safe_parse_empty():
    # None и пустая строка — пустой список
    assert safe_parse_model_response(None) == []
    assert safe_parse_model_response("") == []
    # Пустой JSON-массив — тоже пустой список (не создаём файл)
    assert safe_parse_model_response("[]") == []
    # Обычный текст — теперь сохраняется как result.txt (fallback)
    result = safe_parse_model_response("просто текст")
    assert len(result) == 1
    assert result[0]["type"] == "create_file"
    assert result[0]["path"] == "result.txt"


# ═══════════════════════════════════════════════════════════════
# B55-EXT (2026-10-13): changed_files + снапшот run_command.
# ═══════════════════════════════════════════════════════════════


def test_replace_in_file_goes_to_changed_not_created(tmp_path):
    """replace_in_file пишет в changed_files, не в created_files."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    f = proj / "x.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "replace_in_file",
        "path": "x.py",
        "find": "a = 1",
        "replace": "a = 999",
    }])
    assert r["success"]
    assert "x.py" in r.get("changed_files", [])
    assert "x.py" not in r.get("created_files", [])
    assert f.read_text(encoding="utf-8") == "a = 999\nb = 2\n"


def test_create_file_still_goes_to_created(tmp_path):
    """create_file пишет в created_files (регресс-защита)."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "create_file",
        "path": "new.py",
        "content": "x = 1\n",
    }])
    assert r["success"]
    assert "new.py" in r.get("created_files", [])
    assert "new.py" not in r.get("changed_files", [])


def test_snapshot_text_files_basic(tmp_path):
    """Снапшот видит .py/.json, игнорирует .png и skip-папки."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "b.json").write_text('{"k": 1}\n', encoding="utf-8")
    (proj / "c.png").write_bytes(b"\x89PNG\r\n")
    (proj / "__pycache__").mkdir()
    (proj / "__pycache__" / "d.py").write_text("y = 2\n", encoding="utf-8")

    hand = HandLink(str(proj))
    snap = hand._snapshot_text_files()
    assert "a.py" in snap
    assert "b.json" in snap
    assert "c.png" not in snap
    assert not any("__pycache__" in k for k in snap)
    # sha256 длиной 64
    assert len(snap["a.py"]) == 64


def test_run_command_creates_file_via_shell(tmp_path):
    """run_command через echo создаёт файл — created_by_command."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "echo 'hello = 1' > new_from_cmd.py",
    }])
    assert r["success"]
    assert "new_from_cmd.py" in r.get("created_by_command", [])
    assert (proj / "new_from_cmd.py").exists()


def test_run_command_changes_existing_file(tmp_path):
    """run_command через sed меняет файл — changed_by_command.

    Важно: размер одинаковый (1 → 2, обе цифры 1 байт),
    mtime может совпасть. Ловим только sha256.
    """
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    f = proj / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "sed -i 's/x = 1/x = 2/' x.py",
    }])
    assert r["success"]
    assert "x.py" in r.get("changed_by_command", [])
    assert "x.py" not in r.get("created_by_command", [])
    assert f.read_text(encoding="utf-8") == "x = 2\n"


def test_run_command_no_change(tmp_path):
    """Команда без изменений — оба списка пусты."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("x = 1\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "python3 -c 'print(1)'",
    }])
    assert r["success"]
    assert r.get("created_by_command") == []
    assert r.get("changed_by_command") == []


# ═══════════════════════════════════════════════════════════════
# Б68 (14.10.2026): защита секретов не блокирует .env.example.
# ═══════════════════════════════════════════════════════════════


def test_b68_env_example_not_blocked(tmp_path):
    """cat .env.example — РАЗРЕШЕНО."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env.example").write_text("API_KEY=xxx\n", encoding="utf-8")

    hand = HandLink(str(proj))
    # Не должен бросить ValueError
    r = hand.execute([{
        "type": "run_command",
        "command": "cat .env.example",
    }])
    assert r.get("success") is True


def test_b68_grep_env_example_not_blocked(tmp_path):
    """grep KEY .env.example — РАЗРЕШЕНО."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env.example").write_text("API_KEY=xxx\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "grep KEY .env.example",
    }])
    assert r.get("success") is True


def test_b68_env_template_not_blocked(tmp_path):
    """.env.template / .env.sample / .env.dist — РАЗРЕШЕНЫ."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env.template").write_text("API_KEY=xxx\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "cat .env.template",
    }])
    assert r.get("success") is True


def test_b68_real_env_blocked(tmp_path):
    """cat .env — БЛОКИРУЕТСЯ (регресс защиты)."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("API_KEY=real_secret\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "cat .env",
    }])
    assert r.get("success") is False
    assert "секрет" in (r.get("error") or "").lower()


def test_b68_env_local_blocked(tmp_path):
    """cat .env.local — БЛОКИРУЕТСЯ (не безопасный суффикс)."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env.local").write_text("API_KEY=real\n", encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "cat .env.local",
    }])
    assert r.get("success") is False


def test_b68_secrets_json_blocked(tmp_path):
    """cat secrets.json — БЛОКИРУЕТСЯ (регресс)."""
    from core.hand_link import HandLink
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "secrets.json").write_text('{"k": "v"}', encoding="utf-8")

    hand = HandLink(str(proj))
    r = hand.execute([{
        "type": "run_command",
        "command": "cat secrets.json",
    }])
    assert r.get("success") is False
