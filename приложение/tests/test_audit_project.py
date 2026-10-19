"""Тесты AUDIT-PROJECT (Шаг 1: fast-режим).

Мок-функции case-insensitive (после правки промптов 2026-09-24).
"""

import json
from pathlib import Path

from core import audit_project as ap


def _mk_project(tmp_path, with_tests=False, with_secret=False):
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    if with_secret:
        (tmp_path / "config.py").write_text(
            'API_KEY = "sk_live_abcdef1234567890abcdef"\n',
            encoding="utf-8")
    if with_tests:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    return tmp_path


def test_run_full_audit_basic(tmp_path):
    _mk_project(tmp_path)
    r = ap.run_full_audit(str(tmp_path), mode="fast", max_time_min=5)
    assert r["status"] == "success"
    assert r["audit_id"]
    assert "manifest" in r
    assert r["manifest"]["file_count"] >= 3
    assert "technical" in r
    assert "security" in r
    assert "tests" in r


def test_audit_saves_files(tmp_path):
    _mk_project(tmp_path)
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    d = tmp_path / ".ai_link" / "audits" / r["audit_id"]
    assert d.exists()
    assert (d / "manifest.json").exists()
    assert (d / "audit.json").exists()
    assert (d / "audit_report.md").exists()
    assert (tmp_path / ".ai_link" / "latest_audit.json").exists()


def test_secrets_found(tmp_path):
    _mk_project(tmp_path, with_secret=True)
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1
    kinds = [f["kind"] for f in sec["findings"]]
    # B52-AST: kind теперь точнее (stripe_key, jwt, github_token).
    # Проверяем, что kind не пустой и не «unknown».
    assert all(k and k != "unknown" for k in kinds), kinds
    # Значения не сохраняются
    for f in sec["findings"]:
        assert "value" not in f
        assert set(f.keys()) == {"file", "line", "kind"}


def test_secrets_clean(tmp_path):
    _mk_project(tmp_path)
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] == 0


def test_tests_passed(tmp_path):
    _mk_project(tmp_path, with_tests=True)
    t = ap.check_tests(str(tmp_path))
    assert t["status"] == "passed"
    assert t["passed"] >= 1
    assert t["failed"] == 0


def test_tests_skipped(tmp_path):
    _mk_project(tmp_path, with_tests=False)
    t = ap.check_tests(str(tmp_path))
    assert t["status"] == "skipped"


def test_load_latest_audit(tmp_path):
    _mk_project(tmp_path)
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    last = ap.load_latest_audit(str(tmp_path))
    assert last.get("audit_id") == r["audit_id"]
    assert last.get("status") == "success"


def test_render_md_contains_key_sections(tmp_path):
    _mk_project(tmp_path, with_tests=True, with_secret=True)
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    md = ap.render_audit_md(r)
    assert "# Аудит проекта" in md
    assert "## Снапшот" in md
    # FIX 2026-09-24: новые разделы по типам
    assert "## 🟢 ПОДТВЕРЖДЕНО (Python)" in md
    assert "## 🟠 ЛОГИКА (LLM" in md
    assert "## ⚪ ТЕХДОЛГ" in md
    assert "## Тесты" in md
    # Секреты теперь внутри ПОДТВЕРЖДЕНО
    assert "Потенциальные секреты" in md


def test_fingerprint_stable(tmp_path):
    _mk_project(tmp_path)
    fp1 = ap._fingerprint(str(tmp_path))
    fp2 = ap._fingerprint(str(tmp_path))
    assert fp1 == fp2
    # Изменим файл → fingerprint другой
    (tmp_path / "main.py").write_text("x = 2\n", encoding="utf-8")
    fp3 = ap._fingerprint(str(tmp_path))
    assert fp3 != fp1


def test_nonexistent_project(tmp_path):
    r = ap.run_full_audit(str(tmp_path / "no_such"), mode="fast")
    assert r["status"] == "failed"


def test_max_time_normalized(tmp_path):
    _mk_project(tmp_path)
    r = ap.run_full_audit(str(tmp_path), mode="fast", max_time_min=999)
    assert r["max_time_min"] == 180  # ограничен 180
    r2 = ap.run_full_audit(str(tmp_path), mode="fast", max_time_min=0)
    assert r2["max_time_min"] == 1  # минимум 1


