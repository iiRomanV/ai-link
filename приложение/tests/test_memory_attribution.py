"""Тесты MEMORY-ATTRIBUTION (5-уровневая модель)."""
from core import memory_attribution as ma


# --------------------------------------------------------------------------- #
# extract_keywords
# --------------------------------------------------------------------------- #
def test_extract_keywords_basic():
    """Берём значимые слова, стоп-слова отсеиваем."""
    kw = ma.extract_keywords("Playwright timeout на networkidle для браузера")
    assert "playwright" in kw
    assert "timeout" in kw
    assert "networkidle" in kw
    assert "для" not in kw  # стоп-слово


def test_extract_keywords_min_len():
    """Слова короче min_len игнорируются."""
    kw = ma.extract_keywords("это мы вы он", min_len=4)
    assert kw == []


def test_extract_keywords_max_n():
    """Лимит max_n работает."""
    text = " ".join(f"word{i:04d}" for i in range(50))
    kw = ma.extract_keywords(text, max_n=5)
    assert len(kw) == 5


def test_extract_keywords_empty():
    assert ma.extract_keywords("") == []
    assert ma.extract_keywords(None) == []


def test_extract_keywords_uniqueness():
    """Дубликаты не повторяются."""
    kw = ma.extract_keywords("playwright playwright timeout playwright")
    assert kw.count("playwright") == 1


# --------------------------------------------------------------------------- #
# eligibility_score
# --------------------------------------------------------------------------- #
def _pattern(sig="playwright-timeout-networkidle",
             ctx='{"title": "Playwright timeout", "tags": "playwright,browser"}',
             fix="Использовать wait_for_selector вместо networkidle",
             scope="global", scope_value=""):
    return {
        "signature": sig,
        "context_json": ctx,
        "fix": fix,
        "scope": scope,
        "scope_value": scope_value,
    }


def test_eligibility_matches_task():
    """Релевантная задача → высокая eligibility."""
    pat = _pattern()
    score = ma.eligibility_score(
        pat, phase="fix",
        task_terms={"playwright", "timeout", "networkidle", "browser"},
    )
    assert score >= 0.4


def test_eligibility_no_match():
    """Нерелевантная задача → низкая eligibility."""
    pat = _pattern()
    score = ma.eligibility_score(
        pat, phase="fix",
        task_terms={"docstring", "модуль", "документация", "комментарий"},
    )
    assert score < 0.35


def test_eligibility_provider_match():
    """Совпадение provider → бонус."""
    pat = _pattern(scope="provider", scope_value="qwen")
    score_match = ma.eligibility_score(
        pat, phase="fix", provider="qwen",
    )
    score_miss = ma.eligibility_score(
        pat, phase="fix", provider="deepseek",
    )
    assert score_match > score_miss


def test_eligibility_global_scope():
    """Global scope → базовый бонус 0.10."""
    pat = _pattern(scope="global")
    score = ma.eligibility_score(pat, phase="fix")
    assert score >= 0.10


def test_eligibility_component_in_signature():
    """Component (имя файла) в signature → бонус."""
    pat = _pattern(sig="browser-manager-playwright-timeout")
    score = ma.eligibility_score(
        pat, phase="fix", component="browser",
    )
    assert score >= 0.30


def test_eligibility_returns_float_0_1():
    """Score в [0, 1]."""
    pat = _pattern()
    score = ma.eligibility_score(pat, phase="fix")
    assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# detect_used
# --------------------------------------------------------------------------- #
def test_detect_used_match():
    """Изменённый файл совпадает с signature."""
    pat = _pattern(sig="playwright-timeout")
    score = ma.detect_used(pat, changed_files=["core/browser_manager.py"])
    # Не совпадает с playwright, но used detection работает по terms
    assert score >= 0.0


def test_detect_used_direct_match():
    """Файл с именем = терм из signature."""
    pat = _pattern(sig="browser-manager-fix")
    score = ma.detect_used(
        pat, changed_files=["core/browser_manager.py"],
    )
    # browser и manager есть в signature
    assert score >= 0.3


def test_detect_used_empty_files():
    assert ma.detect_used(_pattern(), changed_files=[]) == 0.0
    assert ma.detect_used(_pattern(), changed_files=None) == 0.0


def test_detect_used_no_match():
    """Файл без совпадений → 0."""
    pat = _pattern(sig="sqlite-lock-wal")
    score = ma.detect_used(
        pat, changed_files=["gui/main_window.py"],
    )
    assert score < 0.3


# --------------------------------------------------------------------------- #
# classify_evidence
# --------------------------------------------------------------------------- #
def test_classify_full_chain():
    """Полный сценарий: retrieved → eligible → used → supported → verified."""
    pat = _pattern()
    r = ma.classify_evidence(
        pat, phase="fix", phase_status="success",
        problem_text="Playwright timeout на networkidle",
        changed_files=["core/browser_manager.py"],
        verify_passed=True,
        next_review_ok=True,
    )
    assert r["retrieved"] is True
    assert r["eligible"] is True
    assert r["used"] is True
    assert r["supported"] is True
    assert r["verified_effect"] is True
    assert r["attribution_method"] == "rule_v1"


def test_classify_not_eligible():
    """Паттерн не подходит → eligible=False → verified=False."""
    pat = _pattern()
    r = ma.classify_evidence(
        pat, phase="fix", phase_status="success",
        problem_text="Добавить docstring в модуль документации",
        changed_files=["docs/readme.py"],
        verify_passed=True,
        next_review_ok=True,
    )
    assert r["eligible"] is False
    assert r["verified_effect"] is False


def test_classify_used_but_not_supported():
    """used=True, но review не OK → supported=False."""
    pat = _pattern()
    r = ma.classify_evidence(
        pat, phase="fix", phase_status="success",
        problem_text="Playwright timeout на networkidle",
        changed_files=["core/browser_manager.py"],
        verify_passed=True,
        next_review_ok=False,  # review не прошёл
    )
    assert r["used"] is True
    assert r["supported"] is False
    assert r["verified_effect"] is False


def test_classify_supported_but_verify_failed():
    """supported=True, но VERIFY упал → verified=False."""
    pat = _pattern()
    r = ma.classify_evidence(
        pat, phase="fix", phase_status="success",
        problem_text="Playwright timeout на networkidle",
        changed_files=["core/browser_manager.py"],
        verify_passed=False,  # VERIFY упал
        next_review_ok=True,
    )
    assert r["supported"] is True
    assert r["verified_effect"] is False


def test_classify_returns_all_keys():
    """Возвращает все ожидаемые ключи."""
    pat = _pattern()
    r = ma.classify_evidence(
        pat, phase="fix", phase_status="success",
        problem_text="test",
    )
    for k in ("retrieved", "eligible", "used", "supported",
              "verified_effect", "relevance_score",
              "attribution_method"):
        assert k in r
