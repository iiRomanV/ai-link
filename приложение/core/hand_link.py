import os
import subprocess
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

try:
    from core.post_check import check_file as _post_check_file
    HAS_POST_CHECK = True
except ImportError:
    HAS_POST_CHECK = False

# SECRETS (2026-10-07): маскирование секретов в прочитанном тексте.
# Локально, БЕЗ LLM. Регулярки ищут типовые ключи.
import re as _re_secrets

_SECRET_PATTERNS = [
    # api_key=..., API-KEY = "...", secret: '...'
    _re_secrets.compile(
        r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|"
        r"token|private[_-]?key|auth[_-]?token|access[_-]?key|"
        r"client[_-]?secret|db[_-]?password|redis[_-]?password)"
        r"\s*[:=]\s*['\"]?([A-Za-z0-9\-_.+/=]{8,})['\"]?"
    ),
    _re_secrets.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),          # OpenAI
    _re_secrets.compile(r"\bsk-proj-[A-Za-z0-9_-]{30,}\b"),   # OpenAI проектный
    _re_secrets.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),         # GitHub PAT
    _re_secrets.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"), # GitHub fine
    _re_secrets.compile(r"\bxox[bpoa]-[A-Za-z0-9-]{20,}\b"),  # Slack
    _re_secrets.compile(r"\bAKIA[A-Z0-9]{16}\b"),             # AWS Access Key
    _re_secrets.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"),
    _re_secrets.compile(
        r"-----BEGIN\s+[A-Z ]+PRIVATE KEY-----"
    ),  # PEM-приватный ключ
    # B+ (19.10.2026): короткие пароли (ADMIN_PASSWORD=123).
    # Основной паттерн требует 8+ символов, но "123" — 3.
    _re_secrets.compile(
        r"(?i)\b(admin[_-]?password|root[_-]?password|"
        r"db[_-]?password|default[_-]?password)"
        r"\s*[:=]\s*['\"]?([^\s'\"]{1,30})['\"]?"
    ),
]


def _mask_secrets(text: str):
    """Заменяет найденные секреты на ***HIDDEN***.

    Возвращает (masked_text, hits). Без LLM — только локальные
    регулярки. Работает быстро.
    """
    if not text:
        return text, 0
    hits = 0

    def _repl_named(m):
        nonlocal hits
        hits += 1
        # Группа 1 — имя ключа, группа 2 — значение.
        name = m.group(1)
        return f"{name}=***HIDDEN***"

    def _repl_anon(m):
        nonlocal hits
        hits += 1
        return "***HIDDEN***"

    # Первые N паттернов — с именем ключа, последние — анонимные.
    for i, pat in enumerate(_SECRET_PATTERNS):
        if i == 0:
            text = pat.sub(_repl_named, text)
        else:
            text = pat.sub(_repl_anon, text)
    return text, hits


