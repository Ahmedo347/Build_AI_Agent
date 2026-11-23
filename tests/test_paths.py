"""Path containment and the secret deny-list.

These are the security tests. If any of them fails, the agent can reach outside
its working directory or read its own credentials.
"""

from __future__ import annotations

import pytest

from functions.paths import DENIED_FILE_NAMES, denied_reason, resolve_path


def test_normal_path_resolves_inside_root(repo):
    target, error = resolve_path(str(repo), "pkg/calc.py", action="read")

    assert error is None
    assert target == (repo / "pkg" / "calc.py").resolve()


def test_dotdot_escape_is_rejected(repo):
    target, error = resolve_path(str(repo), "../outside.txt", action="read")

    assert target is None
    assert "outside the permitted working directory" in error


def test_deep_dotdot_escape_is_rejected(repo):
    target, error = resolve_path(str(repo), "pkg/../../outside.txt", action="read")

    assert target is None
    assert "outside" in error


def test_absolute_path_outside_is_rejected(repo):
    target, error = resolve_path(str(repo), "/etc/passwd", action="read")

    assert target is None
    assert "outside" in error


def test_symlink_cannot_be_used_to_escape(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (repo / "escape").symlink_to(outside)

    target, error = resolve_path(str(repo), "escape/secret.txt", action="read")

    assert target is None
    assert "outside" in error


def test_error_message_names_the_action(repo):
    _, error = resolve_path(str(repo), "../x", action="execute")

    assert "Cannot execute" in error


def test_invalid_working_directory_does_not_raise():
    target, error = resolve_path("\0bad", "x")

    assert target is None
    assert "Invalid working directory" in error


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        ".env.local",
        ".git/config",
        ".ssh/id_rsa",
        ".aws/credentials",
        "id_rsa",
        "id_ed25519",
        "certs/server.pem",
        "secrets/client.key",
        ".netrc",
        ".pgpass",
    ],
)
def test_protected_paths_are_denied(repo, path):
    target, error = resolve_path(str(repo), path, action="read")

    assert target is None
    assert "protected path" in error


@pytest.mark.parametrize("name", sorted(DENIED_FILE_NAMES))
def test_every_denied_file_name_is_denied(repo, name):
    _, error = resolve_path(str(repo), name, action="read")

    assert error is not None
    assert "protected path" in error


def test_env_example_is_not_a_secret(repo):
    target, error = resolve_path(str(repo), ".env.example", action="read")

    assert error is None
    assert target == (repo / ".env.example").resolve()


def test_denied_reason_is_none_for_safe_paths(repo):
    assert denied_reason((repo / "notes.txt").resolve(), repo.resolve()) is None


def test_denied_reason_flags_outside_paths(repo, tmp_path):
    outside = tmp_path / "elsewhere.txt"

    assert "outside" in denied_reason(outside.resolve(), repo.resolve())


def test_nested_denied_directory_is_denied(repo):
    target, error = resolve_path(str(repo), "pkg/.git/HEAD", action="read")

    assert target is None
    assert "protected path" in error
