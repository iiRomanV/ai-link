"""
Browser Agent для AI Link.
Теперь использует browser_manager (постоянный браузер).
"""

from core.agents.browser_manager import query_browser_ai, close_browser

__all__ = ["query_browser_ai", "close_browser"]
