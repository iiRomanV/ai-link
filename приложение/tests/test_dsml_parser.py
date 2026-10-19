"""Б75 (15.10.2026): парсер DSML и детект обрыва DeepSeek."""


def test_dsml_read_file_parsed():
    from core.response_validator import _try_parse_dsml
    text = (
        '<||DSML|| calls>\n'
        '<||DSML|| invoke name="read_file">\n'
        '<||DSML|| parameter name="path">foo.py</parameter>\n'
        '</||DSML|| invoke>\n'
        '</||DSML|| calls>'
    )
    result = _try_parse_dsml(text)
    assert result is not None
    import json
    d = json.loads(result)
    assert d["tool"] == "read_file"
    assert d["path"] == "foo.py"


def test_dsml_run_command_parsed():
    from core.response_validator import _try_parse_dsml
    text = (
        '<||DSML|| invoke name="run_command">\n'
        '<||DSML|| parameter name="command">ls -la</parameter>\n'
        '</||DSML|| invoke>'
    )
    result = _try_parse_dsml(text)
    import json
    d = json.loads(result)
    assert d["tool"] == "run_command"
    assert d["command"] == "ls -la"


def test_dsml_multiline_value():
    from core.response_validator import _try_parse_dsml
    text = (
        '<||DSML|| invoke name="replace_in_file">\n'
        '<||DSML|| parameter name="path">x.py</parameter>\n'
        '<||DSML|| parameter name="find">a = 1\nb = 2</parameter>\n'
        '<||DSML|| parameter name="replace">a = 999\nb = 2</parameter>\n'
        '</||DSML|| invoke>'
    )
    result = _try_parse_dsml(text)
    import json
    d = json.loads(result)
    assert d["tool"] == "replace_in_file"
    assert "a = 1" in d["find"]
    assert "999" in d["replace"]


def test_dsml_absent_returns_none():
    from core.response_validator import _try_parse_dsml
    assert _try_parse_dsml("just text") is None
    assert _try_parse_dsml('{"tool": "read_file"}') is None
    assert _try_parse_dsml("") is None


def test_strip_json_handles_dsml():
    from core.response_validator import _strip_json
    text = (
        'Server is temporarily unavailable.\n'
        '<||DSML|| invoke name="read_file">\n'
        '<||DSML|| parameter name="path">test.py</parameter>\n'
        '</||DSML|| invoke>'
    )
    result = _strip_json(text)
    assert result is not None
    import json
    d = json.loads(result)
    assert d["tool"] == "read_file"


def test_chat_limit_detects_server_unavailable():
    from core.agents import browser_manager
    markers = browser_manager._CHAT_LIMIT_MARKERS
    assert "server is temporarily unavailable" in markers


def test_dsml_real_format_with_string_attr():
    """Б75: реальный DSML от DeepSeek с string='true' и </||DSML|| parameter>."""
    from core.response_validator import _try_parse_dsml
    text = (
        '<||DSML|| calls>\n'
        '<||DSML|| invoke name="list_directory">\n'
        '<||DSML|| parameter name="path" string="true">MT5-GR</||DSML|| parameter>\n'
        '</||DSML|| invoke>\n'
        '</||DSML|| calls>'
    )
    result = _try_parse_dsml(text)
    assert result is not None
    import json
    d = json.loads(result)
    assert d["tool"] == "list_directory"
    assert d["path"] == "MT5-GR"


def test_strip_json_real_dsml():
    """Б75: _strip_json с реальным DSML."""
    from core.response_validator import _strip_json
    text = (
        'Server is temporarily unavailable.\n'
        '<||DSML|| invoke name="run_command">\n'
        '<||DSML|| parameter name="command" string="true">ls -la</||DSML|| parameter>\n'
        '</||DSML|| invoke>'
    )
    result = _strip_json(text)
    assert result is not None
    import json
    d = json.loads(result)
    assert d["tool"] == "run_command"
    assert d["command"] == "ls -la"