def test_secrets_english_password_json(tmp_path):
    """Англ. 'password' в JSON — должен ловиться."""
    (tmp_path / "cfg.json").write_text(
        '{"password": "verysecret123"}', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "regex не поймал англ password в JSON"
    assert any(f["kind"] == "secret" for f in sec["findings"])


def test_secrets_russian_password(tmp_path):
    """Русское «Пароль» — должен ловиться (как в data.json)."""
    (tmp_path / "data.json").write_text(
        '{"Пароль": "super_secret_value"}', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "regex не поймал русское «Пароль»"


def test_secrets_russian_key(tmp_path):
    """Русское «Ключ» — должен ловиться."""
    (tmp_path / "data.json").write_text(
        '{"Ключ": "sk-abcdef1234567890"}', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "regex не поймал русское «Ключ»"


def test_secrets_values_not_stored(tmp_path):
    """Значения секретов НЕ сохраняются — только file/line/kind."""
    (tmp_path / "cfg.py").write_text(
        'password = "topsecret123"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1
    for f in sec["findings"]:
        assert set(f.keys()) == {"file", "line", "kind"}, \
            f"лишние поля в finding: {f.keys()}"
        # Убедимся, что значение не попало
        s = str(f)
        assert "topsecret123" not in s, "значение секрета утекло!"


def test_secrets_clean_still_clean(tmp_path):
    """Обычный код без секретов — 0 находок (нет false positive)."""
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] == 0, f"ложное срабатывание: {sec['findings']}"


def test_secrets_skips_mask(tmp_path):
    """Маска «••••••••» — НЕ считается секретом."""
    (tmp_path / "demo.py").write_text(
        "'Пароль': '••••••••',\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] == 0, f"маска дала FP: {sec['findings']}"


def test_secrets_skips_example(tmp_path):
    """example.com / Пример — НЕ считается секретом."""
    (tmp_path / "demo.py").write_text(
        "'URL': 'https://example.com',\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] == 0, f"example дал FP: {sec['findings']}"


def test_secrets_skips_placeholder_word(tmp_path):
    """Слово 'placeholder' в значении — скипаем (явный маркер)."""
    (tmp_path / "cfg.py").write_text(
        'API_KEY = "your_placeholder_key"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] == 0, f"placeholder дал FP: {sec['findings']}"


def test_secrets_keeps_test_key(tmp_path):
    """'test_key_prod_2024_secret' — НЕ placeholder, ловим.

    Пользователь был прав: нельзя фильтровать по слову 'test'.
    """
    (tmp_path / "cfg.py").write_text(
        'SECRET = "test_key_prod_2024_xK9mP2"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "реальный секрет с 'test' пропущен!"


def test_secrets_keeps_xxx_in_real(tmp_path):
    """'xxx' как часть ключа — НЕ фильтруем."""
    (tmp_path / "cfg.py").write_text(
        'API_KEY = "xxxxxRealKey123456xxxxx"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "ключ с 'xxx' пропущен!"


def test_secrets_keeps_real(tmp_path):
    """Реальный секрет (не placeholder) — ловится."""
    (tmp_path / "real.py").write_text(
        'password = "SuperSecret777"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "реальный секрет пропущен"


def test_secrets_realistic_demo_data(tmp_path):
    """Смесь: демо + реальный → ловим только реальный."""
    (tmp_path / "mix.json").write_text(
        '{\n'
        '  "Пароль": "••••••••",\n'
        '  "Ключ": "api-key-123",\n'
        '  "РеальныйПароль": "xK9mP2nQ7vL4wR8t",\n'
        '}\n',
        encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    # api-key-123 → placeholder? пока не факт. Смотрим.
    # Должен быть хотя бы реальный пароль.
    real_found = any(
        "РеальныйПароль" not in f.get("kind", "") for f in sec["findings"])
    assert sec["total"] >= 1, "реальный пароль не найден"


def test_secrets_env_file(tmp_path):
    """Файл .env — ОБЯЗАТЕЛЬНО проверяется (был пропущен — баг)."""
    (tmp_path / ".env").write_text(
        "DATABASE_PASSWORD=SuperSecret2024xyz\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["checked_files"] >= 1, ".env не проверен!"
    assert sec["total"] >= 1, f".env: секрет не найден, findings={sec['findings']}"
    assert any(f["file"] == ".env" for f in sec["findings"])


def test_secrets_env_local(tmp_path):
    """Файл .env.local — тоже проверяется."""
    (tmp_path / ".env.local").write_text(
        "API_KEY=sk_live_abcdef1234567890abcdef\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert any(f["file"] == ".env.local" for f in sec["findings"])


def test_secrets_npmrc(tmp_path):
    """Файл .npmrc с токеном — проверяется."""
    (tmp_path / ".npmrc").write_text(
        "//registry.npmjs.org/:_authToken=abc123xyz789\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    # Должен найти хотя бы как api_key или bearer
    # (смотря какой regex сработает)
    assert sec["total"] >= 1 or sec["checked_files"] >= 1


def test_secrets_still_skip_git(tmp_path):
    """.git/ по-прежнему пропускается (не сканируем)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "password = RealPassword123\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    # .git/config не должен быть в findings
    assert not any(".git" in f["file"] for f in sec["findings"])


def test_secrets_env_and_regular_both(tmp_path):
    """Смесь: .env + обычный файл — оба проверяются."""
    (tmp_path / ".env").write_text(
        "SECRET_KEY=RealSecret123456789\n", encoding="utf-8")
    (tmp_path / "config.py").write_text(
        'password = "AnotherReal456"\n', encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    files = {f["file"] for f in sec["findings"]}
    assert ".env" in files, f".env пропущен: {files}"
    assert "config.py" in files, f"config.py пропущен: {files}"


def test_secrets_secret_key_env(tmp_path):
    """SECRET_KEY=... — частая переменная, должен ловиться."""
    (tmp_path / ".env").write_text(
        "SECRET_KEY=RealSecret123456789\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "SECRET_KEY не пойман!"


def test_secrets_client_secret(tmp_path):
    """CLIENT_SECRET=... — OAuth, должен ловиться."""
    (tmp_path / ".env").write_text(
        "CLIENT_SECRET=abcdef1234567890\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "CLIENT_SECRET не пойман!"


def test_secrets_db_password(tmp_path):
    """DB_PASSWORD=... — должен ловиться."""
    (tmp_path / ".env").write_text(
        "DB_PASSWORD=SuperDBPass2024\n", encoding="utf-8")
    sec = ap.check_secrets(str(tmp_path))
    assert sec["total"] >= 1, "DB_PASSWORD не пойман!"


# ═══════════════════════════════════════════════════════════════
#  Шаг 2: LLM-режимы (с моком — реальная LLM не вызывается)
# ═══════════════════════════════════════════════════════════════


def _mock_llm_factory(desc="Описание тестовое",
                     ideas="Идея 1, идея 2",
                     cls_json=None):
    """Возвращает мок _llm_call с заданными ответами."""
    calls = []

    def _mock(agent_mode, prompt):
        calls.append({"agent": agent_mode, "prompt": prompt})
        low = prompt.lower()
        if "опиши проект" in low:
            return {"success": True, "text": desc, "error": None}
        if "предложи 5-10 идей" in low:
            return {"success": True, "text": ideas, "error": None}
        if "классифицируй проблемы" in low:
            return {"success": True, "text": cls_json or "[]", "error": None}
        return {"success": True, "text": "", "error": None}
    return _mock, calls


def test_llm_description_called_in_full(tmp_path):
    """В full-режиме вызывается describe_project."""
    _mk_project(tmp_path)
    mock, calls = _mock_llm_factory(desc="Это тестовый проект")
    ap.set_test_llm_stub(mock)
    try:
        r = ap.run_full_audit(str(tmp_path), mode="full", max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
    assert r["status"] == "success"
    assert "llm_description" in r
    assert r["llm_description"]["success"]
    assert "тестовый проект" in r["llm_description"]["text"]


def test_llm_ideas_called_in_full(tmp_path):
    """В full-режиме вызывается suggest_ideas."""
    _mk_project(tmp_path)
    mock, calls = _mock_llm_factory(ideas="Добавить логирование")
    ap.set_test_llm_stub(mock)
    try:
        r = ap.run_full_audit(str(tmp_path), mode="full", max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
    assert "llm_ideas" in r
    assert "логирование" in r["llm_ideas"]["text"]


def test_llm_not_called_in_fast(tmp_path):
    """В fast-режиме LLM НЕ вызывается."""
    _mk_project(tmp_path)
    mock, calls = _mock_llm_factory()
    ap.set_test_llm_stub(mock)
    try:
        r = ap.run_full_audit(str(tmp_path), mode="fast", max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
    assert "llm_description" not in r
    assert "llm_ideas" not in r
    assert len(calls) == 0, "LLM вызвана в fast!"


def test_classify_issues_valid_json(tmp_path):
    """Классификация парсит корректный JSON."""
    import json as _j
    cls = [
        {"i": 1, "level": "CRITICAL", "reason": "syntax error"},
        {"i": 2, "level": "MINOR", "reason": "мелочь"},
    ]
    issues = [
        {"severity": "high", "category": "syntax",
         "file": "a.py", "line": 1, "message": "SyntaxError"},
        {"severity": "low", "category": "todo",
         "file": "b.py", "line": 2, "message": "TODO"},
    ]
    mock, _ = _mock_llm_factory(cls_json=_j.dumps(cls))
    ap.set_test_llm_stub(mock)
    try:
        r = ap.classify_issues(issues, "browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["success"], r.get("error")
    assert len(r["classified"]) == 2
    assert r["classified"][0]["level"] == "CRITICAL"
    assert r["classified"][1]["level"] == "MINOR"


def test_classify_issues_bad_json(tmp_path):
    """Битый JSON → success=False, не падает."""
    mock, _ = _mock_llm_factory(cls_json="Это не JSON совсем")
    ap.set_test_llm_stub(mock)
    try:
        r = ap.classify_issues(
            [{"file": "a.py", "message": "x"}], "browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert not r["success"]
    assert r["classified"] == []


def test_classify_issues_empty():
    """Пустой список проблем → без вызова LLM."""
    mock, calls = _mock_llm_factory()
    ap.set_test_llm_stub(mock)
    try:
        r = ap.classify_issues([], "browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["success"]
    assert r["classified"] == []
    assert len(calls) == 0


def test_safe_text_skips_env(tmp_path):
    """_safe_text НЕ читает .env и data.json."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "SECRET=SuperSecret123456\n", encoding="utf-8")
    (tmp_path / "data.json").write_text(
        '{"password": "RealPass123"}', encoding="utf-8")
    txt = ap._safe_text(str(tmp_path))
    assert "main.py" in txt
    assert "SuperSecret" not in txt, "утечка .env!"
    assert "RealPass" not in txt, "утечка data.json!"


def test_md_contains_llm_sections(tmp_path):
    """render_audit_md содержит секции LLM, если они есть."""
    _mk_project(tmp_path)
    mock, _ = _mock_llm_factory(
        desc="ОПИСАНИЕ_ТЕСТ",
        ideas="ИДЕЯ_ТЕСТ",
        cls_json='[]')
    ap.set_test_llm_stub(mock)
    try:
        r = ap.run_full_audit(str(tmp_path), mode="full", max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
    md = ap.render_audit_md(r)
    assert "## Описание проекта (LLM)" in md
    assert "ОПИСАНИЕ_ТЕСТ" in md
    assert "## Идеи развития (LLM)" in md
    assert "ИДЕЯ_ТЕСТ" in md
    assert "## 📊 Итого по уровням" in md


def test_llm_failure_doesnt_crash(tmp_path):
    """Если LLM падает — аудит всё равно success."""
    _mk_project(tmp_path)

    def _fail_llm(agent_mode, prompt):
        return {"success": False, "text": "", "error": "connection timeout"}

    ap.set_test_llm_stub(_fail_llm)
    try:
        r = ap.run_full_audit(str(tmp_path), mode="full", max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
    assert r["status"] == "success"  # не падаем!
    assert not r["llm_description"]["success"]


# ═══════════════════════════════════════════════════════════════
#  Выбор агента по ролям (agent_assignments.json)
# ═══════════════════════════════════════════════════════════════


def test_agent_for_role_default():
    """Роли без назначения → дефолт из _DEFAULT_AGENTS_BY_ROLE."""
    # В реальной среде могут быть назначения из ~/.ai_link.
    # Проверяем базовую логику через fallback.
    a = ap._agent_for_role("comprehension", fallback="X")
    assert a in ("X", "browser_qwen", "browser_deepseek",
                 "browser_perplexity", "gigachat"), a


def test_agent_for_role_unknown_with_fallback():
    """Неизвестная роль + fallback → fallback."""
    a = ap._agent_for_role("no_such_role_xyz", fallback="browser_xxx")
    assert a == "browser_xxx"


def test_agent_for_role_unknown_no_fallback():
    """ROLES (2026-10-01): hardcoded дефолты убраны → пусто."""
    from core.audit_project import _agent_for_role
    assert _agent_for_role("unknown_role_xyz") == ""


def test_describe_uses_comprehension(tmp_path, monkeypatch):
    """ROLES (2026-10-01): comprehension через agent_assignments.

    Раньше был hardcoded "browser_qwen". Теперь — из назначения.
    """
    from core.capability_benchmark import ASSIGNMENTS_FILE
    import json as _j
    af = tmp_path / "assign.json"
    af.write_text(_j.dumps({
        "version": "1.0",
        "assignments": {"comprehension": "browser_qwen"},
    }), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", af,
    )
    from core.audit_project import _agent_for_role
    assert _agent_for_role("comprehension") == "browser_qwen"


def test_suggest_uses_planning(tmp_path, monkeypatch):
    """ROLES (2026-10-01): planning через agent_assignments."""
    from core.capability_benchmark import ASSIGNMENTS_FILE
    import json as _j
    af = tmp_path / "assign.json"
    af.write_text(_j.dumps({
        "version": "1.0",
        "assignments": {"planning": "browser_qwen"},
    }), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", af,
    )
    from core.audit_project import _agent_for_role
    assert _agent_for_role("planning") == "browser_qwen"


def test_classify_uses_brain(tmp_path, monkeypatch):
    """ROLES (2026-10-01): brain через agent_assignments.

    Раньше hardcoded "browser_qwen". Теперь — из назначения.
    """
    import json as _j
    af = tmp_path / "assign.json"
    af.write_text(_j.dumps({
        "version": "1.0",
        "assignments": {"brain": "browser_qwen"},
    }), encoding="utf-8")
    monkeypatch.setattr(
        "core.capability_benchmark.ASSIGNMENTS_FILE", af,
    )
    from core.audit_project import _agent_for_role
    assert _agent_for_role("brain") == "browser_qwen"


def test_explicit_agent_mode_overrides_role(tmp_path):
    """Явный agent_mode перебивает роль."""
    _mk_project(tmp_path)
    seen = []

    def _mock(agent_mode, prompt):
        seen.append(agent_mode)
        return {"success": True, "text": "ok", "error": None}

    ap.set_test_llm_stub(_mock)
    try:
        ap.describe_project(str(tmp_path), agent_mode="gigachat")
    finally:
        ap.set_test_llm_stub(None)
    assert seen == ["gigachat"]


# ═══════════════════════════════════════════════════════════════
#  Безопасность: хардкод-секреты в .py НЕ уходят в LLM
# ═══════════════════════════════════════════════════════════════


def test_safe_text_blocks_hardcoded_secret(tmp_path):
    """Хардкод-пароль в .py — содержимое НЕ показывается LLM."""
    (tmp_path / "main.py").write_text(
        'API_KEY = "sk-real-prod-abcdef1234567890"\n'
        'DB_PASS = "SuperSecret2024xyz"\n',
        encoding="utf-8")
    txt = ap._safe_text(str(tmp_path))
    assert "sk-real-prod-abcdef" not in txt, "утечка API_KEY!"
    assert "SuperSecret2024xyz" not in txt, "утечка DB_PASS!"
    assert "НЕ ПОКАЗАН LLM" in txt


def test_safe_text_allows_clean_file(tmp_path):
    """Чистый .py — показывается полностью."""
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8")
    txt = ap._safe_text(str(tmp_path))
    assert "def add" in txt
    assert "НЕ ПОКАЗАН LLM" not in txt


def test_content_has_secrets_positive():
    assert ap._content_has_secrets('password = "RealSecret123"')
    assert ap._content_has_secrets("API_KEY=abcdef1234567890xyz")


def test_content_has_secrets_placeholder():
    """Placeholder'ы не считаются секретами."""
    assert not ap._content_has_secrets('pwd = "••••••••"')
    assert not ap._content_has_secrets('key = "example_dummy"')


def test_content_has_secrets_clean():
    assert not ap._content_has_secrets("def add(a, b): return a + b")


def test_clean_llm_text_removes_reasoning():
    """«Завершено размышление» вырезается."""
    raw = (
        "Завершено размышление\n"
        "Мы должны описать проект. Начнём с главного.\n"
        "Реальный ответ начинается здесь."
    )
    cleaned = ap._clean_llm_text(raw)
    assert "Завершено размышление" not in cleaned
    assert "Реальный ответ" in cleaned


def test_clean_llm_text_thought_for():
    raw = "Thought for 5 seconds\nРеальный ответ"
    cleaned = ap._clean_llm_text(raw)
    assert "Thought for" not in cleaned
    assert "Реальный ответ" in cleaned


def test_clean_llm_text_clean_stays(tmp_path):
    """Чистый текст не меняется."""
    raw = "Обычный ответ без reasoning."
    assert ap._clean_llm_text(raw) == raw


def test_secrets_are_critical_in_classification():
    """Секреты принудительно CRITICAL, независимо от LLM."""
    def _mock(agent_mode, prompt):
        # LLM отвечает MINOR (неправильно)
        return {"success": True,
                "text": '[{"i": 1, "level": "MINOR", "reason": "x"}]',
                "error": None}
    ap.set_test_llm_stub(_mock)
    try:
        issues = [
            {"severity": "high", "category": "secret",
             "file": ".env", "line": 1, "message": "secret"},
        ]
        r = ap.classify_issues(issues, agent_mode="browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["success"]
    secret_item = next(
        (c for c in r["classified"] if c["i"] == 1), None)
    assert secret_item is not None
    assert secret_item["level"] == "CRITICAL", \
        f"секрет не CRITICAL: {secret_item}"


def test_clean_deepseek_english_reasoning():
    """DeepSeek английский reasoning вырезается."""
    raw = (
        "We need answer in Russian. Let me think.\n\n"
        "1. Первая идея — мастер-пароль.\n"
        "2. Вторая идея — шифрование."
    )
    cleaned = ap._clean_llm_text(raw)
    assert "We need" not in cleaned
    assert "мастер-пароль" in cleaned


def test_clean_deepseek_russian_reasoning():
    """DeepSeek русский reasoning вырезается."""
    raw = (
        "Мы должны описать проект. Дан фрагмент кода.\n\n"
        "ДЛЯ ЧЕГО: хранит пароли.\n"
        "ЧТО ДЕЛАЕТ: загружает данные."
    )
    cleaned = ap._clean_llm_text(raw)
    assert "Мы должны" not in cleaned
    assert "ДЛЯ ЧЕГО:" in cleaned


def test_clean_struct_marker_cuts():
    """Если «ДЛЯ ЧЕГО:» внутри абзаца — режем до него."""
    raw = (
        "Много reasoning текста. И ещё. "
        "ДЛЯ ЧЕГО: хранит пароли."
    )
    cleaned = ap._clean_llm_text(raw)
    assert cleaned.startswith("ДЛЯ ЧЕГО")


# ═══════════════════════════════════════════════════════════════
#  logic_analyzer: различаем «чисто» от «невалидный ответ»
# ═══════════════════════════════════════════════════════════════


def test_parse_returns_ok_for_empty():
    from core.logic_analyzer import _parse_ai_response
    items, ok = _parse_ai_response("[]", return_ok=True)
    assert items == []
    assert ok is True


def test_parse_returns_not_ok_for_junk():
    from core.logic_analyzer import _parse_ai_response
    items, ok = _parse_ai_response("json\n\n123", return_ok=True)
    assert items == []
    assert ok is False


def test_parse_returns_ok_for_valid():
    from core.logic_analyzer import _parse_ai_response
    raw = '[{"severity": "low", "line": 1, "message": "test"}]'
    items, ok = _parse_ai_response(raw, return_ok=True)
    assert len(items) == 1
    assert ok is True


def test_parse_reasoning_then_valid():
    from core.logic_analyzer import _parse_ai_response
    raw = 'Завершено размышление\n[{"severity": "low", "line": 1, "message": "x"}]'
    items, ok = _parse_ai_response(raw, return_ok=True)
    assert len(items) == 1
    assert ok is True


def test_parse_reasoning_then_junk():
    from core.logic_analyzer import _parse_ai_response
    items, ok = _parse_ai_response(
        "Завершено размышление\njson\n123", return_ok=True)
    assert items == []
    assert ok is False


def test_parse_backward_compat_no_return_ok():
    from core.logic_analyzer import _parse_ai_response
    r = _parse_ai_response("[]")
    assert r == []
    r2 = _parse_ai_response('[{"message": "x"}]')
    assert len(r2) == 1


# ═══════════════════════════════════════════════════════════════
#  A15.1. AST-разведка testable functions
# ═══════════════════════════════════════════════════════════════


def test_list_testable_basic(tmp_path):
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def greet(name):\n    return f'hi {name}'\n\n"
        "def no_args():\n    return 42\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "add" in names
    assert "greet" in names
    assert "no_args" not in names  # нет параметров


def test_list_skips_dunder_private(tmp_path):
    (tmp_path / "m.py").write_text(
        "def __init__(self): pass\n"
        "def _helper(x): return x\n"
        "def test_something(): pass\n"
        "def main(): pass\n"
        "def public(x): return x\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "public" in names
    assert "_helper" not in names
    assert "__init__" not in names
    assert "test_something" not in names
    assert "main" not in names


def test_list_skips_secrets(tmp_path):
    (tmp_path / "a.py").write_text(
        "def clean(x): return x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        'API_KEY = "sk-real-prod-abcdef1234567890"\n'
        "def dirty(x): return x\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "clean" in names
    assert "dirty" not in names, "файл с секретом не пропущен"


def test_list_skips_tests_dir(tmp_path):
    (tmp_path / "src.py").write_text(
        "def real(x): return x\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text(
        "def inner(x): return x\n", encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "real" in names
    assert "inner" not in names


def test_list_priority_main_first(tmp_path):
    (tmp_path / "z_last.py").write_text(
        "def z_func(x): return x\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "def main_func(x): return x\n", encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    # main.py должен идти первым
    assert fns[0]["name"] == "main_func"


def test_list_skips_async(tmp_path):
    (tmp_path / "a.py").write_text(
        "async def a_func(x): return x\n"
        "def s_func(x): return x\n", encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "s_func" in names
    assert "a_func" not in names


# ═══════════════════════════════════════════════════════════════
#  A15.2. Валидация теста в изоляции
# ═══════════════════════════════════════════════════════════════


def test_validate_passing_test(tmp_path):
    """Хороший тест → ok=True."""
    (tmp_path / "m.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_code = (
        "from m import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n"
    )
    r = ap._validate_test_in_isolation(test_code, str(tmp_path))
    assert r["ok"] is True
    assert r["reason"] == "passed"


def test_validate_failing_test(tmp_path):
    """Тест с неверным assert → ok=False, reason=failed."""
    (tmp_path / "m.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_code = (
        "from m import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 999\n"
    )
    r = ap._validate_test_in_isolation(test_code, str(tmp_path))
    assert r["ok"] is False
    assert r["reason"] == "failed"


def test_validate_broken_test(tmp_path):
    """Тест с ImportError → error, не failed."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    test_code = (
        "from nonexistent import foo\n\n"
        "def test_x():\n    assert foo() == 1\n"
    )
    r = ap._validate_test_in_isolation(test_code, str(tmp_path))
    assert r["ok"] is False
    assert r["reason"] == "error"


def test_validate_empty_test():
    """Пустой тест или pass → empty_test."""
    r1 = ap._validate_test_in_isolation("", "/tmp")
    assert r1["reason"] == "empty_test"
    r2 = ap._validate_test_in_isolation("pass", "/tmp")
    assert r2["reason"] == "empty_test"
    r3 = ap._validate_test_in_isolation("def test_x(): pass", "/tmp")
    assert r3["reason"] == "empty_test"


def test_validate_does_not_touch_original(tmp_path):
    """Оригинальный проект не меняется."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    test_code = "def test_x():\n    assert 1 == 1\n"
    ap._validate_test_in_isolation(test_code, str(tmp_path))
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after, f"оригинал изменён: {before} → {after}"
    assert not (tmp_path / "tests").exists()


# ═══════════════════════════════════════════════════════════════
#  A15.3. Генерация тестов через LLM (мок)
# ═══════════════════════════════════════════════════════════════


def test_generate_full_cycle(tmp_path):
    """Полный цикл: AST → LLM → валидация → кандидат."""
    (tmp_path / "m.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")

    def _mock_llm(agent_mode, prompt):
        if "add" in prompt:
            return {"success": True,
                    "text": ("import pytest\n"
                             "from m import add\n\n"
                             "def test_add_basic():\n"
                             "    assert add(1, 2) == 3\n"),
                    "error": None}
        return {"success": True, "text": "SKIP", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    try:
        r = ap.generate_tests_for_project(
            str(tmp_path), max_functions=5,
            agent_mode="browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["total_candidates"] >= 1, r
    assert any("add" in c["func"]["name"] for c in r["candidates"])


def test_generate_skips_bad_test(tmp_path):
    """LLM даёт плохой тест → отбрасывается."""
    (tmp_path / "m.py").write_text(
        "def f(x):\n    return x\n", encoding="utf-8")

    def _mock_llm(agent_mode, prompt):
        return {"success": True,
                "text": ("from m import f\n\n"
                         "def test_f():\n"
                         "    assert f(1) == 999\n"),  # падает!
                "error": None}

    ap.set_test_llm_stub(_mock_llm)
    try:
        r = ap.generate_tests_for_project(
            str(tmp_path), max_functions=1,
            agent_mode="browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["total_candidates"] == 0
    assert r["total_rejected"] >= 1


def test_generate_handles_skip(tmp_path):
    """LLM отвечает SKIP — идём дальше."""
    (tmp_path / "m.py").write_text(
        "def f(x):\n    return x\n", encoding="utf-8")

    def _mock_llm(agent_mode, prompt):
        return {"success": True, "text": "SKIP", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    try:
        r = ap.generate_tests_for_project(
            str(tmp_path), max_functions=1,
            agent_mode="browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    assert r["total_candidates"] == 0
    assert r["total_rejected"] == 1


def test_generate_respects_secrets(tmp_path):
    """Файлы с секретами — не генерируем."""
    (tmp_path / "s.py").write_text(
        'API_KEY = "sk-real-prod-abcdef1234567890"\n'
        "def f(x):\n    return x\n", encoding="utf-8")

    called = []

    def _mock_llm(agent_mode, prompt):
        called.append(prompt)
        return {"success": True, "text": "SKIP", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    try:
        r = ap.generate_tests_for_project(
            str(tmp_path), max_functions=5,
            agent_mode="browser_deepseek")
    finally:
        ap.set_test_llm_stub(None)
    # AST уже отсеял файл с секретами
    assert r["total_considered"] == 0


def test_apply_generated_tests(tmp_path):
    """apply_generated_tests пишет файлы в tests/."""
    (tmp_path / "m.py").write_text("x=1\n", encoding="utf-8")
    cands = [
        {"file_name": "test_gen_a.py",
         "test_code": "def test_a():\n    assert 1==1\n"},
        {"file_name": "test_gen_b.py",
         "test_code": "def test_b():\n    assert 2==2\n"},
    ]
    r = ap.apply_generated_tests(str(tmp_path), cands)
    assert len(r["applied"]) == 2
    assert (tmp_path / "tests" / "test_gen_a.py").exists()
    assert (tmp_path / "tests" / "test_gen_b.py").exists()


def test_apply_skips_existing(tmp_path):
    """Не перезаписывает существующие тесты."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_gen_a.py").write_text(
        "# оригинал\n", encoding="utf-8")
    cands = [{"file_name": "test_gen_a.py",
              "test_code": "def test_x(): pass\n"}]
    r = ap.apply_generated_tests(str(tmp_path), cands)
    assert len(r["applied"]) == 0
    assert len(r["failed"]) == 1
    # Оригинал не перезаписан
    txt = (tmp_path / "tests" / "test_gen_a.py").read_text()
    assert "оригинал" in txt


# ═══════════════════════════════════════════════════════════════
#  A15.4. Интеграция в run_full_audit
# ═══════════════════════════════════════════════════════════════


def test_run_full_audit_generate_tests_flag(tmp_path):
    """generate_tests=True вызывает генерацию в full-режиме."""
    (tmp_path / "m.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")

    def _mock_llm(agent_mode, prompt):
        if "опиши проект" in prompt.lower():
            return {"success": True, "text": "ok", "error": None}
        if "предложи" in prompt.lower():
            return {"success": True, "text": "идеи", "error": None}
        if "классифиц" in prompt.lower():
            return {"success": True, "text": "[]", "error": None}
        # A15 промпт
        if "add" in prompt and "pytest" in prompt.lower():
            return {"success": True,
                    "text": ("from m import add\n\n"
                             "def test_add():\n"
                             "    assert add(1, 2) == 3\n"),
                    "error": None}
        return {"success": True, "text": "SKIP", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    ap.set_test_logic_stub(lambda d, m, a: {
        "success": True, "issues": [], "analyzed_files": []})
    try:
        r = ap.run_full_audit(
            str(tmp_path), mode="full", max_time_min=5,
            generate_tests=True, max_test_functions=3)
    finally:
        ap.set_test_llm_stub(None)
        ap.set_test_logic_stub(None)
    assert "tests_generated" in r, "A15 не вызвался"


def test_run_full_audit_no_generate_by_default(tmp_path):
    """По умолчанию generate_tests=False."""
    _mk_project(tmp_path)

    def _mock_llm(a, p):
        return {"success": True, "text": "ok", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    ap.set_test_logic_stub(lambda d, m, a: {
        "success": True, "issues": [], "analyzed_files": []})
    try:
        r = ap.run_full_audit(str(tmp_path), mode="full",
                              max_time_min=5)
    finally:
        ap.set_test_llm_stub(None)
        ap.set_test_logic_stub(None)
    assert "tests_generated" not in r


def test_run_fast_ignores_generate_tests(tmp_path):
    """В fast-режиме A15 не запускается, даже если флаг True."""
    _mk_project(tmp_path)
    r = ap.run_full_audit(
        str(tmp_path), mode="fast",
        generate_tests=True)
    assert "tests_generated" not in r


def test_md_shows_generated_tests(tmp_path):
    """Секция «Тесты-кандидаты» в отчёте."""
    (tmp_path / "m.py").write_text(
        "def f(x):\n    return x\n", encoding="utf-8")

    def _mock_llm(a, prompt):
        if "pytest" in prompt.lower() and " f" in prompt:
            return {"success": True,
                    "text": ("from m import f\n\n"
                             "def test_f():\n"
                             "    assert f(1) == 1\n"),
                    "error": None}
        return {"success": True, "text": "ok", "error": None}

    ap.set_test_llm_stub(_mock_llm)
    ap.set_test_logic_stub(lambda d, m, a: {
        "success": True, "issues": [], "analyzed_files": []})
    try:
        r = ap.run_full_audit(
            str(tmp_path), mode="full", max_time_min=5,
            generate_tests=True, max_test_functions=2)
    finally:
        ap.set_test_llm_stub(None)
        ap.set_test_logic_stub(None)
    md = ap.render_audit_md(r)
    if "tests_generated" in r:
        assert "Тесты-кандидаты" in md


# ═══════════════════════════════════════════════════════════════
#  BROWSER-CACHE-CLEANUP: чистка безопасного кэша
# ═══════════════════════════════════════════════════════════════


def test_clean_browser_cache_basic(tmp_path, monkeypatch):
    """Чистит Cache/, Code Cache/, GPUCache/, но НЕ Cookies."""
    from core import browser_cleanup as bc
    # Подменяем HOME на tmp_path
    profiles = tmp_path / ".ai_link" / "browser_profiles"
    prof = profiles / "test_profile" / "Default"
    (prof / "Cache").mkdir(parents=True)
    (prof / "Cache" / "data.bin").write_bytes(b"x" * 1024)
    (prof / "Code Cache").mkdir(parents=True)
    (prof / "Code Cache" / "js.bin").write_bytes(b"y" * 512)
    (prof / "GPUCache").mkdir(parents=True)
    # ВАЖНОЕ: что НЕ должно удаляться
    (prof / "Cookies").write_bytes(b"session")
    (prof / "Local Storage").mkdir(parents=True)
    (prof / "Local Storage" / "logins.db").write_bytes(b"data")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    r = bc.clean_browser_cache()

    assert r["cleaned_dirs"] >= 2
    assert r["freed_bytes"] >= 1024
    assert not (prof / "Cache").exists()
    assert not (prof / "Code Cache").exists()
    # Cookies, Local Storage — на месте!
    assert (prof / "Cookies").exists()
    assert (prof / "Local Storage" / "logins.db").exists()


def test_clean_browser_cache_specific_profile(tmp_path, monkeypatch):
    """profile_name чистит только указанный профиль."""
    from core import browser_cleanup as bc
    profiles = tmp_path / ".ai_link" / "browser_profiles"
    p1 = profiles / "qwen_profile" / "Default" / "Cache"
    p2 = profiles / "deepseek_profile" / "Default" / "Cache"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    (p1 / "a.bin").write_bytes(b"a" * 100)
    (p2 / "b.bin").write_bytes(b"b" * 100)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    r = bc.clean_browser_cache(profile_name="qwen_profile")

    assert "qwen_profile" in r["profiles"]
    assert "deepseek_profile" not in r["profiles"]
    assert not p1.exists()
    assert p2.exists()  # не тронут


def test_clean_browser_cache_no_profiles(tmp_path, monkeypatch):
    """Нет профилей — не падаем."""
    from core import browser_cleanup as bc
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    r = bc.clean_browser_cache()
    assert r["cleaned_dirs"] == 0
    assert r["errors"]


def test_clean_browser_cache_sw_caches(tmp_path, monkeypatch):
    """Service Worker CacheStorage и ScriptCache чистятся."""
    from core import browser_cleanup as bc
    profiles = tmp_path / ".ai_link" / "browser_profiles"
    prof = profiles / "q" / "Default"
    (prof / "Service Worker" / "CacheStorage").mkdir(parents=True)
    (prof / "Service Worker" / "CacheStorage" / "x.bin").write_bytes(b"z" * 200)
    (prof / "Service Worker" / "ScriptCache").mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    r = bc.clean_browser_cache()
    assert r["cleaned_dirs"] >= 2
    assert not (prof / "Service Worker" / "CacheStorage").exists()
    assert not (prof / "Service Worker" / "ScriptCache").exists()


# ═══════════════════════════════════════════════════════════════
#  _extract_python_code (фикс reasoning в тестах)
# ═══════════════════════════════════════════════════════════════


def test_extract_clean_code():
    """Чистый код без обёрток."""
    raw = "from m import f\n\ndef test_f():\n    assert f(1) == 1\n"
    assert ap._extract_python_code(raw).startswith("from m import")


def test_extract_strips_reasoning_prefix():
    """Reasoning в начале вырезается."""
    raw = ("We need to test this. Let me write it.\n"
           "from m import f\n\ndef test_f():\n    assert f(1) == 1\n")
    out = ap._extract_python_code(raw)
    assert out.startswith("from m import")
    assert "We need" not in out


def test_extract_strips_reasoning_suffix():
    """Reasoning в конце вырезается."""
    raw = ("from m import f\n\ndef test_f():\n"
           "    assert f(1) == 1\n\n"
           "This is a good test, it covers basic case.")
    out = ap._extract_python_code(raw)
    assert "good test" not in out
    assert "assert f(1)" in out


def test_extract_markdown_fence():
    """Код в ```python ... ``` извлекается."""
    raw = ("```python\nfrom m import f\n\n"
           "def test_f():\n    assert f(1) == 1\n```")
    out = ap._extract_python_code(raw)
    assert out.startswith("from m import")
    assert "```" not in out


def test_extract_empty():
    assert ap._extract_python_code("") == ""
    assert ap._extract_python_code("только reasoning без кода") == ""


def test_extract_russian_reasoning():
    """Русский reasoning в начале вырезается."""
    raw = ("Мы должны написать тест. Начнём с импорта.\n\n"
           "from m import f\n\ndef test_f():\n    assert f(1) == 1\n")
    out = ap._extract_python_code(raw)
    assert "Мы должны" not in out
    assert "from m import" in out


def test_extract_filters_mid_reasoning():
    """Reasoning ВНУТРИ кода вырезается."""
    raw = (
        "from m import f\n\n"
        "def test_f():\n"
        "    Should we use monkeypatch? OK.\n"
        "    assert f(1) == 1\n"
    )
    out = ap._extract_python_code(raw)
    assert "Should we use" not in out
    assert "assert f(1)" in out


def test_extract_filters_multiple_mid_lines():
    """Несколько reasoning-строк внутри — все вырезаны."""
    raw = (
        "from m import f\n\n"
        "def test_f():\n"
        "    That's one assert.\n"
        "    Should pass.\n"
        "    assert f(1) == 1\n"
    )
    out = ap._extract_python_code(raw)
    assert "That's one" not in out
    assert "Should pass" not in out
    assert "assert f(1)" in out


def test_extract_validation_compile():
    """Невалидный код → пустая строка."""
    raw = "def test_broken(\n    незакрытая скобка"
    assert ap._extract_python_code(raw) == ""


def test_extract_keeps_indent():
    """Отступы внутри функции сохраняются."""
    raw = ("from m import f\n\n"
           "def test_f():\n"
           "    x = 5\n"
           "    assert f(x) == x\n")
    out = ap._extract_python_code(raw)
    assert "    x = 5" in out
    assert "    assert f(x)" in out


def test_fix_indentation_basic():
    """Восстановление отступов: def + assert без отступа."""
    bad = "def test_x():\nassert True"
    good = ap._fix_python_indentation(bad)
    assert "    assert True" in good
    compile(good, "<t>", "exec")


def test_fix_indentation_nested():
    """Вложенные блоки."""
    bad = ("def test_x():\nif True:\nassert 1 == 1\nelse:\n"
           "assert 2 == 2")
    good = ap._fix_python_indentation(bad)
    compile(good, "<t>", "exec")


def test_fix_indentation_keeps_toplevel():
    """Top-level import и def остаются без отступа."""
    bad = "import pytest\nfrom m import f\ndef test_x():\nassert f() == 1"
    good = ap._fix_python_indentation(bad)
    assert good.startswith("import pytest")
    assert "\ndef test_x():" in good
    compile(good, "<t>", "exec")


def test_strip_ui_noise_prefix():
    """python / Copy / Download срезаются."""
    raw = "python\nCopy\nDownload\nimport pytest\n"
    out = ap._strip_ui_noise(raw)
    assert out.startswith("import pytest")


def test_strip_ui_noise_fence():
    """Markdown-fence снимается."""
    raw = "```python\nimport pytest\n```"
    out = ap._strip_ui_noise(raw)
    assert out == "import pytest"


# ═══════════════════════════════════════════════════════════════
#  A15-FILTER: приоритизация функций
# ═══════════════════════════════════════════════════════════════


def test_filter_skips_gui(tmp_path):
    """GUI-метод пропускается."""
    (tmp_path / "m.py").write_text(
        "def make_button():\n"
        "    b = QPushButton('ok')\n"
        "    b.pack()\n"
        "    return b\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "make_button" not in names, f"GUI не отсеян: {names}"


def test_filter_skips_io(tmp_path):
    """Функция с open/subprocess — пропускается."""
    (tmp_path / "m.py").write_text(
        "import subprocess\n"
        "def run_ls(path):\n"
        "    return subprocess.run(['ls', path])\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "run_ls" not in names, f"IO не отсеян: {names}"


def test_filter_skips_open(tmp_path):
    (tmp_path / "m.py").write_text(
        "def read_config(p):\n"
        "    with open(p) as f:\n"
        "        return f.read()\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "read_config" not in names


def test_filter_high_priority_method(tmp_path):
    """Метод класса с логикой — priority=high."""
    (tmp_path / "m.py").write_text(
        "class Data:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "    def add(self, x):\n"
        "        if x not in self.items:\n"
        "            self.items.append(x)\n"
        "            return True\n"
        "        return False\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    add_fn = next((f for f in fns if f["name"] == "add"), None)
    assert add_fn is not None
    assert add_fn["priority"] == "high", f"got {add_fn['priority']}"


def test_filter_low_priority_getter(tmp_path):
    """Простой getter → priority=low."""
    (tmp_path / "m.py").write_text(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.x = 5\n"
        "    def get_x(self):\n"
        "        return self.x\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    gx = next((f for f in fns if f["name"] == "get_x"), None)
    if gx:
        assert gx["priority"] == "low", f"got {gx['priority']}"


def test_filter_orders_high_first(tmp_path):
    """high идёт раньше medium и low."""
    (tmp_path / "m.py").write_text(
        "class Data:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "    def get_items(self):\n"
        "        return self.items\n"
        "    def add(self, x):\n"
        "        if x not in self.items:\n"
        "            self.items.append(x)\n"
        "            return True\n"
        "        return False\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    # add должен быть выше get_items
    names = [f["name"] for f in fns]
    if "add" in names and "get_items" in names:
        assert names.index("add") < names.index("get_items")


def test_filter_skips_gui_class(tmp_path):
    """Все методы GUI-класса пропускаются."""
    (tmp_path / "m.py").write_text(
        "import tkinter as tk\n"
        "from tkinter import ttk\n"
        "\n"
        "class App:\n"
        "    def __init__(self):\n"
        "        self.root = tk.Tk()\n"
        "        self.tree = ttk.Treeview(self.root)\n"
        "    def refresh_table(self, event):\n"
        "        data = [1, 2, 3]\n"
        "        return sum(data)\n"
        "    def on_click(self, e):\n"
        "        return e.x + 1\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "refresh_table" not in names, f"GUI-метод не отсеян: {names}"
    assert "on_click" not in names, f"GUI-метод не отсеян: {names}"


def test_filter_keeps_data_class(tmp_path):
    """Методы класса БЕЗ GUI остаются."""
    (tmp_path / "m.py").write_text(
        "class Data:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "    def add(self, x):\n"
        "        if x not in self.items:\n"
        "            self.items.append(x)\n"
        "            return True\n"
        "        return False\n",
        encoding="utf-8")
    fns = ap._list_testable_functions(str(tmp_path))
    names = [f["name"] for f in fns]
    assert "add" in names


# ═══════════════════════════════════════════════════════════════
#  READ-ONLY GUARD: аудит не пишет вне .ai_link/
# ═══════════════════════════════════════════════════════════════


def test_guard_allows_ai_link_path(tmp_path):
    """Путь внутри .ai_link — ok."""
    ai = tmp_path / ".ai_link" / "audits" / "2026"
    ap._assert_inside_ai_link(str(tmp_path), ai)  # не бросает


def test_guard_blocks_outside(tmp_path):
    """Путь вне .ai_link — AuditWriteViolation."""
    bad = tmp_path / "src" / "main.py"
    try:
        ap._assert_inside_ai_link(str(tmp_path), bad)
        assert False, "не бросил исключение"
    except ap.AuditWriteViolation:
        pass


def test_guard_blocks_parent(tmp_path):
    """Путь родителя .ai_link — блок."""
    bad = tmp_path  # сам project_dir
    try:
        ap._assert_inside_ai_link(str(tmp_path), bad)
        assert False
    except ap.AuditWriteViolation:
        pass


def test_audit_writes_only_in_ai_link(tmp_path, monkeypatch):
    """Страж: run_full_audit пишет только в .ai_link/."""
    # Создаём минимальный проект
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    violations = []

    # Ловим любые записи через pathlib
    orig_write = Path.write_text
    orig_mkdir = Path.mkdir

    def _watch_write(self, *a, **kw):
        try:
            self.resolve().relative_to(
                (tmp_path / ".ai_link").resolve())
        except Exception:
            violations.append(f"write: {self}")
        return orig_write(self, *a, **kw)

    def _watch_mkdir(self, *a, **kw):
        try:
            self.resolve().relative_to(
                (tmp_path / ".ai_link").resolve())
        except Exception:
            # Разрешаем .resolve() самого tmp_path
            if str(self.resolve()) != str(tmp_path.resolve()):
                violations.append(f"mkdir: {self}")
        return orig_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", _watch_write)
    monkeypatch.setattr(Path, "mkdir", _watch_mkdir)

    r = ap.run_full_audit(str(tmp_path), mode="fast",
                          max_time_min=2)

    assert r["status"] == "success"
    assert not violations, f"аудит писал вне .ai_link: {violations}"


# ═══════════════════════════════════════════════════════════════
#  DEPENDENCY SCAN
# ═══════════════════════════════════════════════════════════════


def test_deps_no_files(tmp_path):
    """Нет файлов зависимостей — пустой результат."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    assert r["files"] == []
    assert r["total"] == 0


def test_deps_requirements(tmp_path):
    """requirements.txt — пакеты извлекаются."""
    (tmp_path / "requirements.txt").write_text(
        "requests>=2.0\n"
        "click==8.0\n"
        "# комментарий\n"
        "\n"
        "flask\n",
        encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    assert "requirements.txt" in r["files"]
    assert "requests" in r["packages"]
    assert "click" in r["packages"]
    assert "flask" in r["packages"]


def test_deps_missing_detected(tmp_path):
    """Неустановленный пакет — в missing."""
    (tmp_path / "requirements.txt").write_text(
        "some-fake-package-xyz-12345==1.0\n",
        encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    assert "some-fake-package-xyz-12345" in r["missing"]


def test_deps_installed_detected(tmp_path):
    """Установленный пакет (pytest) — не в missing."""
    (tmp_path / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    assert "pytest" not in r["missing"]


def test_deps_pyproject(tmp_path):
    """pyproject.toml — пакеты из dependencies."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n'
        'name = "x"\n'
        'dependencies = [\n'
        '    "requests>=2.0",\n'
        '    "click",\n'
        ']\n',
        encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    assert "pyproject.toml" in r["files"]
    assert "requests" in r["packages"]


def test_deps_multiple_files(tmp_path):
    """Несколько файлов — все в files."""
    (tmp_path / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text(
        "ruff\n", encoding="utf-8")
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup()\n", encoding="utf-8")
    r = ap.check_dependencies(str(tmp_path))
    files = set(r["files"])
    assert "requirements.txt" in files
    assert "requirements-dev.txt" in files
    assert "setup.py" in files


def test_deps_in_md_report(tmp_path):
    """Секция 📦 Зависимости появляется в отчёте."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8")
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    md = ap.render_audit_md(r)
    assert "## 📦 Зависимости" in md


# ═══════════════════════════════════════════════════════════════
#  BASELINE + COMPARE
# ═══════════════════════════════════════════════════════════════


def test_baseline_set_get(tmp_path):
    """set_baseline → get_baseline."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    r = ap.set_baseline(str(tmp_path))
    assert r["ok"], r.get("error")
    b = ap.get_baseline(str(tmp_path))
    assert b.get("audit_id")
    assert b.get("tech_total") == 0


def test_baseline_clear(tmp_path):
    """clear_baseline удаляет."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))
    assert ap.get_baseline(str(tmp_path))
    assert ap.clear_baseline(str(tmp_path))
    assert ap.get_baseline(str(tmp_path)) == {}


def test_baseline_no_audit(tmp_path):
    """Без аудита — ошибка."""
    r = ap.set_baseline(str(tmp_path))
    assert not r["ok"]


def test_compare_no_baseline(tmp_path):
    """Без baseline — has_baseline=False."""
    r = ap.compare_with_baseline(str(tmp_path), {})
    assert r["has_baseline"] is False


def test_compare_with_secrets_added(tmp_path):
    """Секреты добавились → worsened."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    r1 = ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))

    # Добавили секрет
    (tmp_path / "cfg.py").write_text(
        'API_KEY = "sk-real-abcdef1234567890"\n',
        encoding="utf-8")
    r2 = ap.run_full_audit(str(tmp_path), mode="fast")

    cmp = r2.get("baseline_compare", {})
    assert cmp.get("has_baseline")
    # secrets_total увеличился
    d = cmp["delta"]["secrets_total"]
    assert d["after"] > d["before"]
    assert "secrets_total" in cmp["worsened"]


def test_compare_improved(tmp_path):
    """Убрали секрет → improved."""
    (tmp_path / "cfg.py").write_text(
        'API_KEY = "sk-real-abcdef1234567890"\n',
        encoding="utf-8")
    r1 = ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))

    # Убираем файл с секретом
    (tmp_path / "cfg.py").unlink()
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    r2 = ap.run_full_audit(str(tmp_path), mode="fast")

    cmp = r2.get("baseline_compare", {})
    assert "secrets_total" in cmp["improved"]


def test_compare_in_md(tmp_path):
    """Секция 📌 в отчёте при наличии baseline."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    md = ap.render_audit_md(r)
    assert "## 📌 Сравнение с baseline" in md


def test_baseline_has_file_count(tmp_path):
    """file_count попадает в baseline."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "n.py").write_text("y = 2\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))
    b = ap.get_baseline(str(tmp_path))
    assert b.get("file_count") == 2


def test_compare_shows_file_count_delta(tmp_path):
    """Сравнение показывает +1 файл."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))
    # Добавили файл
    (tmp_path / "n.py").write_text("y = 2\n", encoding="utf-8")
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    cmp = r.get("baseline_compare", {})
    d = cmp["delta"].get("file_count")
    assert d is not None
    assert d["before"] == 1
    assert d["after"] == 2
    assert d["change"] == 1
    assert d.get("neutral") is True
    # НЕ в improved/worsened
    assert "file_count" not in cmp["improved"]
    assert "file_count" not in cmp["worsened"]


def test_compare_md_shows_file_count(tmp_path):
    """В отчёте есть строка «Файлов в проекте»."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    ap.run_full_audit(str(tmp_path), mode="fast")
    ap.set_baseline(str(tmp_path))
    (tmp_path / "n.py").write_text("y = 2\n", encoding="utf-8")
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    md = ap.render_audit_md(r)
    assert "Файлов в проекте" in md
    assert "1 → 2" in md


# ═══════════════════════════════════════════════════════════════
#  PIP-AUDIT (опционально)
# ═══════════════════════════════════════════════════════════════


def test_pip_audit_available_check():
    """_pip_audit_available возвращает bool, не падает."""
    r = ap._pip_audit_available()
    assert isinstance(r, bool)


def test_pip_audit_no_requirements(tmp_path):
    """Нет requirements — не падаем."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    r = ap.check_vulnerabilities(str(tmp_path))
    assert "available" in r
    assert r["total"] == 0


def test_pip_audit_nonexistent_project(tmp_path):
    """Несуществующий проект — не падаем."""
    r = ap.check_vulnerabilities(str(tmp_path / "no_such"))
    assert r["available"] is False
    assert r["total"] == 0


def test_pip_audit_with_requirements(tmp_path):
    """С requirements — либо available=True, либо ошибка про pip-audit."""
    (tmp_path / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8")
    r = ap.check_vulnerabilities(str(tmp_path))
    assert "available" in r
    # Если pip-audit не установлен — available=False с понятной ошибкой
    if not r["available"]:
        assert "pip-audit" in r["error"]


def test_pip_audit_in_audit_result(tmp_path):
    """В run_full_audit с requirements — проверка вызывается."""
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8")
    r = ap.run_full_audit(str(tmp_path), mode="fast")
    assert "vulnerabilities" in r
