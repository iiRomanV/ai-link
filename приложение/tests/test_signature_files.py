"""SIGNATURE-FROM-FILES (2026-10-01): signature из changed_files."""
from core.orchestrator import _signature_from_files


def test_empty_list_returns_empty():
    assert _signature_from_files([]) == ""
    assert _signature_from_files(None) == ""


def test_single_file():
    """gui/main_window.py → main-window."""
    assert _signature_from_files(["gui/main_window.py"]) == "main-window"


def test_multiple_files():
    """Два файла → main-window-export."""
    files = ["gui/main_window.py", "smoke_tracker/export.py"]
    assert _signature_from_files(files) == "main-window-export"


def test_max_three_files():
    """Больше 3 файлов — берём первые 3."""
    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    assert _signature_from_files(files) == "a-b-c"


def test_underscore_to_dash():
    """snake_case → kebab-case."""
    assert _signature_from_files(["smoke_tracker.py"]) == "smoke-tracker"


def test_skip_common_names():
    """__init__ и test — пропускаются."""
    files = ["__init__.py", "main.py", "test.py", "real.py"]
    assert _signature_from_files(files) == "main-real"


def test_deduplicate():
    """Один файл дважды — не дублируется."""
    files = ["gui/main_window.py", "gui/main_window.py", "export.py"]
    assert _signature_from_files(files) == "main-window-export"


def test_skip_empty_entries():
    """Пустые строки не ломают."""
    files = ["", "gui/main_window.py", None, "export.py"]
    assert _signature_from_files(files) == "main-window-export"


def test_long_signature_truncated():
    """Длинная сигнатура обрезается до 60 символов."""
    files = ["a_very_long_filename_that_should_be_truncated_when_used_as_signature.py"]
    result = _signature_from_files(files)
    assert len(result) <= 60


def test_no_extension():
    """Файл без расширения тоже работает."""
    assert _signature_from_files(["Makefile"]) == "makefile"
