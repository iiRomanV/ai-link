"""SIGNATURE-NO-FILES (2026-10-02): паттерн без changed_files
не создаётся."""


def test_signature_from_files_empty():
    from core.orchestrator import _signature_from_files
    assert _signature_from_files([]) == ""
    assert _signature_from_files(None) == ""


def test_signature_from_files_only_common():
    """Только __init__ / test → пусто."""
    from core.orchestrator import _signature_from_files
    assert _signature_from_files(["__init__.py"]) == ""
    assert _signature_from_files(["test.py"]) == ""


def test_signature_from_files_real():
    """Реальные файлы → sig."""
    from core.orchestrator import _signature_from_files
    assert _signature_from_files(["main.py"]) == "main"
    assert _signature_from_files(
        ["gui/main_window.py", "smoke_tracker/export.py"]
    ) == "main-window-export"
