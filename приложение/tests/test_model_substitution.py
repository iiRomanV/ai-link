"""Тесты MODEL-SUBSTITUTION."""

from core import model_substitution as ms


def test_get_chain_known_role():
    chain = ms.get_chain("coder")
    assert chain[0] == "browser_deepseek"
    assert "browser_qwen" in chain


def test_get_chain_unknown_role():
    assert ms.get_chain("nonsense") == []


def test_role_for_phase():
    assert ms.role_for_phase("plan") == "planner"
    assert ms.role_for_phase("code") == "coder"
    assert ms.role_for_phase("fix") == "coder"
    assert ms.role_for_phase("review") == "reviewer"
    assert ms.role_for_phase("unknown_phase") == "unknown_phase"


def test_next_in_chain():
    assert ms.next_in_chain("coder", "browser_deepseek") == "browser_qwen"
    assert ms.next_in_chain("coder", "browser_qwen") == "browser_perplexity"
    # хвост цепочки — пусто
    assert ms.next_in_chain("coder", "browser_perplexity") == ""
    # неизвестный агент — вернуть первый
    assert ms.next_in_chain("coder", "nonexistent") == "browser_deepseek"


def test_substitute():
    assert ms.substitute("planner", "browser_qwen") == "browser_deepseek"


def test_is_known():
    assert ms.is_known("browser_qwen")
    assert ms.is_known("ollama")
    assert not ms.is_known("nonsense_agent")


def test_try_chain_first_succeeds():
    calls = []
    def fn(agent):
        calls.append(agent)
        return {"success": True, "text": f"ok from {agent}"}
    r = ms.try_chain("coder", fn)
    assert r["success"] is True
    assert r["agent"] == "browser_deepseek"
    assert calls == ["browser_deepseek"]


def test_try_chain_falls_back():
    calls = []
    def fn(agent):
        calls.append(agent)
        if agent == "browser_deepseek":
            return {"success": False, "error": "boom"}
        return {"success": True, "text": "ok"}
    r = ms.try_chain("coder", fn)
    assert r["success"] is True
    assert r["agent"] == "browser_qwen"
    assert r["tried"] == ["browser_deepseek", "browser_qwen"]


def test_try_chain_all_fail():
    def fn(agent):
        return {"success": False, "error": f"fail {agent}"}
    r = ms.try_chain("brain", fn)
    assert r["success"] is False
    assert r["agent"] == ""
    assert r["tried"] == ["browser_qwen", "browser_deepseek"]
    assert "fail" in r["error"]


def test_try_chain_unknown_role():
    def fn(agent):
        return {"success": True}
    r = ms.try_chain("nonsense", fn)
    assert r["success"] is False
    assert "нет цепочки" in r["error"]


def test_try_chain_max_attempts():
    def fn(agent):
        return {"success": False, "error": "nope"}
    r = ms.try_chain("coder", fn, max_attempts=2)
    assert len(r["tried"]) == 2


def test_try_chain_call_fn_exception():
    def fn(agent):
        raise RuntimeError("broken")
    r = ms.try_chain("brain", fn, max_attempts=1)
    assert r["success"] is False
    assert "broken" in r["error"]


def test_try_chain_logs_substitution(tmp_path):
    from core.event_log import load_events, reset_log
    reset_log(str(tmp_path))
    def fn(agent):
        if agent == "browser_deepseek":
            return {"success": False, "error": "x"}
        return {"success": True}
    ms.try_chain("coder", fn, project_dir=str(tmp_path))
    events = load_events(str(tmp_path))
    subs = [e for e in events if e["event_type"] == "model_substituted"]
    assert len(subs) == 1
    assert subs[0]["payload_json"]["old"] == "browser_deepseek"
    assert subs[0]["payload_json"]["new"] == "browser_qwen"


def test_summary_for_llm():
    s = ms.summary_for_llm()
    assert "РОЛИ И АГЕНТЫ" in s
    assert "coder" in s
    assert "browser_deepseek" in s
