"""The agent loop, driven by a scripted client.

These tests describe the contract the eval harness depends on: a ``RunResult``
with tokens, turns and tool-call counts, and no exceptions escaping for
expected provider failures.
"""

from __future__ import annotations

from types import SimpleNamespace

from fakes import (
    FakeClient,
    completion,
    connection_error,
    message,
    status_error,
    tool_call,
)

import main
from functions.call_function import available_functions
from permissions import ALLOW_ALL, DRY_RUN, InteractivePolicy
from ratelimit import RateLimiter


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_final_answer_without_tool_calls(repo):
    client = FakeClient([completion(message("all done"))])

    result = main.run_agent(client, "hi", repo, model="m")

    assert result.ok
    assert result.exit_code == 0
    assert result.turns == 1
    assert result.tool_calls == 0
    assert result.final_message == "all done"
    assert (result.prompt_tokens, result.completion_tokens) == (10, 5)
    assert result.models_seen == ["fake/model"]
    assert result.error is None


def test_request_shape(repo):
    client = FakeClient([completion(message("ok"))])

    main.run_agent(client, "hi", repo, model="chosen-model", max_tokens=123)

    sent = client.calls[0]
    assert sent["model"] == "chosen-model"
    assert sent["tools"] == available_functions
    assert sent["temperature"] == 0
    assert sent["max_tokens"] == 123
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1] == {"role": "user", "content": "hi"}


def test_tool_call_is_answered_and_looped(repo):
    client = FakeClient(
        [
            completion(
                message(tool_calls=[tool_call("get_files_info", {"directory": "."})]),
                finish_reason="tool_calls",
            ),
            completion(message("three entries")),
        ]
    )

    result = main.run_agent(client, "list files", repo, model="m")

    assert result.ok
    assert result.turns == 2
    assert result.tool_calls == 1
    assert result.final_message == "three entries"

    tool_message = client.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert "- notes.txt:" in tool_message["content"]


def test_parallel_tool_calls_are_all_answered(repo):
    client = FakeClient(
        [
            completion(
                message(
                    tool_calls=[
                        tool_call("get_files_info", {"directory": "."}, id="a"),
                        tool_call(
                            "get_file_content", {"file_path": "notes.txt"}, id="b"
                        ),
                    ]
                ),
                finish_reason="tool_calls",
            ),
            completion(message("done")),
        ]
    )

    result = main.run_agent(client, "look", repo, model="m")

    assert result.tool_calls == 2
    answered = {
        item["tool_call_id"]
        for item in client.calls[1]["messages"]
        if isinstance(item, dict) and item.get("role") == "tool"
    }
    assert answered == {"a", "b"}


def test_missing_tool_call_id_is_generated(repo, capsys):
    blank = tool_call("get_files_info", {"directory": "."}, id="")
    client = FakeClient(
        [
            completion(message(tool_calls=[blank]), finish_reason="tool_calls"),
            completion(message("ok")),
        ]
    )

    result = main.run_agent(client, "list", repo, model="m")

    tool_message = client.calls[1]["messages"][-1]
    assert result.ok
    assert tool_message["tool_call_id"]
    assert blank.id == tool_message["tool_call_id"]


def test_failed_tool_still_produces_a_valid_turn(repo):
    client = FakeClient(
        [
            completion(
                message(tool_calls=[tool_call("nope", {})]), finish_reason="tool_calls"
            ),
            completion(message("recovered")),
        ]
    )

    result = main.run_agent(client, "do something odd", repo, model="m")

    assert result.ok
    assert result.final_message == "recovered"
    assert "Unknown function: nope" in client.calls[1]["messages"][-1]["content"]


def test_empty_content_gets_a_placeholder(repo):
    client = FakeClient([completion(message(None))])

    result = main.run_agent(client, "hi", repo, model="m")

    assert result.final_message == "(the model returned no content)"


def test_content_filter_is_reported_not_swallowed(repo):
    client = FakeClient([completion(message(None), finish_reason="content_filter")])

    result = main.run_agent(client, "something disallowed", repo, model="m")

    assert result.exit_code == 1
    assert "safety filter" in result.error
    assert result.final_message is None
    assert len(client.calls) == 1


def test_length_finish_reason_warns(repo, capsys):
    client = FakeClient([completion(message("cut off"), finish_reason="length")])

    result = main.run_agent(client, "write an essay", repo, model="m", max_tokens=64)

    assert result.final_message == "cut off"
    assert "token limit" in capsys.readouterr().out


def test_iteration_cap_is_enforced(repo):
    looping = completion(
        message(tool_calls=[tool_call("get_files_info", {"directory": "."})]),
        finish_reason="tool_calls",
    )
    client = FakeClient([looping, looping])

    result = main.run_agent(client, "loop forever", repo, model="m", max_iterations=2)

    assert result.exit_code == 1
    assert "Maximum number of iterations" in result.error


def test_api_status_error_becomes_a_result(repo):
    client = FakeClient([status_error(401)])

    result = main.run_agent(client, "hi", repo, model="m")

    assert result.exit_code == 1
    assert "401" in result.error
    assert not result.ok


