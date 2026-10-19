"""Тесты для core.review_log."""

from pathlib import Path
from core.review_log import (
    log_review, load_reviews, get_reviewer_stats,
    count_syntax_errors, reset_log,
)


def setup_function(_func):
    pass


def test_log_and_load(tmp_path):
    reset_log(str(tmp_path))
    log_review(str(tmp_path), "browser_qwen", "problems",
               output="есть проблемы", syntax_errors_before=0)
    log_review(str(tmp_path), "browser_qwen", "ok",
               output="всё ок", syntax_errors_before=0)
    records = load_reviews(str(tmp_path))
    assert len(records) == 2
    assert records[0]["agent"] == "browser_qwen"
    assert records[0]["verdict"] == "problems"
    assert records[1]["verdict"] == "ok"


def test_validated_true_when_syntax_errors(tmp_path):
    """Если до fix были syntax errors и fix применился — validated=True."""
    reset_log(str(tmp_path))
    r = log_review(str(tmp_path), "qwen", "problems",
                   syntax_errors_before=2,
                   fix_applied=True, syntax_errors_after=0)
    assert r["validated"] is True


def test_validated_none_when_no_syntax_errors(tmp_path):
    """CROSS-REVIEW-2: без syntax и без severity → None.

    Раньше: validated=False («придирка»). Но логическая ошибка
    или SQL-инъекция могут быть без syntax. Поэтому None —
    не портим статистику. Решит cross-verify или severity.
    """
    reset_log(str(tmp_path))
    r = log_review(str(tmp_path), "qwen", "problems",
                   syntax_errors_before=0,
                   fix_applied=True, syntax_errors_after=0)
    assert r["validated"] is None


def test_validated_true_when_severity_critical(tmp_path):
    """CROSS-REVIEW-2: severity=critical → validated=True."""
    reset_log(str(tmp_path))
    r = log_review(str(tmp_path), "qwen", "problems",
                   severity="critical")
    assert r["validated"] is True


def test_validated_false_when_severity_minor(tmp_path):
    """CROSS-REVIEW-2: severity=minor → validated=False (стиль)."""
    reset_log(str(tmp_path))
    r = log_review(str(tmp_path), "qwen", "problems",
                   severity="minor")
    assert r["validated"] is False


def test_validated_none_for_ok(tmp_path):
    reset_log(str(tmp_path))
    r = log_review(str(tmp_path), "qwen", "ok")
    assert r["validated"] is None


def test_get_reviewer_stats(tmp_path):
    """CROSS-REVIEW-2: статистика с severity."""
    reset_log(str(tmp_path))
    # 1 minor → False, 1 critical → True, 1 syntax>0 → True, 1 ok
    log_review(str(tmp_path), "browser_qwen", "problems",
               severity="minor")
    log_review(str(tmp_path), "browser_qwen", "problems",
               severity="critical")
    log_review(str(tmp_path), "browser_qwen", "problems",
               syntax_errors_before=1, fix_applied=True)
    log_review(str(tmp_path), "browser_qwen", "ok")

    stats = get_reviewer_stats(str(tmp_path))
    s = stats["browser_qwen"]
    assert s["total"] == 4
    assert s["problems"] == 3
    assert s["ok"] == 1
    assert s["validated_false"] == 1
    assert s["validated_true"] == 2
    assert s["false_alarm_rate"] == round(1/3, 3)


def test_get_reviewer_stats_multi_agents(tmp_path):
    """CROSS-REVIEW-2: два агента с разной severity."""
    reset_log(str(tmp_path))
    log_review(str(tmp_path), "qwen", "problems", severity="minor")
    log_review(str(tmp_path), "deepseek", "problems", syntax_errors_before=1)
    stats = get_reviewer_stats(str(tmp_path))
    assert "qwen" in stats
    assert "deepseek" in stats
    assert stats["qwen"]["validated_false"] == 1
    assert stats["deepseek"]["validated_true"] == 1


def test_load_reviews_empty(tmp_path):
    reset_log(str(tmp_path))
    assert load_reviews(str(tmp_path)) == []


def test_load_reviews_limit(tmp_path):
    reset_log(str(tmp_path))
    for i in range(10):
        log_review(str(tmp_path), "qwen", "ok", output=f"msg {i}")
    records = load_reviews(str(tmp_path), limit=3)
    assert len(records) == 3
    assert records[0]["output_head"] == "msg 7"


def test_count_syntax_errors_clean(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert count_syntax_errors(str(tmp_path)) == 0


def test_count_syntax_errors_broken(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    assert count_syntax_errors(str(tmp_path)) >= 1
