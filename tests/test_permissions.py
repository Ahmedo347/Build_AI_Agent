"""The deterministic gate: who may do what, and what a refusal looks like."""

from __future__ import annotations

from pathlib import Path

import pytest

import main
from functions.call_function import function_map
from permissions import (
    ALLOW_ALL,
    DRY_RUN,
    MUTATING_TOOLS,
    NON_INTERACTIVE,
    READ_ONLY_TOOLS,
    Decision,
    InteractivePolicy,
    PermissionRequest,
    StaticPolicy,
    authorize,
    build_policy,
    refusal_message,
    stdin_is_tty,
)

REPO = Path("/repo")


def request(tool_name: str, **arguments) -> PermissionRequest:
    return PermissionRequest(
        tool_name=tool_name, arguments=arguments, working_directory=REPO
    )


def write_request(**overrides) -> PermissionRequest:
    arguments = {"file_path": "pkg/calc.py", "content": "VALUE = 42\n"}
    arguments.update(overrides)
    return request("write_file", **arguments)


class RecordingPolicy:
    name = "recording"

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.calls: list[PermissionRequest] = []

    def __call__(self, request: PermissionRequest) -> Decision:
        self.calls.append(request)
        return self.decision


# --- classification -------------------------------------------------------


def test_every_registered_tool_is_classified():
    """Adding a tool to the registry without classifying it must fail loudly."""

    assert len(function_map) == 6
    assert set(function_map) == READ_ONLY_TOOLS | MUTATING_TOOLS


def test_read_only_tools_are_never_prompted():
    policy = RecordingPolicy(Decision.deny("should not be asked"))

    for name in sorted(READ_ONLY_TOOLS):
        assert authorize(request(name, directory=".", query="x"), policy).allowed

    assert policy.calls == []


def test_read_only_tools_are_allowed_without_any_policy():
    assert authorize(request("get_files_info", directory="."), None).allowed


def test_mutating_tools_consult_the_policy():
    policy = RecordingPolicy(Decision.allow())

    decision = authorize(write_request(), policy)

    assert decision.allowed
    assert len(policy.calls) == 1
    assert policy.calls[0].tool_name == "write_file"


@pytest.mark.parametrize("name", sorted(MUTATING_TOOLS))
def test_no_policy_fails_closed(name):
    decision = authorize(request(name, file_path="x.py"), None)

    assert not decision.allowed
    assert "no approval policy" in decision.message
    assert "No changes were made" in decision.message


def test_policy_exception_fails_closed():
    def broken(request: PermissionRequest) -> Decision:
        raise RuntimeError("boom")

    decision = authorize(write_request(), broken)

    assert not decision.allowed
    assert "approval check failed" in decision.message
    assert "RuntimeError" in decision.message


def test_policy_returning_garbage_fails_closed():
    decision = authorize(write_request(), lambda request: True)  # type: ignore[arg-type]

    assert not decision.allowed
    assert "not a decision" in decision.message


def test_unknown_tool_is_not_treated_as_mutating():
    """Dispatch reports unknown tools; there is nothing to execute."""

    assert authorize(request("rm_rf", file_path="/"), None).allowed


# --- request description ---------------------------------------------------


def test_describe_shows_tool_path_and_size():
    described = write_request().describe()

    assert described.startswith("write_file (")
    assert "path: pkg/calc.py" in described
    assert "11 chars" in described


def test_describe_shows_interpreter_arguments():
    described = request(
        "run_python_file", file_path="main.py", args=["3 + 5", "-v"]
    ).describe()

    assert "path: main.py" in described
    assert "args: ['3 + 5', '-v']" in described


def test_describe_survives_missing_path():
    assert "path: (unspecified path)" in request("write_file").describe()


def test_describe_uses_the_singular_for_one_character():
    assert "1 char)" in write_request(content="x").describe()


def test_describe_does_not_leak_file_content():
    described = write_request(content="SECRET_TOKEN=abc").describe()

    assert "SECRET_TOKEN" not in described
    assert "16 chars" in described