def test_connection_errors_become_a_result(repo, monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    client = FakeClient([connection_error() for _ in range(main.MAX_RETRIES)])

    result = main.run_agent(client, "hi", repo, model="m")

    assert result.exit_code == 1
    assert "could not reach" in result.error


def test_rate_limit_wait_is_accounted(repo):
    clock = FakeClock()
    limiter = RateLimiter(4.0, clock=clock.clock, sleep=clock.sleep)
    client = FakeClient([completion(message("one")), completion(message("two"))])

    first = main.run_agent(client, "a", repo, model="m", limiter=limiter)
    second = main.run_agent(client, "b", repo, model="m", limiter=limiter)

    assert first.rate_limit_wait == 0.0
    assert second.rate_limit_wait == 4.0


def test_summary_reports_served_model_and_tokens(repo):
    client = FakeClient([completion(message("ok"), model="served-model")])
    settings = SimpleNamespace(model="requested-model")

    result = main.run_agent(client, "hi", repo, model="requested-model")
    summary = main.format_summary(settings, result)

    assert "model=requested-model" in summary
    assert "served=served-model" in summary
    assert "turns=1" in summary
    assert "prompt_tokens=10" in summary


# --- permission gate -------------------------------------------------------


def write_call(path: str = "out.txt", content: str = "hi", id: str = "call_1"):
    return tool_call("write_file", {"file_path": path, "content": content}, id=id)


def test_mutating_tool_without_policy_is_denied(repo):
    client = FakeClient(
        [
            completion(message(tool_calls=[write_call()]), finish_reason="tool_calls"),
            completion(message("understood")),
        ]
    )

    result = main.run_agent(client, "write a file", repo, model="m")

    assert result.denied_calls == 1
    assert result.tool_calls == 0
    assert not (repo / "out.txt").exists()
    assert result.final_message == "understood"

    tool_message = client.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert "No changes were made" in tool_message["content"]


def test_allow_all_policy_executes_the_write(repo):
    client = FakeClient(
        [
            completion(message(tool_calls=[write_call()]), finish_reason="tool_calls"),
            completion(message("written")),
        ]
    )

    result = main.run_agent(client, "write a file", repo, model="m", policy=ALLOW_ALL)

    assert result.tool_calls == 1
    assert result.denied_calls == 0
    assert (repo / "out.txt").read_text(encoding="utf-8") == "hi"


def test_dry_run_refuses_the_write_and_says_so(repo):
    client = FakeClient(
        [
            completion(message(tool_calls=[write_call()]), finish_reason="tool_calls"),
            completion(message("skipped")),
        ]
    )

    result = main.run_agent(client, "write a file", repo, model="m", policy=DRY_RUN)

    assert result.denied_calls == 1
    assert not (repo / "out.txt").exists()

    content = client.calls[1]["messages"][-1]["content"]
    assert "dry-run" in content
    assert "No changes were made" in content


def test_dry_run_refuses_execution(repo):
    (repo / "touch.py").write_text(
        "from pathlib import Path\nPath('created.txt').write_text('x')\n",
        encoding="utf-8",
    )
    client = FakeClient(
        [
            completion(
                message(
                    tool_calls=[tool_call("run_python_file", {"file_path": "touch.py"})]
                ),
                finish_reason="tool_calls",
            ),
            completion(message("skipped")),
        ]
    )

    result = main.run_agent(client, "run it", repo, model="m", policy=DRY_RUN)

    assert result.denied_calls == 1
    assert not (repo / "created.txt").exists()
    assert "dry-run" in client.calls[1]["messages"][-1]["content"]


def test_interactive_decline_is_a_denial_not_a_crash(repo):
    client = FakeClient(
        [
            completion(message(tool_calls=[write_call()]), finish_reason="tool_calls"),
            completion(message("ok, I will not")),
        ]
    )
    policy = InteractivePolicy(input_fn=lambda prompt: "n")

    result = main.run_agent(client, "write a file", repo, model="m", policy=policy)

    assert result.ok
    assert result.denied_calls == 1
    assert not (repo / "out.txt").exists()
    assert "the user declined" in client.calls[1]["messages"][-1]["content"]


def test_interactive_approval_executes(repo):
    client = FakeClient(
        [
            completion(message(tool_calls=[write_call()]), finish_reason="tool_calls"),
            completion(message("done")),
        ]
    )
    policy = InteractivePolicy(input_fn=lambda prompt: "y")

    result = main.run_agent(client, "write a file", repo, model="m", policy=policy)

    assert result.tool_calls == 1
    assert (repo / "out.txt").read_text(encoding="utf-8") == "hi"


def test_read_only_tools_ignore_a_raising_policy(repo):
    def explode(request):
        raise AssertionError("a read-only tool must not ask for permission")

    client = FakeClient(
        [
            completion(
                message(tool_calls=[tool_call("get_files_info", {"directory": "."})]),
                finish_reason="tool_calls",
            ),
            completion(message("three entries")),
        ]
    )

    result = main.run_agent(client, "list", repo, model="m", policy=explode)

    assert result.tool_calls == 1
    assert result.denied_calls == 0
    assert "- notes.txt:" in client.calls[1]["messages"][-1]["content"]


def test_mixed_turn_answers_every_call_in_order(repo):
    client = FakeClient(
        [
            completion(
                message(
                    tool_calls=[
                        tool_call("get_files_info", {"directory": "."}, id="read"),
                        write_call(id="write"),
                    ]
                ),
                finish_reason="tool_calls",
            ),
            completion(message("done")),
        ]
    )

    result = main.run_agent(client, "look and write", repo, model="m")

    assert result.tool_calls == 1
    assert result.denied_calls == 1

    answers = [
        item
        for item in client.calls[1]["messages"]
        if isinstance(item, dict) and item.get("role") == "tool"
    ]
    assert [item["tool_call_id"] for item in answers] == ["read", "write"]
    assert "- notes.txt:" in answers[0]["content"]
    assert "No changes were made" in answers[1]["content"]


def test_summary_mentions_denials_only_when_they_happen(repo):
    clean = main.RunResult()
    denied = main.RunResult(denied_calls=2)
    settings = SimpleNamespace(model="m")

    assert "denied=" not in main.format_summary(settings, clean)
    assert "denied=2" in main.format_summary(settings, denied)
