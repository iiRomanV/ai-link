import json
import os
from pathlib import Path
from typing import Optional

CREDENTIALS_FILE = Path.home() / ".ai_link" / "credentials.json"

def _load_credentials_file() -> dict:
    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_credentials_file(data: dict):
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(CREDENTIALS_FILE, 0o600)

def save_secret(service: str, username: str, secret: str):
    """Сохраняет секрет в keyring или файл с ограниченными правами."""
    try:
        import keyring
        keyring.set_password(service, username, secret)
    except Exception:
        data = _load_credentials_file()
        if service not in data:
            data[service] = {}
        data[service][username] = secret
        _save_credentials_file(data)

def get_secret(service: str, username: str) -> Optional[str]:
    """Читает секрет из keyring или файла."""
    try:
        import keyring
        secret = keyring.get_password(service, username)
        if secret is not None:
            return secret
    except Exception:
        pass
    data = _load_credentials_file()
    return data.get(service, {}).get(username)

def delete_secret(service: str, username: str):
    """Удаляет секрет."""
    try:
        import keyring
        keyring.delete_password(service, username)
    except Exception:
        pass
    data = _load_credentials_file()
    if service in data and username in data[service]:
        del data[service][username]
        _save_credentials_file(data)
