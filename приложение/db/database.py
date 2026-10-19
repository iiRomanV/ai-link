import sqlite3
from pathlib import Path
from config.settings import DB_PATH

def init_db():
    """Инициализирует базу данных: создаёт таблицы."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Таблица задач
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            project_dir TEXT NOT NULL,
            result TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Таблица агентов
    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            config TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Таблица паттернов (база знаний)
    c.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            solution TEXT,
            rating INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_task(task: str, project_dir: str, result: str = "", error: str = ""):
    """Сохраняет выполненную задачу в БД."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (task, project_dir, result, error) VALUES (?, ?, ?, ?)",
        (task, project_dir, result, error)
    )
    conn.commit()
    conn.close()

def get_history(limit: int = 10) -> list:
    """Возвращает последние выполненные задачи."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, task, project_dir, result, error, created_at FROM tasks ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def save_pattern(task: str, solution: str, rating: int = 0):
    """Сохраняет паттерн (успешное решение) в базу знаний."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO patterns (task, solution, rating) VALUES (?, ?, ?)",
        (task, solution, rating)
    )
    conn.commit()
    conn.close()

def get_patterns(limit: int = 20) -> list:
    """Возвращает последние паттерны."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, task, solution, rating, created_at FROM patterns ORDER BY created_at DESC LIMIT ?",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def update_agent_in_db(agent_id: str, name: str, agent_type: str, enabled: bool, config: str):
    """Сохраняет/обновляет информацию об агенте в таблице agents."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO agents (id, name, type, enabled, config)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            type=excluded.type,
            enabled=excluded.enabled,
            config=excluded.config,
            updated_at=CURRENT_TIMESTAMP
    """, (agent_id, name, agent_type, 1 if enabled else 0, config))
    conn.commit()
    conn.close()
