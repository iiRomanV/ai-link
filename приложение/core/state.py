from typing import TypedDict, Optional, Dict, Any, List

class AgentState(TypedDict):
    task: str
    project_dir: str
    research_enabled: bool
    agent_mode: str  # "ollama", "gigachat", "browser_qwen", "browser_deepseek"
    execution_mode: str  # "auto" | "simple" | "agentic"
    research_result: Optional[str]
    plan: Optional[List[Dict[str, Any]]]
    execution_result: Optional[Dict[str, Any]]
    analysis: Optional[Dict[str, Any]]
    attempts: int
    max_attempts: int
    done: bool
    error: Optional[str]
    last_error: Optional[str]
    files_created: List[str]
