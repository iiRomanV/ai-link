"""Тесты для core.dialog."""

from pathlib import Path
from core.dialog import (
    ROLE_USER, ROLE_LINK, ROLE_SYSTEM,
    DialogMessage, DialogSession,
    create_session, add_message, save_message, save_session,
    load_messages, clear_dialog, count_messages,
)


def test_message_auto_ts():
    m = DialogMessage(role=ROLE_USER, text="привет")
    assert m.ts != ""
    assert m.kind == "text"
    assert m.meta == {}


def test_session_auto_created_at(tmp_path):
    s = create_session(str(tmp_path))
    assert s.created_at != ""
    assert s.messages == []
    assert s.project_dir == str(tmp_path)


def test_add_message():
    s = create_session("/tmp/x")
    msg = add_message(s, ROLE_USER, "привет")
    assert len(s.messages) == 1
    assert s.messages[0].role == ROLE_USER
    assert s.messages[0].text == "привет"
    assert msg is s.messages[0]


def test_add_message_with_kind_and_meta():
    s = create_session("/tmp/x")
    msg = add_message(s, ROLE_LINK, "уточни X", kind="question",
                      meta={"phase": "plan"})
    assert msg.kind == "question"
    assert msg.meta["phase"] == "plan"


def test_save_and_load_one(tmp_path):
    clear_dialog(str(tmp_path))
    msg = DialogMessage(role=ROLE_USER, text="тест")
    save_message(str(tmp_path), msg)
    loaded = load_messages(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].role == ROLE_USER
    assert loaded[0].text == "тест"


def test_save_session_avoids_duplicates(tmp_path):
    clear_dialog(str(tmp_path))
    s = create_session(str(tmp_path))
    add_message(s, ROLE_USER, "первое")
    add_message(s, ROLE_LINK, "второе")
    save_session(str(tmp_path), s)
    # Повторный save не должен дублировать
    save_session(str(tmp_path), s)
    loaded = load_messages(str(tmp_path))
    assert len(loaded) == 2


def test_save_session_then_more(tmp_path):
    clear_dialog(str(tmp_path))
    s = create_session(str(tmp_path))
    add_message(s, ROLE_USER, "A")
    save_session(str(tmp_path), s)
    add_message(s, ROLE_LINK, "B")
    save_session(str(tmp_path), s)
    loaded = load_messages(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[0].text == "A"
    assert loaded[1].text == "B"


def test_load_messages_limit(tmp_path):
    clear_dialog(str(tmp_path))
    for i in range(10):
        save_message(str(tmp_path),
                     DialogMessage(role=ROLE_USER, text=f"msg {i}"))
    loaded = load_messages(str(tmp_path), limit=3)
    assert len(loaded) == 3
    assert loaded[0].text == "msg 7"
    assert loaded[-1].text == "msg 9"


def test_load_messages_empty(tmp_path):
    clear_dialog(str(tmp_path))
    assert load_messages(str(tmp_path)) == []


def test_load_broken_line_skipped(tmp_path):
    path = tmp_path / ".ai_link" / "dialog.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"role": "user", "text": "ok", "ts": "2026-09-14T10:00:00"}\n'
        'невалидный json\n'
        '{"role": "link", "text": "ответ", "ts": "2026-09-14T10:00:01"}\n',
        encoding="utf-8",
    )
    loaded = load_messages(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[0].text == "ok"
    assert loaded[1].text == "ответ"


def test_clear_dialog(tmp_path):
    save_message(str(tmp_path),
                 DialogMessage(role=ROLE_USER, text="X"))
    assert count_messages(str(tmp_path)) == 1
    clear_dialog(str(tmp_path))
    assert count_messages(str(tmp_path)) == 0


def test_count_messages_empty(tmp_path):
    clear_dialog(str(tmp_path))
    assert count_messages(str(tmp_path)) == 0


def test_load_sets_saved_flag(tmp_path):
    clear_dialog(str(tmp_path))
    save_message(str(tmp_path),
                 DialogMessage(role=ROLE_USER, text="X"))
    loaded = load_messages(str(tmp_path))
    assert getattr(loaded[0], "_saved", False) is True
