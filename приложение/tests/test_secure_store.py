"""Тесты для core.secure_store."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from core import secure_store
from core.secure_store import (
    save_secret, get_secret, delete_secret,
    _load_credentials_file, _save_credentials_file,
)


def _patch_creds(tmp_path):
    """Патч пути к файлу credentials."""
    return patch.object(secure_store, "CREDENTIALS_FILE",
                        tmp_path / "credentials.json")


def _patch_keyring_fail():
    """Мокает keyring так, чтобы он падал (использовался файл)."""
    mock_kr = MagicMock()
    mock_kr.set_password.side_effect = Exception("keyring недоступен")
    mock_kr.get_password.side_effect = Exception("keyring недоступен")
    mock_kr.delete_password.side_effect = Exception("keyring недоступен")
    return patch.dict("sys.modules", {"keyring": mock_kr})


# === _load_credentials_file ===

def test_load_credentials_no_file(tmp_path):
    with _patch_creds(tmp_path):
        assert _load_credentials_file() == {}


def test_load_credentials_broken_json(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("невалидный json", encoding="utf-8")
    with patch.object(secure_store, "CREDENTIALS_FILE", path):
        assert _load_credentials_file() == {}


def test_load_credentials_ok(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text('{"service": {"user": "secret"}}', encoding="utf-8")
    with patch.object(secure_store, "CREDENTIALS_FILE", path):
        data = _load_credentials_file()
        assert data["service"]["user"] == "secret"


# === _save_credentials_file ===

def test_save_credentials_creates_file(tmp_path):
    with _patch_creds(tmp_path):
        _save_credentials_file({"s": {"u": "v"}})
        assert (tmp_path / "credentials.json").exists()


def test_save_credentials_permissions(tmp_path):
    with _patch_creds(tmp_path):
        _save_credentials_file({"s": {"u": "v"}})
        mode = os.stat(tmp_path / "credentials.json").st_mode
        # Проверяем, что права 0o600 (только для владельца)
        assert (mode & 0o777) == 0o600


# === save_secret / get_secret (файл, keyring недоступен) ===

def test_save_and_get_secret(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("myservice", "user1", "secret1")
        assert get_secret("myservice", "user1") == "secret1"


def test_get_secret_missing(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        assert get_secret("no_service", "no_user") is None


def test_save_two_services(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("s1", "u", "v1")
        save_secret("s2", "u", "v2")
        assert get_secret("s1", "u") == "v1"
        assert get_secret("s2", "u") == "v2"


def test_save_two_users_same_service(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("service", "user1", "secret1")
        save_secret("service", "user2", "secret2")
        assert get_secret("service", "user1") == "secret1"
        assert get_secret("service", "user2") == "secret2"


def test_save_overwrites(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("service", "user", "old")
        save_secret("service", "user", "new")
        assert get_secret("service", "user") == "new"


# === delete_secret ===

def test_delete_secret(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("service", "user", "secret")
        assert get_secret("service", "user") == "secret"
        delete_secret("service", "user")
        assert get_secret("service", "user") is None


def test_delete_missing(tmp_path):
    # Не должно падать
    with _patch_creds(tmp_path), _patch_keyring_fail():
        delete_secret("no_service", "no_user")


def test_delete_removes_only_target(tmp_path):
    with _patch_creds(tmp_path), _patch_keyring_fail():
        save_secret("s1", "u", "v1")
        save_secret("s2", "u", "v2")
        delete_secret("s1", "u")
        assert get_secret("s1", "u") is None
        assert get_secret("s2", "u") == "v2"


# === keyring работает ===

def test_save_secret_keyring_ok(tmp_path):
    """Если keyring работает — файл не создаётся."""
    with _patch_creds(tmp_path):
        mock_kr = MagicMock()
        mock_kr.set_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            save_secret("s", "u", "v")
            # keyring принял — файла быть не должно
            assert not (tmp_path / "credentials.json").exists()


def test_get_secret_from_keyring(tmp_path):
    """Если keyring вернул — используем его."""
    with _patch_creds(tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "from_keyring"
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            assert get_secret("s", "u") == "from_keyring"


def test_get_secret_keyring_none_fallback_file(tmp_path):
    """keyring вернул None — читаем из файла."""
    with _patch_creds(tmp_path):
        # Сначала пишем через mock-падающий keyring (в файл)
        with _patch_keyring_fail():
            save_secret("s", "u", "from_file")
        # Теперь keyring вернёт None
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            assert get_secret("s", "u") == "from_file"
