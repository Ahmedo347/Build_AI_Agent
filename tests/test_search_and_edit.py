"""Behaviour of the v2 search/edit tools, and their place in the gate.

Every test builds its own working directory. The security tests matter most:
a match from outside the sandbox, or from ``.env``, is a leak.
"""

from __future__ import annotations

import pytest

import functions.search_files as search_module
from functions.edit_file import edit_file, schema_edit_file
from functions.search_files import schema_search_files, search_files
from permissions import (
    ALLOW_ALL,
    DRY_RUN,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    PermissionRequest,
    authorize,
)

# --- search_files: matching ------------------------------------------------


def test_search_finds_literal_matches(repo):
    output = search_files(str(repo), "VALUE")

    assert "pkg/calc.py:1: VALUE = 41" in output


def test_search_is_case_sensitive_by_default(repo):
    assert search_files(str(repo), "value").startswith("No matches")
    assert "pkg/calc.py:1" in search_files(str(repo), "VALUE")


def test_search_can_ignore_case(repo):
    output = search_files(str(repo), "value", case_sensitive=False)

    assert "pkg/calc.py:1: VALUE = 41" in output


def test_search_supports_regex(repo):
    output = search_files(str(repo), r"VALUE\s*=\s*\d+", regex=True)

    assert "pkg/calc.py:1: VALUE = 41" in output


def test_invalid_regex_is_reported(repo):
    output = search_files(str(repo), "VALUE(", regex=True)

    assert output.startswith("Error: invalid regular expression")


def test_search_reports_each_matching_line(repo):
    (repo / "multi.py").write_text(
        "a = 1\ntarget = 2\nb = 3\ntarget = 4\n", encoding="utf-8"
    )

    output = search_files(str(repo), "target", glob="multi.py")

    assert "multi.py:2: target = 2" in output
    assert "multi.py:4: target = 4" in output


def test_search_glob_filters_files(repo):
    (repo / "pkg" / "notes.py").write_text("VALUE = 99\n", encoding="utf-8")

    py_output = search_files(str(repo), "VALUE", glob="*.py")
    txt_output = search_files(str(repo), "VALUE", glob="*.txt")

    assert "pkg/calc.py" in py_output
    assert "pkg/notes.py" in py_output
    assert txt_output.startswith("No matches")


def test_search_directory_scopes_the_walk(repo):
    output = search_files(str(repo), "VALUE", directory="pkg")

    assert "pkg/calc.py:1" in output


def test_empty_query_is_rejected(repo):
    assert "non-empty" in search_files(str(repo), "")


def test_search_reports_a_missing_directory(repo):
    assert "is not a directory" in search_files(str(repo), "x", directory="nope")


# --- search_files: output caps --------------------------------------------


def test_search_truncates_at_the_match_cap(repo, monkeypatch):
    monkeypatch.setattr(search_module, "MAX_MATCHES", 2)
    (repo / "many.txt").write_text("\n".join("hit" for _ in range(10)), "utf-8")

    output = search_files(str(repo), "hit", glob="many.txt")

    assert output.count("many.txt:") == 2
    assert "truncated at 2 matches" in output


def test_search_truncates_at_the_character_cap(repo, monkeypatch):
    monkeypatch.setattr(search_module, "MAX_CHARS", 100)
    (repo / "many.txt").write_text("\n".join("hit" for _ in range(10)), "utf-8")

    output = search_files(str(repo), "hit", glob="many.txt")

    assert "truncated" in output


def test_search_truncates_a_long_matched_line(repo):
    (repo / "long.txt").write_text("x" * 1000 + "needle\n", encoding="utf-8")

    output = search_files(str(repo), "needle", glob="long.txt")

    first_line = output.splitlines()[0]
    assert "…" in first_line
    assert len(first_line) < 400


# --- search_files: sandbox and secrets ------------------------------------


def test_search_refuses_a_directory_outside_the_root(repo):
    output = search_files(str(repo), "x", directory="/bin")

    assert "outside the permitted working directory" in output


