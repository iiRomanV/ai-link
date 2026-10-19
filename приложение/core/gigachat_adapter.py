import requests
import datetime
import uuid
from typing import Dict, Any, Optional
from core.secure_store import get_secret, save_secret

GIGACHAT_SERVICE = "gigachat"
GIGACHAT_TOKEN_KEY = "access_token"
GIGACHAT_EXPIRES_KEY = "expires_at"

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

def get_credentials() -> Dict[str, str]:
    """Возвращает Authorization и RqUID из secure_store."""
    auth = get_secret(GIGACHAT_SERVICE, "authorization")
    rquid = get_secret(GIGACHAT_SERVICE, "rquid")
    return {"authorization": auth, "rquid": rquid}

def _get_saved_token() -> Optional[str]:
    token = get_secret(GIGACHAT_SERVICE, GIGACHAT_TOKEN_KEY)
    expires_str = get_secret(GIGACHAT_SERVICE, GIGACHAT_EXPIRES_KEY)
    if token and expires_str:
        try:
            expires_at = datetime.datetime.fromisoformat(expires_str)
            # Если до истечения больше 6 минут, используем
            if datetime.datetime.now() < expires_at - datetime.timedelta(minutes=6):
                return token
        except Exception:
            pass
    return None

def _save_token(token: str, expires_in_seconds: int):
    expires_at = datetime.datetime.now() + datetime.timedelta(seconds=expires_in_seconds)
    save_secret(GIGACHAT_SERVICE, GIGACHAT_TOKEN_KEY, token)
    save_secret(GIGACHAT_SERVICE, GIGACHAT_EXPIRES_KEY, expires_at.isoformat())

def get_access_token() -> Dict[str, Any]:
    """Возвращает действующий access_token. При необходимости обновляет."""
    token = _get_saved_token()
    if token:
        return {"success": True, "token": token}

    creds = get_credentials()
    auth = creds.get("authorization")
    if not auth:
        return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": "Отсутствует Authorization для GigaChat. Добавьте его в настройках.", "metadata": {}}

    # RqUID генерируем автоматически, если он не задан
    rquid = creds.get("rquid") or str(uuid.uuid4())

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": rquid,
        "Authorization": f"Basic {auth}"
    }
    payload = {"scope": "GIGACHAT_API_PERS"}
    try:
        resp = requests.post(OAUTH_URL, headers=headers, data=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 1800)
            if access_token:
                _save_token(access_token, int(expires_in))
                return {"success": True, "token": access_token}
            else:
                return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": "В ответе OAuth нет access_token", "metadata": {}}
        else:
            return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": f"OAuth ошибка {resp.status_code}: {resp.text[:200]}", "metadata": {}}
    except Exception as e:
        return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": str(e), "metadata": {}}

def refresh_gigachat_token_if_needed():
    """Вызывает get_access_token для обновления токена, если он истекает (для таймера)."""
    get_access_token()

def query_gigachat(task: str, model: str = "GigaChat:latest",
                   chat_mode: bool = False) -> Dict[str, Any]:
    """Отправляет задачу в GigaChat и возвращает текст ответа.

    chat_mode=False (по умолчанию) — проектный режим: JSON-массив действий.
    chat_mode=True — простой диалог: обычный текст, без SYSTEM_PROMPT.
    """
    token_result = get_access_token()
    if not token_result["success"]:
        return token_result

    token = token_result["token"]

    if chat_mode:
        # Простой диалог — без проектного SYSTEM_PROMPT
        messages = [
            {"role": "user", "content": task}
        ]
        temperature = 0.7
    else:
        # Проектный режим — с SYSTEM_PROMPT для JSON
        messages = [
            {"role": "system", "content": "Ты — агент для выполнения задач разработки. Отвечай только JSON-массивом действий (как в HandLink)."},
            {"role": "user", "content": task}
        ]
        temperature = 0.2

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"]
                return {"source": "gigachat", "kind": "model_response", "success": True, "text": text, "error": None, "metadata": {}}
            except (KeyError, IndexError):
                return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": "Неожиданный формат ответа от GigaChat", "metadata": {}}
        else:
            return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": f"GigaChat API ошибка {resp.status_code}: {resp.text[:200]}", "metadata": {}}
    except Exception as e:
        return {"source": "gigachat", "kind": "error", "success": False, "text": "", "error": str(e), "metadata": {}}
