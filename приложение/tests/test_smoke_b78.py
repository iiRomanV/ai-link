"""Б78 (16.10.2026): smoke_test не обнуляет DISPLAY для GUI."""


def test_env_for_run_does_not_reset_display():
    """DISPLAY не пустой для GUI."""
    import os
    from core.smoke_test import _env_for_run
    os.environ["DISPLAY"] = ":0"
    env = _env_for_run("gui")
    assert env.get("DISPLAY") != ""


def test_env_for_run_gui_sets_display():
    """GUI-режим оставляет DISPLAY валидным."""
    from core.smoke_test import _env_for_run
    env = _env_for_run("gui")
    disp = env.get("DISPLAY", "")
    assert disp and disp != ""