def test_search_skips_symlinks_that_escape_the_root(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("API_KEY=supersecretvalue\n", encoding="utf-8")
    (repo / "escape").symlink_to(outside)
    (repo / "file_link.txt").symlink_to(outside / "secret.txt")

    output = search_files(str(repo), "API_KEY")

    assert "supersecretvalue" not in output
    assert "escape/" not in output
    assert "file_link.txt" not in output


def test_search_never_reads_denied_secret_files(repo):
    (repo / ".env").write_text("API_KEY=supersecretvalue\n", encoding="utf-8")
    (repo / "config.txt").write_text("API_KEY=public\n", encoding="utf-8")

    output = search_files(str(repo), "API_KEY")

    assert "config.txt:1: API_KEY=public" in output
    assert "supersecretvalue" not in output
    assert ".env" not in output


def test_search_skips_binary_files(repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01findme\x00")

    output = search_files(str(repo), "findme")

    assert "blob.bin" not in output


# --- edit_file: happy path -------------------------------------------------


def test_edit_replaces_one_occurrence(repo):
    output = edit_file(str(repo), "pkg/calc.py", "VALUE = 41", "VALUE = 42")

    assert "Successfully edited" in output
    assert "pkg/calc.py" in output
    assert "1 replacement" in output
    assert "chars" in output
    assert (repo / "pkg" / "calc.py").read_text(encoding="utf-8") == "VALUE = 42\n"


def test_edit_can_insert_and_delete_text(repo):
    edit_file(str(repo), "notes.txt", "café", "thé")
    edit_file(str(repo), "notes.txt", " — ", " ")

    assert (repo / "notes.txt").read_text(encoding="utf-8") == "thé 日本語\n"


def test_edit_message_does_not_echo_content(repo):
    (repo / "secret.py").write_text("token = 'oldsecret'\n", encoding="utf-8")

    output = edit_file(str(repo), "secret.py", "oldsecret", "newsecret")

    assert "oldsecret" not in output
    assert "newsecret" not in output


# --- edit_file: uniqueness guard ------------------------------------------


def test_edit_missing_text_changes_nothing(repo):
    before = (repo / "pkg" / "calc.py").read_text(encoding="utf-8")

    output = edit_file(str(repo), "pkg/calc.py", "MISSING", "x")

    assert "was not found" in output
    assert "No changes were made" in output
    assert (repo / "pkg" / "calc.py").read_text(encoding="utf-8") == before


def test_edit_ambiguous_text_changes_nothing(repo):
    (repo / "dup.py").write_text("same\nsame\n", encoding="utf-8")
    before = (repo / "dup.py").read_text(encoding="utf-8")

    output = edit_file(str(repo), "dup.py", "same", "other")

    assert "appears 2 times" in output
    assert "No changes were made" in output
    assert (repo / "dup.py").read_text(encoding="utf-8") == before


def test_edit_empty_old_text_is_rejected(repo):
    before = (repo / "notes.txt").read_text(encoding="utf-8")

    output = edit_file(str(repo), "notes.txt", "", "x")

    assert "must not be empty" in output
    assert (repo / "notes.txt").read_text(encoding="utf-8") == before


# --- edit_file: sandbox ----------------------------------------------------


def test_edit_refuses_paths_outside_the_root(repo):
    output = edit_file(str(repo), "../escape.txt", "a", "b")

    assert "outside the permitted working directory" in output
    assert not (repo.parent / "escape.txt").exists()


def test_edit_refuses_denied_secret_files(repo):
    (repo / ".env").write_text("KEY=value\n", encoding="utf-8")

    output = edit_file(str(repo), ".env", "value", "stolen")

    assert "protected path" in output
    assert (repo / ".env").read_text(encoding="utf-8") == "KEY=value\n"


def test_edit_reports_a_missing_file(repo):
    output = edit_file(str(repo), "nope.py", "a", "b")

    assert "not found" in output
    assert "write_file" in output


def test_edit_refuses_a_directory(repo):
    output = edit_file(str(repo), "pkg", "a", "b")

    assert "not a regular file" in output


# --- registry and gate -----------------------------------------------------


def test_new_tools_are_classified():
    assert "search_files" in READ_ONLY_TOOLS
    assert "edit_file" in MUTATING_TOOLS


def test_search_needs_no_policy(repo):
    request = PermissionRequest(
        tool_name="search_files", arguments={"query": "x"}, working_directory=repo
    )

    assert authorize(request, None).allowed


def test_edit_is_gated_by_the_policy(repo):
    request = PermissionRequest(
        tool_name="edit_file",
        arguments={"file_path": "pkg/calc.py", "old_text": "a", "new_text": "b"},
        working_directory=repo,
    )

    assert not authorize(request, None).allowed
    assert authorize(request, ALLOW_ALL).allowed
    assert not authorize(request, DRY_RUN).allowed


def test_edit_permission_prompt_shows_sizes_not_content(repo):
    request = PermissionRequest(
        tool_name="edit_file",
        arguments={
            "file_path": "x.py",
            "old_text": "SECRET_OLD",
            "new_text": "SECRET_NEW",
        },
        working_directory=repo,
    )

    described = request.describe()

    assert "edit_file" in described
    assert "path: x.py" in described
    assert "10 -> 10 chars" in described
    assert "SECRET_OLD" not in described
    assert "SECRET_NEW" not in described


# --- schemas stay Gemini-compatible ---------------------------------------


def _iter_schema_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _iter_schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_schema_keys(item)


@pytest.mark.parametrize("schema", [schema_search_files, schema_edit_file])
def test_schema_avoids_keywords_gemini_rejects(schema):
    keys = set(_iter_schema_keys(schema))

    assert "additionalProperties" not in keys
    assert "default" not in keys
