"""Behaviour of the four sandboxed tools.

Each test builds its own working directory under ``tmp_path``; nothing here
reads or writes the real project tree.
"""

from __future__ import annotations

import pytest

import functions.run_python_file as run_python_module
from functions.get_files_info import MAX_CHARS, get_file_content, get_files_info
from functions.run_python_file import MAX_OUTPUT_CHARS, run_python_file
from functions.write_file import write_file

# --- get_files_info -------------------------------------------------------


def test_lists_entries_sorted_with_sizes(repo):
    output = get_files_info(str(repo))

    lines = output.splitlines()
    names = [line.removeprefix("- ").split(":")[0] for line in lines]

    assert names == sorted(names)
    assert names == ["main.py", "notes.txt", "pkg"]
    assert "- main.py: file_size=" in output
    assert "is_dir=True" in output
    assert "is_dir=False" in output


def test_lists_a_subdirectory(repo):
    output = get_files_info(str(repo), "pkg")

    assert "- calc.py:" in output


def test_missing_directory_is_reported(repo):
    output = get_files_info(str(repo), "nope")

    assert output.startswith('Error: "nope" is not a directory')


def test_listing_a_file_is_reported(repo):
    output = get_files_info(str(repo), "notes.txt")

    assert "is not a directory" in output


def test_listing_outside_the_root_is_refused(repo):
    output = get_files_info(str(repo), "/bin")

    assert "outside the permitted working directory" in output


# --- get_file_content -----------------------------------------------------


def test_reads_utf8_without_mangling(repo):
    assert get_file_content(str(repo), "notes.txt") == "café — 日本語\n"


def test_missing_file_is_reported(repo):
    output = get_file_content(str(repo), "nope.txt")

    assert "not found" in output


def test_directory_is_not_a_file(repo):
    output = get_file_content(str(repo), "pkg")

    assert "not a regular file" in output


def test_large_file_is_truncated(repo):
    big = repo / "big.txt"
    big.write_text("x" * (MAX_CHARS + 500), encoding="utf-8")

    output = get_file_content(str(repo), "big.txt")

    assert output.startswith("x" * 100)
    assert "truncated at" in output
    assert len(output) < MAX_CHARS + 200


def test_file_exactly_at_the_limit_is_not_truncated(repo):
    exact = repo / "exact.txt"
    exact.write_text("y" * MAX_CHARS, encoding="utf-8")

    output = get_file_content(str(repo), "exact.txt")

    assert "truncated" not in output
    assert len(output) == MAX_CHARS


def test_secret_files_are_refused(repo):
    (repo / ".env").write_text("LLM_API_KEY=real-key\n", encoding="utf-8")

    output = get_file_content(str(repo), ".env")

    assert "protected path" in output
    assert "real-key" not in output


# --- write_file -----------------------------------------------------------


def test_writes_a_file(repo):
    output = write_file(str(repo), "new.txt", "hello")

    assert "Successfully wrote" in output
    assert (repo / "new.txt").read_text(encoding="utf-8") == "hello"


def test_creates_missing_parent_directories(repo):
    write_file(str(repo), "a/b/c.txt", "deep")

    assert (repo / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "deep"


def test_overwrites_and_reports_length(repo):
    output = write_file(str(repo), "notes.txt", "café — 日本語")

    assert "10 characters written" in output
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "café — 日本語"


def test_refuses_to_write_to_a_directory(repo):
    output = write_file(str(repo), "pkg", "x")

    assert "as it is a directory" in output


def test_refuses_to_write_outside_the_root(repo):
    output = write_file(str(repo), "../escape.txt", "x")

    assert "outside the permitted working directory" in output
    assert not (repo.parent / "escape.txt").exists()


def test_refuses_to_overwrite_secrets(repo):
    output = write_file(str(repo), ".env", "LLM_API_KEY=stolen")

    assert "protected path" in output


# --- run_python_file ------------------------------------------------------


def test_runs_a_script_with_arguments(repo):
    output = run_python_file(str(repo), "main.py", ["3 + 5"])

    assert "STDOUT:" in output
    assert "hello 3 + 5" in output


def test_reports_non_zero_exit_and_stderr(repo):
    (repo / "boom.py").write_text(
        "import sys\nsys.stderr.write('kaput\\n')\nsys.exit(3)\n", encoding="utf-8"
    )

    output = run_python_file(str(repo), "boom.py")

    assert "Process exited with code 3" in output
    assert "STDERR:" in output
    assert "kaput" in output


def test_reports_silent_success(repo):
    (repo / "quiet.py").write_text("pass\n", encoding="utf-8")

    output = run_python_file(str(repo), "quiet.py")

    assert output == "No output produced"


def test_rejects_non_python_files(repo):
    output = run_python_file(str(repo), "notes.txt")

    assert "is not a Python file" in output


def test_reports_missing_files(repo):
    output = run_python_file(str(repo), "nope.py")

    assert "does not exist" in output


def test_refuses_to_run_outside_the_root(repo):
    output = run_python_file(str(repo), "../../etc/passwd.py")

    assert "outside the permitted working directory" in output


def test_times_out(repo, monkeypatch):
    monkeypatch.setattr(run_python_module, "TIMEOUT_SECONDS", 0.5)
    (repo / "slow.py").write_text("import time\ntime.sleep(5)\n", encoding="utf-8")

    output = run_python_file(str(repo), "slow.py")

    assert "timed out" in output


def test_truncates_large_output(repo, monkeypatch):
    monkeypatch.setattr(run_python_module, "MAX_OUTPUT_CHARS", 100)
    (repo / "loud.py").write_text("print('z' * 500)\n", encoding="utf-8")

    output = run_python_file(str(repo), "loud.py")

    assert "output truncated" in output
    assert len(output) <= MAX_OUTPUT_CHARS + 200


def test_child_process_does_not_see_secrets(repo, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super-secret")
    (repo / "peek.py").write_text(
        "import os\nprint(os.environ.get('MY_API_KEY', 'MISSING'))\n",
        encoding="utf-8",
    )

    output = run_python_file(str(repo), "peek.py")

    assert "MISSING" in output
    assert "super-secret" not in output


@pytest.mark.parametrize("args", [None, []])
def test_no_arguments_is_equivalent_to_empty_list(repo, args):
    output = run_python_file(str(repo), "main.py", args)

    assert "hello" in output
