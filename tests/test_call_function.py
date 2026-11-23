"""Tool dispatch: every failure mode must come back as a ``tool`` message."""

from __future__ import annotations

from fakes import tool_call

import functions.call_function as dispatch
from functions.call_function import call_function


def test_successful_call_returns_matching_tool_message(repo):
    result = call_function(tool_call("get_files_info", {"directory": "."}), str(repo))

    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert "- notes.txt:" in result["content"]


def test_unknown_function_is_reported(repo):
    result = call_function(tool_call("delete_everything", {}), str(repo))

    assert "Unknown function: delete_everything" in result["content"]


def test_invalid_json_is_reported(repo):
    result = call_function(tool_call("get_files_info", "{not json"), str(repo))

    assert "could not parse arguments" in result["content"]


def test_non_object_arguments_are_reported(repo):
    result = call_function(tool_call("get_files_info", "[1, 2]"), str(repo))

    assert "must be a JSON object" in result["content"]


def test_missing_argument_is_reported_not_raised(repo):
    result = call_function(tool_call("write_file", {"file_path": "x.txt"}), str(repo))

    assert "bad arguments for write_file" in result["content"]


def test_unexpected_tool_exception_is_reported(repo, monkeypatch):
    def explode(working_directory, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(dispatch.function_map, "explode", explode)
    result = call_function(tool_call("explode", {}), str(repo))

    assert "explode failed: RuntimeError: boom" in result["content"]


def test_empty_tool_output_is_replaced(repo, monkeypatch):
    monkeypatch.setitem(
        dispatch.function_map, "silent", lambda working_directory, **kwargs: ""
    )
    result = call_function(tool_call("silent", {}), str(repo))

    assert result["content"] == "(no output)"


def test_no_tool_call_id_ever_goes_unanswered(repo):
    """A 400 from the provider is worse than an invented id."""

    for name, args in [
        ("get_files_info", {"directory": "."}),
        ("nope", {}),
        ("get_files_info", "{"),
    ]:
        result = call_function(tool_call(name, args, id=""), str(repo))

        assert result["tool_call_id"] == ""
        assert result["content"]


def test_verbose_names_the_arguments(repo, capsys):
    call_function(
        tool_call("get_files_info", {"directory": "pkg"}), str(repo), verbose=True
    )

    assert "get_files_info({'directory': 'pkg'})" in capsys.readouterr().out