# --- built-in policies -----------------------------------------------------


def test_allow_all_allows_mutations():
    assert ALLOW_ALL(write_request()).allowed


def test_dry_run_refuses_and_explains_the_mode():
    decision = DRY_RUN(write_request())

    assert not decision.allowed
    assert "dry-run" in decision.message
    assert "pkg/calc.py" in decision.message
    assert "No changes were made" in decision.message


def test_non_interactive_refuses_with_a_way_out():
    decision = NON_INTERACTIVE(write_request())

    assert not decision.allowed
    assert "not a terminal" in decision.message
    assert "--yes" in decision.message


def test_static_policy_template_receives_the_action():
    policy = StaticPolicy("custom", template="nope: {action}")

    assert policy(write_request()).message.startswith("nope: write_file (")


# --- interactive policy ----------------------------------------------------


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " y ", "Yes\n"])
def test_interactive_accepts_explicit_yes(answer):
    policy = InteractivePolicy(input_fn=lambda prompt: answer)

    assert policy(write_request()).allowed


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe", "yeah", "1", "0"])
def test_interactive_everything_else_is_a_no(answer):
    policy = InteractivePolicy(input_fn=lambda prompt: answer)

    decision = policy(write_request())

    assert not decision.allowed
    assert "the user declined" in decision.message
    assert "No changes were made" in decision.message


def test_interactive_prompt_names_the_tool_and_path():
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    InteractivePolicy(input_fn=fake_input)(write_request())

    assert len(prompts) == 1
    assert "write_file" in prompts[0]
    assert "pkg/calc.py" in prompts[0]
    assert "[y/N]" in prompts[0]


def test_interactive_eof_fails_closed():
    def fake_input(prompt: str) -> str:
        raise EOFError

    decision = InteractivePolicy(input_fn=fake_input)(write_request())

    assert not decision.allowed
    assert "end of input" in decision.message


# --- policy selection ------------------------------------------------------


def test_dry_run_outranks_yes():
    policy = build_policy(auto_approve=True, dry_run=True, is_tty=True)

    assert policy is DRY_RUN


def test_yes_wins_over_a_missing_tty():
    policy = build_policy(auto_approve=True, dry_run=False, is_tty=False)

    assert policy is ALLOW_ALL


def test_no_tty_without_yes_fails_closed():
    policy = build_policy(auto_approve=False, dry_run=False, is_tty=False)

    assert policy is NON_INTERACTIVE


def test_tty_without_yes_asks():
    policy = build_policy(
        auto_approve=False, dry_run=False, is_tty=True, input_fn=lambda prompt: "n"
    )

    assert policy.name == "interactive"
    assert not policy(write_request()).allowed


def test_flags_reach_the_policy():
    assert main.parse_args(["do it"]).yes is False
    assert main.parse_args(["do it"]).dry_run is False
    assert main.parse_args(["do it", "--yes"]).yes is True
    assert main.parse_args(["do it", "-y"]).yes is True
    assert main.parse_args(["do it", "--dry-run"]).dry_run is True


class FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_stdin_is_tty_reads_the_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(True))
    assert stdin_is_tty() is True

    monkeypatch.setattr("sys.stdin", FakeStdin(False))
    assert stdin_is_tty() is False


def test_stdin_is_tty_handles_none_and_broken_streams(monkeypatch):
    monkeypatch.setattr("sys.stdin", None)
    assert stdin_is_tty() is False

    class Broken:
        def isatty(self) -> bool:
            raise ValueError("detached")

    monkeypatch.setattr("sys.stdin", Broken())
    assert stdin_is_tty() is False


# --- refusal message -------------------------------------------------------


def test_refusal_message_keeps_the_tool_call_id():
    message = refusal_message("call_7", Decision.deny("Permission denied. No changes."))

    assert message == {
        "role": "tool",
        "tool_call_id": "call_7",
        "content": "Permission denied. No changes.",
    }
