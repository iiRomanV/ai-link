import subprocess
import json
import requests
from typing import Optional, Dict, Any

OLLAMA_API_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:7b"

def is_ollama_running() -> bool:
    """
    Проверяет, запущен ли сервер Ollama.
    """
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        return response.status_code == 200
    except Exception:
        return False

def query_ollama(task: str, model: str = DEFAULT_MODEL, timeout: int = 300) -> Dict[str, Any]:
    """
    Отправляет задачу в Ollama и возвращает ответ.
    Если Ollama не запущена, пытается запустить её через subprocess (не блокируя).
    """
    if not is_ollama_running():
        # Пытаемся запустить сервер в фоне
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Ждём до 5 секунд
        import time
        for _ in range(10):
            time.sleep(0.5)
            if is_ollama_running():
                break
        else:
            return {"source": "ollama", "kind": "error", "success": False, "text": "", "error": "Ollama сервер не запустился", "metadata": {}}

    system_instruction = (
        "Ты — агент для выполнения задач разработки. "
        "Отвечай ТОЛЬКО JSON-массивом действий без пояснений. "
        "Доступные типы действий: "
        "{'type': 'create_file', 'path': 'относительный путь', 'content': 'содержимое файла'} "
        "или {'type': 'run_command', 'command': 'команда', 'timeout': 30}. "
        "Если задача требует создания файла, верни [{'type': 'create_file', 'path': '...', 'content': '...'}]. "
        "Не добавляй комментарии, не используй markdown-блоки, только JSON."
    )
    payload = {
        "model": model,
        "prompt": task,
        "system": system_instruction,
        "stream": False
    }
    try:
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            text = data.get("response", "")
            return {"source": "ollama", "kind": "model_response", "success": True, "text": text, "error": None, "metadata": {}}
        else:
            return {"source": "ollama", "kind": "error", "success": False, "text": "", "error": f"Ошибка Ollama API: {response.status_code} {response.text}", "metadata": {}}
    except Exception as e:
        return {"source": "ollama", "kind": "error", "success": False, "text": "", "error": str(e), "metadata": {}}

def get_available_models() -> list:
    """
    Возвращает список установленных моделей Ollama.
    """
    if not is_ollama_running():
        return []
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        if response.status_code == 200:
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []

# Простой тест
if __name__ == "__main__":
    print("Ollama running:", is_ollama_running())
    print("Available models:", get_available_models())
