import sys
from unittest.mock import patch
from core.ollama_adapter import query_ollama, is_ollama_running

def test_is_ollama_running_true():
    with patch('core.ollama_adapter.requests.get') as mock_get:
        mock_get.return_value.status_code = 200
        assert is_ollama_running() is True

def test_query_ollama_success():
    with patch('core.ollama_adapter.is_ollama_running', return_value=True), \
         patch('core.ollama_adapter.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"response": "test response"}
        result = query_ollama("test")
        assert result["success"] is True
        assert result["text"] == "test response"

def test_query_ollama_failure():
    with patch('core.ollama_adapter.is_ollama_running', return_value=True), \
         patch('core.ollama_adapter.requests.post') as mock_post:
        mock_post.return_value.status_code = 500
        result = query_ollama("test")
        assert result["success"] is False