class HandLink:
    """
    Универсальный исполнитель: создаёт файлы и выполняет безопасные команды.
    """
    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)

    def _verify_file(self, path: Path) -> dict:
        """Проверяет, что файл существует, доступен для чтения, возвращает информацию."""
        import os as _os
        return {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "readable": path.is_file() and _os.access(path, _os.R_OK),
        }

    # SECRETS (2026-10-07): файлы, которые НЕЛЬЗЯ читать.
    # Link не должен вытаскивать секреты в LLM (утечка через промпт).
    # Разрешено только .env.example (там примеры, не настоящие ключи).
    _SECRET_NAMES = {
        ".env", ".env.local", ".env.production", ".env.prod",
        ".env.dev", ".env.development", ".env.staging",
        "secrets.json", "credentials.json",
        "secrets.yaml", "secrets.yml",
        "credentials.yaml", "credentials.yml",
        "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    }
    _SECRET_EXTS = {".key", ".pem", ".pfx", ".p12"}

    def _is_secret_file(self, path: Path) -> bool:
        """True, если файл — секрет (нельзя читать)."""
        name = path.name.lower()
        # Разрешаем .env.example
        if name == ".env.example" or name.endswith(".example"):
            return False
        if name in self._SECRET_NAMES:
            return True
        if path.suffix.lower() in self._SECRET_EXTS:
            return True
        # .env.* (кроме .env.example — уже пропущен выше)
        if name.startswith(".env."):
            return True
        return False

    def _safe_resolve_path(self, rel_path: str) -> Path:
        """Безопасно разрешает путь и проверяет, что он внутри project_dir."""
        base = Path(self.project_dir).resolve()
        target = (base / rel_path).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(f"Путь выходит за пределы проекта: {rel_path}")
        # SECRETS: блокируем чтение .env / secrets.json / *.key.
        if self._is_secret_file(target):
            logger.warning(
                "SECRETS: попытка доступа к секрету заблокирована: %s",
                rel_path,
            )
            raise ValueError(
                f"Файл {rel_path} — секрет (ключи/пароли). "
                f"Не читается и не пишется через Link. "
                f"Работай с .env.example (пример), а настоящие "
                f"ключи пользователь заполнит сам."
            )
        return target

    def execute(self, actions):
        """Выполняет список действий, каждое проверяется на безопасность."""
        created_files = []
        # B55-EXT (2026-10-13): отдельный список для правок
        # существующих файлов (replace_in_file). Семантически
        # это не "создание". Снапшот run_command — ниже.
        changed_files = []
        self._safe_replace_warnings = []
        # EVENT-LOG: запрос на выполнение действий
        try:
            from core.event_log import log_event as _log_ev
            _types = [
                (a.get("type") if isinstance(a, dict) else "?")
                for a in (actions or [])
            ]
            _log_ev(event_type="tool_call_requested", payload={
                "count": len(actions or []),
                "types": _types[:20],
            })
        except Exception:
            pass
        try:
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_type = action.get("type")
                if action_type is None:
                    continue

                if action_type == "create_file":
                    rel_path = action.get("path")
                    file_content = action.get("content", "")
                    if not rel_path:
                        raise ValueError("Не указан путь для create_file")
                    full_path = self._safe_resolve_path(rel_path)
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    # .bak, если файл уже существует
                    bak_path = None
                    if full_path.exists():
                        bak_path = full_path.with_suffix(full_path.suffix + ".bak")
                        try:
                            bak_path.write_text(
                                full_path.read_text(encoding="utf-8"),
                                encoding="utf-8"
                            )
                        except Exception:
                            bak_path = None
                    full_path.write_text(file_content, encoding="utf-8")
                    rel_str = str(full_path.relative_to(Path(self.project_dir).resolve()))
                    created_files.append(rel_str)
                    logger.info("hand: create_file → %s (%d байт)",
                                rel_str, len(file_content))

                    # Пост-проверка
                    if HAS_POST_CHECK:
                        try:
                            pc = _post_check_file(
                                full_path,
                                Path(self.project_dir).resolve(),
                            )
                            # Откат при падении py_compile
                            if (pc.get("py_compile") and
                                    not pc["py_compile"].get("ok") and
                                    bak_path is not None):
                                bak_path.replace(full_path)
                                pc["rolled_back"] = True
                            if not hasattr(self, "_last_post_checks"):
                                self._last_post_checks = {}
                            self._last_post_checks[rel_str] = pc
                        except Exception:
                            pass

                elif action_type == "append_to_file":
                    # FIX 2026-09-26: append_to_file был в
                    # response_validator, но НЕ в hand_link.
                    # LLM зовёт → валидатор пропускает → HandLink
                    # молча игнорирует → агент думает, что записал.
                    rel_path = action.get("path")
                    file_content = action.get("content", "")
                    if not rel_path:
                        raise ValueError(
                            "Не указан путь для append_to_file")
                    full_path = self._safe_resolve_path(rel_path)
                    full_path.parent.mkdir(
                        parents=True, exist_ok=True)
                    # Если файла нет — создаём, иначе дописываем
                    if full_path.exists():
                        with open(full_path, "a",
                                  encoding="utf-8") as f:
                            f.write(file_content)
                    else:
                        full_path.write_text(
                            file_content, encoding="utf-8")
                    rel_str = str(full_path.relative_to(
                        Path(self.project_dir).resolve()))
                    if rel_str not in created_files:
                        created_files.append(rel_str)
                    logger.info(
                        "hand: append_to_file → %s (+%d байт)",
                        rel_str, len(file_content))

                elif action_type == "move_file":
                    # FIX 2026-09-26: переименование/перемещение
                    import shutil as _sh
                    src_path = action.get("source") or action.get("path")
                    dst_path = action.get("destination") or action.get("to")
                    if not src_path or not dst_path:
                        raise ValueError(
                            "move_file: нужны source и destination")
                    src_full = self._safe_resolve_path(src_path)
                    dst_full = self._safe_resolve_path(dst_path)
                    if not src_full.exists():
                        raise ValueError(
                            f"move_file: исходный файл не найден: "
                            f"{src_path}")
                    dst_full.parent.mkdir(parents=True, exist_ok=True)
                    _sh.move(str(src_full), str(dst_full))
                    rel_str = str(dst_full.relative_to(
                        Path(self.project_dir).resolve()))
                    created_files.append(rel_str)
                    logger.info(
                        "hand: move_file %s → %s",
                        src_path, rel_str)

                elif action_type == "copy_file":
                    # FIX 2026-09-26: копирование
                    import shutil as _sh
                    src_path = action.get("source") or action.get("path")
                    dst_path = action.get("destination") or action.get("to")
                    if not src_path or not dst_path:
                        raise ValueError(
                            "copy_file: нужны source и destination")
                    src_full = self._safe_resolve_path(src_path)
                    dst_full = self._safe_resolve_path(dst_path)
                    if not src_full.exists():
                        raise ValueError(
                            f"copy_file: исходный файл не найден: "
                            f"{src_path}")
                    dst_full.parent.mkdir(parents=True, exist_ok=True)
                    _sh.copy2(str(src_full), str(dst_full))
                    rel_str = str(dst_full.relative_to(
                        Path(self.project_dir).resolve()))
                    created_files.append(rel_str)
                    logger.info(
                        "hand: copy_file %s → %s",
                        src_path, rel_str)

                elif action_type == "file_metadata":
                    # FIX 2026-09-26: размер, дата, строки
                    rel_path = action.get("path")
                    if not rel_path:
                        raise ValueError(
                            "file_metadata: не указан путь")
                    full_path = self._safe_resolve_path(rel_path)
                    if not hasattr(self, "_last_metadata"):
                        self._last_metadata = {}
                    if full_path.exists() and full_path.is_file():
                        try:
                            _st = full_path.stat()
                            _lines = 0
                            try:
                                _lines = sum(
                                    1 for _ in open(
                                        full_path,
                                        encoding="utf-8",
                                        errors="ignore")
                                )
                            except Exception:
                                pass
                            self._last_metadata[rel_path] = {
                                "size": _st.st_size,
                                "mtime": int(_st.st_mtime),
                                "lines": _lines,
                                "is_file": True,
                            }
                        except Exception as e:
                            self._last_metadata[rel_path] = {
                                "error": str(e)[:120]}
                    elif full_path.exists() and full_path.is_dir():
                        try:
                            _n = sum(1 for _ in full_path.iterdir())
                            self._last_metadata[rel_path] = {
                                "is_dir": True,
                                "items": _n,
                            }
                        except Exception:
                            pass
                    else:
                        self._last_metadata[rel_path] = {
                            "error": "не найдено"}

                elif action_type == "read_file":
                    rel_path = action.get("path")
                    if not rel_path:
                        raise ValueError("Не указан путь для read_file")
                    full_path = self._safe_resolve_path(rel_path)
                    if not full_path.exists():
                        raise ValueError(f"Файл не найден: {rel_path}")
                    # HAND-LESSONS (2026-10-02): если путь — папка,
                    # read_file не работает (Errno 21). Явно сообщаем.
                    if full_path.is_dir():
                        raise ValueError(
                            f"Это папка, не файл: {rel_path}. "
                            f"Используй list_directory для папок."
                        )
                    # Читаем и сохраняем во временный словарь (вернём через read_results)
                    if not hasattr(self, "_last_reads"):
                        self._last_reads = {}
                    content = full_path.read_text(encoding="utf-8")
                    # SECRETS (2026-10-07): маскируем ключи в тексте.
                    # Даже если файл назван «нестандартно» — паттерны
                    # найдут и скроют ключи. LLM структуру видит,
                    # значения — нет.
                    _masked, _hits = _mask_secrets(content)
                    if _hits:
                        logger.warning(
                            "SECRETS: замаскировано %d секретов в %s",
                            _hits, rel_path,
                        )
                        content = _masked

                    # W3: offset/limit для больших файлов.
                    # offset — с какой строки начать (0-based).
                    # limit — сколько строк (0 = до конца).
                    try:
                        _offset = int(action.get("offset", 0) or 0)
                        _limit = int(action.get("limit", 0) or 0)
                    except (ValueError, TypeError):
                        _offset = 0
                        _limit = 0

                    if _offset > 0 or _limit > 0:
                        _lines = content.splitlines(keepends=True)
                        _total = len(_lines)
                        if _offset > 0:
                            _lines = _lines[_offset:]
                        if _limit > 0:
                            _lines = _lines[:_limit]
                        content = "".join(_lines)
                        logger.info(
                            "hand: read_file → %s (строки %d-%d из %d, %d символов)",
                            rel_path, _offset,
                            _offset + len(_lines),
                            _total, len(content),
                        )
                    else:
                        self._last_reads[rel_path] = content
                        logger.info("hand: read_file → %s (%d символов)",
                                    rel_path, len(content))
                        continue  # уже записали — пропускаем финальную запись
                    self._last_reads[rel_path] = content

                elif action_type == "replace_in_file":
                    rel_path = action.get("path")
                    find_text = action.get("find", "")
                    replace_text = action.get("replace", "")
                    if not rel_path:
                        raise ValueError("Не указан путь для replace_in_file")
                    if not find_text:
                        raise ValueError("Параметр 'find' пустой")
                    full_path = self._safe_resolve_path(rel_path)
                    if not full_path.exists():
                        raise ValueError(f"Файл не найден: {rel_path}")
                    # HAND-LESSONS (2026-10-02): если путь — папка,
                    # replace_in_file не работает (Errno 21).
                    if full_path.is_dir():
                        raise ValueError(
                            f"Это папка, не файл: {rel_path}. "
                            f"replace_in_file работает только с файлами."
                        )
                    original = full_path.read_text(encoding="utf-8")
                    if find_text not in original:
                        raise ValueError(f"Фрагмент не найден в {rel_path}")
                    # .bak
                    bak_path = full_path.with_suffix(full_path.suffix + ".bak")
                    try:
                        bak_path.write_text(original, encoding="utf-8")
                    except Exception:
                        bak_path = None
                    updated = original.replace(find_text, replace_text, 1)

                    # FIX SAFE-REPLACE (2026-09-28): перед записью
                    # проверяем, не удаляем ли def/class, которую
                    # кто-то вызывает. Предупреждение в лог,
                    # инфо в _last_post_checks для review.
                    _broken_refs = []
                    try:
                        import ast as _ast
                        from core.project_indexer import (
                            find_symbol_usages as _fsu
                        )
                        _defs_before = set()
                        _defs_after = set()
                        try:
                            _t_b = _ast.parse(original)
                            for _n in _ast.walk(_t_b):
                                if isinstance(_n, (
                                        _ast.FunctionDef,
                                        _ast.AsyncFunctionDef,
                                        _ast.ClassDef)):
                                    _defs_before.add(_n.name)
                        except Exception:
                            pass
                        try:
                            _t_a = _ast.parse(updated)
                            for _n in _ast.walk(_t_a):
                                if isinstance(_n, (
                                        _ast.FunctionDef,
                                        _ast.AsyncFunctionDef,
                                        _ast.ClassDef)):
                                    _defs_after.add(_n.name)
                        except Exception:
                            pass
                        _removed = _defs_before - _defs_after
                        if _removed:
                            _proj = Path(self.project_dir).resolve()
                            _self_rel = str(
                                full_path.relative_to(_proj)
                            ).replace("\\", "/")
                            for _sym in sorted(_removed):
                                _us = _fsu(self.project_dir, _sym,
                                           skip_tests=False)
                                _ext = [u for u in _us
                                        if u["file"] != _self_rel]
                                if _ext:
                                    _broken_refs.append({
                                        "symbol": _sym,
                                        "usages": _ext[:10],
                                    })
                            if _broken_refs:
                                logger.warning(
                                    "SAFE-REPLACE: в %s удаляются "
                                    "def/class, которые вызываются "
                                    "в других файлах: %s",
                                    rel_path,
                                    ", ".join(
                                        f"{b['symbol']} "
                                        f"({len(b['usages'])})"
                                        for b in _broken_refs
                                    ),
                                )
                                self._safe_replace_warnings.append({
                                    "file": rel_path,
                                    "broken_refs": _broken_refs,
                                })
                    except Exception as _e:
                        logger.debug("SAFE-REPLACE: ошибка %s", _e)

                    full_path.write_text(updated, encoding="utf-8")
                    rel_str = str(full_path.relative_to(Path(self.project_dir).resolve()))
                    # B55-EXT: это ПРАВКА существующего файла,
                    # не создание. Раньше ошибочно шло в created.
                    changed_files.append(rel_str)
                    logger.info("hand: replace_in_file → %s "
                                "(find=%d, replace=%d символов)",
                                rel_str, len(find_text), len(replace_text))

                    # Пост-проверка
                    if HAS_POST_CHECK:
                        try:
                            pc = _post_check_file(
                                full_path,
                                Path(self.project_dir).resolve(),
                            )
                            if (pc.get("py_compile") and
                                    not pc["py_compile"].get("ok") and
                                    bak_path is not None):
                                bak_path.replace(full_path)
                                pc["rolled_back"] = True
                            if not hasattr(self, "_last_post_checks"):
                                self._last_post_checks = {}
                            self._last_post_checks[rel_str] = pc
                        except Exception:
                            pass

                elif action_type == "list_directory":
                    rel_path = action.get("path", ".")
                    full_path = self._safe_resolve_path(rel_path)
                    if not full_path.exists():
                        raise ValueError(f"Папка не найдена: {rel_path}")
                    if not full_path.is_dir():
                        raise ValueError(f"Не папка: {rel_path}")
                    if not hasattr(self, "_last_lists"):
                        self._last_lists = {}
                    items = []
                    for child in sorted(full_path.iterdir()):
                        items.append(child.name + ("/" if child.is_dir() else ""))
                    self._last_lists[rel_path] = items

                elif action_type == "show_info":
                    msg = action.get("message", "")
                    print(f"[SHOW_INFO] {msg}")

                elif action_type == "run_command":
                    cmd = action.get("command")
                    if not cmd:
                        raise ValueError("Не указана команда для run_command")
                    timeout = action.get("timeout", 60)
                    self._run_command(cmd, timeout)
                else:
                    print(f"Пропущено действие с неизвестным типом: {action_type}")
                    continue
        except Exception as e:
            logger.warning("hand: execute FAILED — %s", str(e)[:200])
            # EVENT-LOG: результат (ошибка)
            try:
                from core.event_log import log_event as _log_ev
                _log_ev(event_type="tool_result_received", payload={
                    "success": False,
                    "created_count": len(created_files),
                    "error": str(e)[:200],
                })
            except Exception:
                pass
            return {"success": False,
                    "created_files": created_files,
                    "changed_files": changed_files,
                    "error": str(e)}

        # Собираем verified_files
        verified_files = []
        for f in created_files:
            full_path = Path(self.project_dir).resolve() / f
            verified_files.append(self._verify_file(full_path))

        # B55-EXT: собираем данные снапшотов run_command
        _created_by_cmd = []
        _changed_by_cmd = []
        for _c in (getattr(self, "_last_commands", {}) or {}).values():
            _created_by_cmd.extend(_c.get("created_by_command", []) or [])
            _changed_by_cmd.extend(_c.get("changed_by_command", []) or [])

        result = {
            "success": True,
            "created_files": created_files,
            "changed_files": changed_files,
            "created_by_command": _created_by_cmd,
            "changed_by_command": _changed_by_cmd,
            "verified_files": verified_files,
            "error": None,
        }
        if hasattr(self, "_last_reads") and self._last_reads:
            result["read_results"] = self._last_reads
            self._last_reads = {}
        if hasattr(self, "_last_lists") and self._last_lists:
            result["list_results"] = self._last_lists
            self._last_lists = {}
        if hasattr(self, "_last_post_checks") and self._last_post_checks:
            result["post_checks"] = self._last_post_checks
            self._last_post_checks = {}
        if getattr(self, "_safe_replace_warnings", None):
            result["safe_replace_warnings"] = self._safe_replace_warnings
            self._safe_replace_warnings = []
        if hasattr(self, "_last_commands") and self._last_commands:
            result["command_results"] = self._last_commands
            self._last_commands = {}
        if hasattr(self, "_last_metadata") and self._last_metadata:
            result["metadata_results"] = self._last_metadata
            self._last_metadata = {}
        # EVENT-LOG: результат (успех)
        try:
            from core.event_log import log_event as _log_ev
            _log_ev(event_type="tool_result_received", payload={
                "success": True,
                "created_count": len(created_files),
                "created_files": created_files[:20],
            })
        except Exception:
            pass
        return result

    # B55-EXT (2026-10-13): снапшот текстовых файлов для
    # определения изменений от run_command.
    _SNAPSHOT_EXTS = {
        ".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml",
        ".cfg", ".ini", ".sh", ".env", ".example",
    }
    _SNAPSHOT_SKIP_DIRS = {
        ".venv", "venv", "env", "__pycache__", ".git",
        "node_modules", ".pytest_cache", ".ai_link",
        ".diagnostics", ".mypy_cache", ".ruff_cache",
    }
    _SNAPSHOT_MAX_SIZE = 1_000_000   # 1 МБ
    _SNAPSHOT_MAX_FILES = 1000       # защита от гигантских проектов

    def _snapshot_text_files(self) -> dict:
        """B55-EXT: {rel_path: sha256} по текстовым файлам.

        Пропускаем служебные папки (.venv, __pycache__, .git и др.),
        бинарные расширения, файлы больше 1 МБ.
        Если файлов > 1000 — возвращаем пустой dict (пропуск).
        """
        import hashlib
        root = Path(self.project_dir).resolve()
        snap: dict = {}
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    rel_parts = p.relative_to(root).parts
                except ValueError:
                    continue
                if any(part in self._SNAPSHOT_SKIP_DIRS
                       for part in rel_parts):
                    continue
                if p.suffix.lower() not in self._SNAPSHOT_EXTS:
                    continue
                try:
                    if p.stat().st_size > self._SNAPSHOT_MAX_SIZE:
                        continue
                    rel_str = str(p.relative_to(root))
                    snap[rel_str] = hashlib.sha256(
                        p.read_bytes()
                    ).hexdigest()
                except Exception:
                    continue
                if len(snap) > self._SNAPSHOT_MAX_FILES:
                    return {}
        except Exception:
            return {}
        return snap

    def _run_command(self, command: str, timeout: int):
        forbidden = ["rm -rf", "sudo", "mkfs", "shutdown", "reboot"]

        # SECRETS (2026-10-07): не даём читать секреты через shell.
        # Ловим типовые способы: cat .env, grep ... .env, head .env,
        # less .env, source .env, export $(cat .env).
        import re as _re
        # Б68 (14.10.2026): не блокируем безопасные суффиксы
        # .env.example / .env.template / .env.sample / .env.dist.
        # Они созданы специально для документации и без секретов.
        _secret_pat = _re.compile(
            r"(?:\bcat\b|\bgrep\b|\bhead\b|\btail\b|\bless\b|"
            r"\bmore\b|\bsource\b|\bexport\b|\.\s)"
            r"[^|;&]*?"
            r"(?:"
            r"\.env(?!(?:\.(?:example|template|sample|dist|bak|backup))\b)"
            r"(?:\.\w+)?\b"
            r"|secrets\.(?:json|ya?ml)\b"
            r"|credentials\.(?:json|ya?ml)\b"
            r"|id_(?:rsa|ed25519|dsa|ecdsa)\b"
            r")"
        )
        if _secret_pat.search(command):
            logger.warning(
                "SECRETS: run_command блокирует чтение секрета: %s",
                command[:120],
            )
            raise ValueError(
                "Команда пытается прочитать файл с секретами "
                "(.env / secrets.json / credentials.*). "
                "Работай с .env.example."
            )
        for pattern in forbidden:
            if pattern in command.lower():
                logger.warning("hand: run_command заблокирован "
                               "(паттерн=%s): %s",
                               pattern, command[:80])
                raise ValueError(f"Команда содержит запрещённый паттерн: {pattern}")

        # FIX: алиасы python → python3, pip → pip3 (Linux Mint).
        # Модели часто пишут "python -m pytest" по привычке.
        _stripped = command.lstrip()
        _lead = command[:len(command) - len(_stripped)]
        for _alias, _real in (("python", "python3"), ("pip", "pip3")):
            # "python" в начале команды ИЛИ после ; / && / |
            if _stripped == _alias or _stripped.startswith(_alias + " "):
                _stripped = _real + _stripped[len(_alias):]
                break
            for _sep in ("; ", "&& ", "| "):
                _pat = _sep + _alias + " "
                if _pat in _stripped:
                    _stripped = _stripped.replace(
                        _pat, _sep + _real + " ", 1)
                    break
        command = _lead + _stripped

        logger.info("hand: run_command → %s", command[:100])
        # B55-EXT: снапшот ДО. Позволяет понять, что команда
        # реально изменила (а не то, что LLM пообещала).
        _snap_before = self._snapshot_text_files()
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        _snap_after = self._snapshot_text_files()
        _created_by_cmd = [
            k for k in _snap_after if k not in _snap_before
        ]
        _changed_by_cmd = [
            k for k in _snap_after
            if k in _snap_before
            and _snap_after[k] != _snap_before[k]
        ]
        # FIX 2026-09-25: не падаем при rc!=0 — сохраняем результат
        # в _last_commands для следующего execute. Агент сам решит.
        if not hasattr(self, "_last_commands"):
            self._last_commands = {}
        self._last_commands[command[:200]] = {
            "rc": result.returncode,
            "stdout": (result.stdout or "")[-2000:],
            "stderr": (result.stderr or "")[-1000:],
            "created_by_command": _created_by_cmd[:50],
            "changed_by_command": _changed_by_cmd[:50],
        }
        if result.returncode != 0:
            logger.warning("hand: run_command rc=%d (сохранён): %s",
                           result.returncode,
                           (result.stderr or result.stdout)[:200])
        else:
            logger.info("hand: run_command OK (rc=0)")

def parse_model_response(response_text: str) -> List[Dict[str, Any]]:
    def _normalize_action(action):
        if not isinstance(action, dict):
            return action
        normalized = action.copy()
        if "type" not in normalized and "action" in normalized:
            normalized["type"] = normalized["action"]
        if "path" not in normalized and "file_name" in normalized:
            normalized["path"] = normalized["file_name"]
        return normalized

    try:
        data = json.loads(response_text)
        if isinstance(data, list):
            return [_normalize_action(a) for a in data]
        if isinstance(data, dict) and "actions" in data:
            return [_normalize_action(a) for a in data["actions"]]
    except json.JSONDecodeError:
        pass

    json_match = re.search(r'```json\s*(\[.*?\]|\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, list):
                return [_normalize_action(a) for a in data]
            if isinstance(data, dict) and "actions" in data:
                return [_normalize_action(a) for a in data["actions"]]
        except json.JSONDecodeError:
            pass

    code_match = re.search(r'```(?:python|py)?\s*\n(.*?)```', response_text, re.DOTALL)
    if code_match:
        return [{"type": "create_file", "path": "main.py", "content": code_match.group(1)}]

    return []

def safe_parse_model_response(response_text: str) -> list:
    if not response_text or not response_text.strip():
        return []
    text = response_text.strip()
    actions = parse_model_response(text)
    if actions:
        valid = [a for a in actions if isinstance(a, dict) and a.get("type") in ("create_file", "run_command")]
        if valid:
            return valid
    import re
    stripped = text.strip()
    # Если это пустой JSON-массив — не создаём файл, возвращаем пустой список
    if stripped in ("[]", "[ ]", ""):
        return []
    # Если это JSON-объект без действий — тоже ничего не создаём
    if stripped.startswith("{") or stripped.startswith("["):
        # Похоже на JSON, но не распознан — не считаем это текстом
        return []
    if re.search(r'\b(def|class|import|print)\b', stripped):
        return [{"type": "create_file", "path": "main.py", "content": stripped}]
    return [{"type": "create_file", "path": "result.txt", "content": stripped}]

def test_hand_link():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        hl = HandLink(tmpdir)
        actions = [{"type": "create_file", "path": "hello.py", "content": "print('Hello')"}]
        result = hl.execute(actions)
        assert result["success"] is True
        assert (Path(tmpdir) / "hello.py").exists()
        print("HandLink test passed")

if __name__ == "__main__":
    test_hand_link()
