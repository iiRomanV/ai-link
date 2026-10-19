"""SECRETS-SCANNER (2026-10-12, Б52-AST): точный поиск секретов.

Для .py — AST-парсинг (без ложных на UI-код).
Для остальных — regex через _SECRET_PATTERNS.

Возвращает: [{"file", "line", "kind"}].
Значения секретов НЕ сохраняются — только факт находки.
"""
import ast
import fnmatch
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# === Sensitive names (части, регистронезависимо) ===
# B52-AST: _key — общий суффикс (stripe_key, aws_key, gcp_key).
# Без underscore «monkey» не попадёт.
_SENSITIVE_PARTS = (
    "api_key", "apikey", "api_secret",
    "_key",           # stripe_key, aws_key, ...
    "token", "secret", "password", "passwd", "pwd",
    "credential",
    # Русские (были раньше в audit_project, вернул 12.10)
    "пароль", "секрет", "логин", "токен", "ключ",
)

# B52-AST: классификация типа секрета (для UI).
_KIND_BY_PREFIX = (
    (("ghp_", "gho_", "ghu_", "ghs_", "github_pat_"), "github_token"),
    (("AKIA", "ASIA"), "aws_access_key"),
    (("xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-"), "slack_token"),
    (("sk_live_", "rk_live_", "sk_test_", "rk_test_"), "stripe_key"),
    (("eyJ",), "jwt"),
    (("AIza",), "google_api_key"),
)


def _classify_kind(name: str, value: str) -> str:
    """Тип секрета: api_key, secret, token, password, github_token..."""
    # По префиксу значения
    for prefixes, kind in _KIND_BY_PREFIX:
        if value.startswith(prefixes):
            return kind
    # По имени
    n = (name or "").lower()
    if "password" in n or "pwd" in n or "passwd" in n:
        return "secret"
    if "token" in n:
        return "api_key"
    if "secret" in n:
        return "secret"
    if "key" in n:
        return "api_key"
    return "generic_secret"


# === Известные префиксы ключей (гарантированно секрет) ===
_KNOWN_PREFIXES = (
    "ghp_", "gho_", "ghu_", "ghs_", "github_pat_",
    "AKIA", "ASIA",
    "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-",
    "sk_live_", "rk_live_", "sk_test_", "rk_test_",
    "eyJ",  # JWT
    "AIza",  # Google API key
)

# === B52-AST-fix5: safe-значения-маркеры (НЕ секреты) ===
_SAFE_VALUES = (
    "access_token", "refresh_token", "id_token", "token_type",
    "expires_at", "expires_in", "expires", "expiry",
    "plaintext_warning", "encrypted_file",
    "password_storage", "password_plain", "password_encrypted",
    "keyring", "plaintext", "encrypted",
)


# === Плейсхолдеры (не секреты) ===
_PLACEHOLDER_WORDS = (
    "example", "sample", "placeholder", "changeme", "change_me",
    "dummy", "your_", "your-", "insert_",
    "fake", "todo", "fixme",
)

# === B52-AST: безопасные _key (не секреты) ===
# Public key — открытый. Foreign/primary — ссылки БД.
# Cache/sort/hash — внутренние. НЕ считать sensitive.
_SAFE_KEY_SUFFIXES = (
    "public_key", "foreign_key", "primary_key",
    "cache_key", "sort_key", "hash_key", "index_key",
)


# === Суффиксы env-имён (не значения) ===
_ENV_SUFFIXES = (
    "_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PWD",
    "_URL", "_ID", "_NAME", "_ENV",
    "_VAL", "_VALUE",
)


def has_sensitive_name(name: str) -> bool:
    """Имя похоже на sensitive (api_key, token, password...).

    B52-AST: safe-list (_public_key, _foreign_key и т.п.) — не sensitive.
    """
    if not name:
        return False
    n = name.lower().replace("-", "_")
    # Safe-list: public_key, foreign_key, primary_key, cache_key и т.д.
    if n.endswith(_SAFE_KEY_SUFFIXES):
        return False
    return any(p in n for p in _SENSITIVE_PARTS)


def is_placeholder(value: str) -> bool:
    v = (value or "").lower()
    return any(p in v for p in _PLACEHOLDER_WORDS)


def is_env_var_name(value: str) -> bool:
    """B53: имя env-переменной (GIGACHAT_KEY), не значение."""
    if not value or len(value) > 50:
        return False
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", value):
        return False
    return value.endswith(_ENV_SUFFIXES)


def has_known_prefix(value: str) -> bool:
    return any(value.startswith(p) for p in _KNOWN_PREFIXES)


def is_secret_value(value: Any) -> bool:
    """Похоже ли значение на настоящий секрет."""
    if not isinstance(value, str):
        return False
    if len(value) < 6:
        return False
    if is_placeholder(value):
        return False
    if is_env_var_name(value):
        return False
    # B52-AST-fix5: safe-значения (access_token, plaintext_warning...).
    if value.lower() in _SAFE_VALUES:
        return False
    # Известный префикс — точно секрет.
    if has_known_prefix(value):
        return True
    # Generic: только буквы/цифры/_- (без спецсимволов/пробелов).
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", value):
        return False
    # Достаточно длинный + есть буквы. Цифры не обязательны
    # (super_secret_value, my_api_key_here — тоже секреты).
    if len(value) < 12:
        return False
    return any(c.isalpha() for c in value)


# ============================================================
# AST-сканер для .py
# ============================================================

def _extract_target_name(node: ast.AST) -> str:
    """Имя присваивания: Name/Attribute/Subscript."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return ""


def _check_value(name: str, value_node: ast.AST,
                 lineno: int, findings: List[Dict[str, Any]],
                 kind_suffix: str = "assign") -> None:
    """Проверяет одно присваивание. kind — тип секрета."""
    if not name or not has_sensitive_name(name):
        return
    # Значение должно быть строковым литералом.
    if not isinstance(value_node, ast.Constant):
        return
    val = value_node.value
    if not isinstance(val, str):
        return
    if not is_secret_value(val):
        return
    findings.append({
        "line": lineno,
        "kind": _classify_kind(name, val),
    })


def scan_py_ast(file_path: Path) -> List[Dict[str, Any]]:
    """AST-сканер .py. Без ложных на UI-код."""
    findings: List[Dict[str, Any]] = []
    try:
        src = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=str(file_path))
    except (SyntaxError, ValueError, Exception) as e:
        logger.debug("AST parse error %s: %s", file_path, e)
        return findings

    for node in ast.walk(tree):
        # a = "value"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = _extract_target_name(target)
                _check_value(name, node.value, node.lineno,
                             findings, "assign")
        # a: str = "value"
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None:
                name = _extract_target_name(node.target)
                _check_value(name, node.value, node.lineno,
                             findings, "annassign")
        # {"api_key": "value"}
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    _check_value(k.value, v, node.lineno,
                                 findings, "dict")

    return findings


# ============================================================
# Regex-сканер для остальных форматов (.env, .sh, .json, ...)
# ============================================================

# Минимальный набор — пересекается с audit_project._SECRET_PATTERNS.
_RE_TEXT_EXT = {
    ".env", ".sh", ".bash", ".zsh", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".json", ".txt",
    ".md", ".html", ".xml", ".desktop",
    }

# B52-AST: файлы без расширения (suffix=''), но с секретами.
_SPECIAL_FILES = {
    ".npmrc", ".pypirc", ".netrc", ".htpasswd",
    ".dockercfg", ".envrc",
}

# Для текстовых форматов: ключ = значение или ключ: "значение".
# B52-AST-fix3: английские ключи с \b (не ловим password_hash),
# русские — БЕЗ \b (compound «РеальныйПароль», «СтарыйКлюч»).
# B52-AST-fix5: без \b (compound-friendly). Для фильтрации
# ложных вида password_hash/password_storage — safe-name список.
_SAFE_NAME_PARTS = (
    "password_hash", "password_storage", "password_plain",
    "password_encrypted", "password_var", "password_entry",
    "password_label", "password_input", "password_field",
    "token_type", "token_url", "token_key", "token_var",
    "token_name", "token_file",
    "api_key_var", "api_key_name", "api_key_env",
    "secret_name", "secret_file",
)

_PAT_KV = re.compile(
    r"(?i)(?:"
    # Английские (без \b — compound-friendly)
    r"(?P<key_en>api[_\-]?key|apikey|secret[_\-]?key|secretkey|"
    r"client[_\-]?secret|api[_\-]?secret|db[_\-]?password|"
    r"database[_\-]?password|auth[_\-]?token|"
    r"token|secret|password|passwd|pwd|pass)"
    r"|"
    # Русские (без границ — compound-friendly)
    r"(?P<key_ru>ключ|токен|пароль|секрет|логин)"
    r")['\"]?"
    r"(?:=(?!\s)(?P<val_env>[A-Za-z0-9][A-Za-z0-9_\-]{5,})"
    r"|\s*[:=]\s*['\"](?P<val_code>[^'\"]{6,})['\"])"
)


def scan_text_kv(file_path: Path) -> List[Dict[str, Any]]:
    """Regex-сканер для .env/.sh/.json и др."""
    findings: List[Dict[str, Any]] = []
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings

    for lineno, line in enumerate(text.splitlines(), 1):
        m = _PAT_KV.search(line)
        if not m:
            continue
        # B52-fix4: именованные группы — не путать ключ и значение.
        val = m.group("val_env") or m.group("val_code") or ""
        if not val:
            continue
        if not is_secret_value(val):
            continue
        key_name = m.group("key_en") or m.group("key_ru") or ""
        # B52-AST-fix5: safe-name (password_hash, token_type, ...).
        _kn = (key_name or "").lower().replace("-", "_")
        if any(p in _kn for p in _SAFE_NAME_PARTS):
            continue
        findings.append({
            "line": lineno,
            "kind": _classify_kind(key_name, val),
        })
    return findings


# ============================================================
# Работа с .gitignore (B52-AST-fix7)
# ============================================================

def _load_gitignore_patterns(project_dir: str) -> List[str]:
    """Читает все .gitignore в проекте (корень + подпапки)."""
    base = Path(project_dir).resolve()
    patterns: List[str] = []
    try:
        for gi in base.rglob(".gitignore"):
            try:
                for line in gi.read_text(
                        encoding="utf-8", errors="ignore"
                ).splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    patterns.append(line)
            except Exception:
                continue
    except Exception:
        pass
    return patterns


def _is_gitignored(rel_path: str, patterns: List[str]) -> bool:
    """Путь попадает под .gitignore?

    B52-AST-fix7: проверка по паттернам:
      • .env        — точное имя в любом месте дерева
      • *.key       — расширение
      • secrets/    — папка
      • /build      — от корня
    """
    if not patterns:
        return False
    rp = rel_path.replace("\\", "/")
    parts = rp.split("/")

    for pat in patterns:
        p = pat.strip()
        if not p:
            continue
        # Отрицание — не обрабатываем (упрощённо).
        if p.startswith("!"):
            continue
        # Ведущий / — от корня.
        if p.startswith("/"):
            p_root = p[1:]
            if fnmatch.fnmatch(rp, p_root):
                return True
            continue
        # Папка (заканчивается на /).
        if p.endswith("/"):
            dir_name = p.rstrip("/")
            for part in parts:
                if fnmatch.fnmatch(part, dir_name):
                    return True
            continue
        # С / в паттерне — матчим полный путь.
        if "/" in p:
            if fnmatch.fnmatch(rp, p):
                return True
            continue
        # Без / — матчим любой компонент пути.
        for part in parts:
            if fnmatch.fnmatch(part, p):
                return True
    return False


# ============================================================
# Главная функция
# ============================================================

_SKIP_PARTS: Set[str] = {
    "__pycache__", ".ai_link", ".git", ".venv", "venv",
    "node_modules", ".pytest_cache", ".mypy_cache",
}


def scan_project(project_dir: str,
                 max_files: int = 500) -> Dict[str, Any]:
    """Сканирует проект. Для .py — AST, для остальных — regex.

    Возвращает:
      {"findings": [{"file", "line", "kind"}],
       "total": N, "checked_files": M}
    """
    base = Path(project_dir).resolve()
    findings: List[Dict[str, Any]] = []
    checked = 0
    # B52-AST-fix7: паттерны .gitignore (один раз).
    _ignored_patterns = _load_gitignore_patterns(project_dir)

    try:
        for p in sorted(base.rglob("*")):
            if checked >= max_files:
                break
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if any(part in _SKIP_PARTS for part in rel.parts):
                continue
            # B52-AST-fix7: проверка .gitignore.
            # Скрытые файлы .env*, .npmrc и т.д. — проверяем.
            # Но если файл под .gitignore → пропускаем (норма).
            _ALLOW_HIDDEN = (
                ".env", ".env.local", ".env.production",
                ".env.development", ".env.example",
                ".npmrc", ".pypirc", ".netrc",
                ".htpasswd", ".dockercfg", ".envrc",
            )
            if any(part.startswith(".") and part not in _ALLOW_HIDDEN
                    for part in rel.parts):
                continue
            # Проверка .gitignore
            rel_str = rel.as_posix()
            if _is_gitignored(rel_str, _ignored_patterns):
                continue

            suffix = p.suffix.lower()
            rel_str = rel.as_posix()

            # .py — AST
            if suffix == ".py":
                sub = scan_py_ast(p)
                for f in sub:
                    findings.append({
                        "file": rel_str,
                        "line": f["line"],
                        "kind": f["kind"],
                    })
                checked += 1
                continue

            # Остальные текстовые — regex.
            # B52-AST: либо по расширению, либо .env*, либо _SPECIAL_FILES.
            _is_text = (
                suffix in _RE_TEXT_EXT
                or p.name.startswith(".env")
                or p.name in _SPECIAL_FILES
            )
            if _is_text:
                sub = scan_text_kv(p)
                for f in sub:
                    findings.append({
                        "file": rel_str,
                        "line": f["line"],
                        "kind": f["kind"],
                    })
                checked += 1
    except Exception as e:
        # DEBUG: показываем traceback, чтобы понять где падает.
        import traceback as _tb
        _tb.print_exc()
        logger.warning("secrets_scanner: %s", e)

    return {
        "findings": findings,
        "total": len(findings),
        "checked_files": checked,
    }
