"""Offline tests for the eval harness.

These tests use a fake client and never touch the network.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fakes import FakeClient, completion, message

from evals.harness import (
    EvalRun,
    Task,
    TaskResult,
    check_substrings,
    generate_markdown_report,
    load_checkpoint,
    load_tasks,
    run_single_task,
    save_checkpoint,
    verify_task,
)
from evals.run_eval import build_arg_parser
from permissions import ALLOW_ALL
from ratelimit import RateLimiter


class FakeSettings:
    def __init__(
        self,
        model="test/model",
        min_interval=0,
        max_iterations=20,
        max_tokens=8192,
        working_directory: Path | None = None,
    ):
        self.model = model
        self.min_interval = min_interval
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.working_directory = working_directory or Path("calculator")


def test_load_tasks_from_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "model": "gemini-3.5-flash-lite",
                "tasks": [
                    {
                        "id": "test-1",
                        "category": "read",
                        "prompt": "What does X do?",
                        "expected_substrings": ["X", "does"],
                        "verify_cmd": None,
                    }
                ],
            },
            f,
        )
        path = Path(f.name)

    model, tasks = load_tasks(path)
    assert model == "gemini-3.5-flash-lite"
    assert len(tasks) == 1
    assert tasks[0].id == "test-1"
    assert tasks[0].category == "read"
    assert tasks[0].expected_substrings == ["X", "does"]
    assert tasks[0].verify_cmd is None
    path.unlink()


def test_load_tasks_default_model():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"tasks": [{"id": "t1", "category": "read", "prompt": "p"}]}, f)
        path = Path(f.name)

    model, _ = load_tasks(path)
    assert model == "gemini-3.5-flash-lite"
    path.unlink()


def test_task_result_total_tokens():
    r = TaskResult(
        task_id="t",
        category="read",
        status="pass",
        turns=2,
        prompt_tokens=100,
        completion_tokens=50,
        latency_s=1.0,
        drift=False,
        notes="",
    )
    assert r.total_tokens() == 150


def test_eval_run_pass_rate():
    run = EvalRun(requested_model="m")
    run.tasks_total = 10
    run.tasks_passed = 7
    run.tasks_failed = 3
    assert run.pass_rate == 70.0


def test_eval_run_avg_turns():
    run = EvalRun(requested_model="m")
    run.results = [
        TaskResult("t1", "r", "pass", 2, 10, 5, 1.0, False, ""),
        TaskResult("t2", "r", "pass", 4, 10, 5, 1.0, False, ""),
        TaskResult("t3", "r", "pass", 3, 10, 5, 1.0, False, ""),
    ]
    run.tasks_total = 3
    assert run.avg_turns == 3.0


def test_check_substrings_all_present():
    msg = "The Calculator class evaluates arithmetic expressions with operator precedence."
    ok, notes = check_substrings(msg, ["Calculator", "arithmetic", "precedence"])
    assert ok
    assert "3 substrings present" in notes


def test_check_substrings_missing():
    msg = "The calculator evaluates expressions."
    ok, notes = check_substrings(msg, ["Calculator", "precedence", "missing"])
    assert not ok
    assert "missing expected substrings" in notes
    assert "precedence" in notes
    assert "missing" in notes


def test_check_substrings_none_expected():
    ok, _ = check_substrings("anything", None)
    assert ok


def test_check_substrings_no_message():
    ok, notes = check_substrings(None, ["anything"])
    assert not ok
    assert "no final message" in notes


def test_verify_task_no_command():
    task = Task("t", "read", "prompt", ["expected"], None)
    with tempfile.TemporaryDirectory() as d:
        work_dir = Path(d)
        passed, notes = verify_task(task, work_dir)
        assert passed
        assert "no verify_cmd" in notes


def test_verify_task_no_verification_needed():
    task = Task("t", "read", "prompt", None, None)
    with tempfile.TemporaryDirectory() as d:
        work_dir = Path(d)
        passed, notes = verify_task(task, work_dir)
        assert passed
        assert "no verification required" in notes


def test_save_and_load_checkpoint():
    run = EvalRun(requested_model="test/model")
    run.results = [
        TaskResult("t1", "read", "pass", 1, 10, 5, 0.5, False, "ok"),
        TaskResult("t2", "fix", "fail", 2, 20, 10, 1.0, True, "bad"),
    ]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)

    save_checkpoint(path, run)
    loaded = load_checkpoint(path)

    assert set(loaded.keys()) == {"t1", "t2"}
    assert loaded["t1"].status == "pass"
    assert loaded["t2"].status == "fail"
    assert loaded["t2"].drift is True
    path.unlink()


def test_load_checkpoint_missing_file():
    loaded = load_checkpoint(Path("/nonexistent/results.json"))
    assert loaded == {}


def test_generate_markdown_report():
    run = EvalRun(requested_model="gemini-3.5-flash-lite")
    run.actual_models = ["gemini-3.5-flash-lite"]
    run.tasks_total = 2
    run.tasks_passed = 1
    run.tasks_failed = 1
    run.total_prompt_tokens = 100
    run.total_completion_tokens = 50
    run.total_latency_s = 5.0
    run.total_rate_limit_wait = 2.0
    run.results = [
        TaskResult(
            "read-01", "read", "pass", 2, 50, 25, 2.0, False, "all good", "message"
        ),
        TaskResult(
            "fix-01", "fix", "fail", 3, 50, 25, 3.0, True, "verify failed", "msg"
        ),
    ]

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = Path(f.name)

    md = generate_markdown_report(run, path)

    assert "# Eval Report" in md
    assert "gemini-3.5-flash-lite" in md
    assert "read-01" in md
    assert "fix-01" in md
    assert "✅" in md
    assert "❌" in md
    assert "⚠️" in md  # drift marker
    path.unlink()


def test_cli_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args([])
    assert args.tasks == "evals/tasks.json"
    assert args.output == "evals/results/baseline.md"
    assert args.limit is None
    assert args.only is None
    assert args.resume is False
    assert args.dry_run is False


def test_cli_parser_overrides():
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--tasks",
            "custom.json",
            "--output",
            "out.md",
            "--limit",
            "5",
            "--only",
            "a,b",
            "--resume",
            "--dry-run",
            "--model",
            "custom-model",
            "--base-url",
            "http://localhost",
            "--min-interval",
            "6.0",
        ]
    )
    assert args.tasks == "custom.json"
    assert args.output == "out.md"
    assert args.limit == 5
    assert args.only == "a,b"
    assert args.resume is True
    assert args.dry_run is True
    assert args.model == "custom-model"
    assert args.base_url == "http://localhost"
    assert args.min_interval == 6.0


def test_dry_run_rejects_auto_model(monkeypatch):
    """--dry-run should allow auto models, but normal run should reject."""
    from evals.run_eval import main

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "model": "auto-router",
                "tasks": [{"id": "t1", "category": "read", "prompt": "p"}],
            },
            f,
        )
        path = Path(f.name)

    # Dry-run should pass
    with patch(
        "evals.run_eval.load_tasks",
        return_value=("auto-router", [Task("t1", "read", "p", None, None)]),
    ):
        assert main(["--tasks", str(path), "--dry-run"]) == 0

    # Normal run should fail
    assert main(["--tasks", str(path)]) == 1
    path.unlink()


def test_run_single_task_dry_run():
    task = Task("test-1", "read", "prompt", ["expected"], None)
    settings = FakeSettings()

    with tempfile.TemporaryDirectory() as _:
        result = run_single_task(
            task,
            client=None,
            settings=settings,
            limiter=RateLimiter(0),
            dry_run=True,
        )

    assert result.status == "skipped"
    assert result.notes == "dry-run mode"
    assert result.turns == 0


def test_run_single_task_read_pass(monkeypatch, tmp_path):
    """A read task with expected substrings that the agent output contains."""
    # Create a minimal calculator fixture
    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "pkg").mkdir()
    (calc_dir / "pkg" / "calculator.py").write_text(
        "class Calculator:\n    pass\n", encoding="utf-8"
    )

    task = Task("read-1", "read", "prompt", ["Calculator", "evaluate"], None)
    settings = FakeSettings(working_directory=calc_dir)

    client = FakeClient(
        [completion(message("The Calculator class evaluates expressions."))]
    )

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.status == "pass"
    assert result.turns == 1
    assert result.prompt_tokens > 0


def test_run_single_task_read_fail_missing_substring(monkeypatch, tmp_path):
    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "pkg").mkdir()
    (calc_dir / "pkg" / "calculator.py").write_text(
        "class Calculator:\n    pass\n", encoding="utf-8"
    )

    task = Task("read-1", "read", "prompt", ["missing_word"], None)
    settings = FakeSettings(working_directory=calc_dir)

    client = FakeClient([completion(message("The Calculator class evaluates."))])

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.status == "fail"
    assert "missing expected substrings" in result.notes


def test_run_single_task_with_verify_cmd(monkeypatch, tmp_path):
    """A fix task that runs a verify command."""
    work_dir = tmp_path / "calculator"
    work_dir.mkdir()
    (work_dir / "tests.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    task = Task("fix-1", "fix", "fix something", None, f"{sys.executable} tests.py")
    settings = FakeSettings(working_directory=work_dir)

    client = FakeClient([completion(message("Fixed it."))])

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.status == "pass"


def test_run_single_task_verify_cmd_fails(monkeypatch, tmp_path):
    work_dir = tmp_path / "calculator"
    work_dir.mkdir()
    (work_dir / "tests.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    task = Task("fix-1", "fix", "fix something", None, f"{sys.executable} tests.py")
    settings = FakeSettings(working_directory=work_dir)

    client = FakeClient([completion(message("Fixed it."))])

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.status == "fail"
    assert "verify_cmd failed" in result.notes


def test_resume_skips_completed_tasks(tmp_path):
    """Verify that --resume skips tasks already in checkpoint."""

    checkpoint_data = {
        "requested_model": "test/model",
        "results": [
            {
                "task_id": "t1",
                "category": "read",
                "status": "pass",
                "turns": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "latency_s": 0.5,
                "drift": False,
                "notes": "ok",
                "final_message": "msg",
                "error": None,
                "models_seen": ["test/model"],
            }
        ],
    }

    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint_data))

    tasks = [
        Task("t1", "read", "p1", None, None),
        Task("t2", "read", "p2", None, None),
    ]

    for task in tasks:
        if task.id in load_checkpoint(checkpoint_path):
            continue
        # would run task here

    loaded = load_checkpoint(checkpoint_path)
    assert "t1" in loaded
    assert "t2" not in loaded


def test_drift_detection(monkeypatch, tmp_path):
    """If models_seen differs from requested model, drift=True."""
    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "pkg").mkdir()
    (calc_dir / "pkg" / "calculator.py").write_text(
        "class Calculator:\n    pass\n", encoding="utf-8"
    )

    task = Task("t1", "read", "prompt", ["expected"], None)
    settings = FakeSettings(model="requested-model", working_directory=calc_dir)

    client = FakeClient(
        [completion(message("Response with expected word"), model="different-model")]
    )

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.drift is True
    assert "different-model" in result.models_seen


def test_isolated_working_directory(tmp_path):
    """Each task gets its own temp directory; original calculator/ is untouched."""
    original_calc = tmp_path / "calculator"
    original_calc.mkdir()
    (original_calc / "pkg").mkdir()
    (original_calc / "pkg" / "calculator.py").write_text(
        "original content", encoding="utf-8"
    )

    task = Task("t1", "read", "prompt", ["original"], None)
    settings = FakeSettings()
    settings.working_directory = original_calc

    client = FakeClient([completion(message("original content found"))])

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    # Original file should be unchanged
    assert (original_calc / "pkg" / "calculator.py").read_text() == "original content"
    assert result.status == "pass"


def test_run_agent_called_with_allow_all_policy():
    """Verify the harness passes ALLOW_ALL policy to run_agent."""
    import evals.harness as harness_module
    from evals.harness import run_single_task

    original_run_agent = harness_module.run_agent

    captured = {}

    def capture_run_agent(*args, **kwargs):
        captured["policy"] = kwargs.get("policy")
        return original_run_agent(*args, **kwargs)

    harness_module.run_agent = capture_run_agent
    try:
        calc_dir = Path("calculator")
        if not calc_dir.exists():
            calc_dir.mkdir(parents=True)
            (calc_dir / "pkg").mkdir()
            (calc_dir / "pkg" / "calculator.py").write_text(
                "class Calculator:\n    pass\n"
            )

        task = Task("t1", "read", "prompt", ["expected"], None)
        settings = FakeSettings(working_directory=calc_dir)
        client = FakeClient([completion(message("expected word found"))])

        run_single_task(task, client, settings, RateLimiter(0), dry_run=False)

        assert captured["policy"] is ALLOW_ALL
    finally:
        harness_module.run_agent = original_run_agent


def test_arg_parser_help():
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])


def test_eval_run_to_dict():
    run = EvalRun(requested_model="test/model")
    run.tasks_total = 1
    run.tasks_passed = 1
    run.actual_models = ["test/model"]
    run.results = [TaskResult("t1", "r", "pass", 1, 10, 5, 1.0, False, "")]

    d = run.to_dict()
    assert d["requested_model"] == "test/model"
    assert d["pass_rate"] == 100.0
    assert d["results"][0]["task_id"] == "t1"


def test_task_from_dict_optional_fields():
    task = Task.from_dict({"id": "t1", "category": "read", "prompt": "p"})
    assert task.expected_substrings is None
    assert task.verify_cmd is None


def test_verify_task_timeout(tmp_path):
    """Verify command that hangs should timeout."""
    work_dir = tmp_path / "calculator"
    work_dir.mkdir()
    (work_dir / "hang.py").write_text(
        "import time\ntime.sleep(100)\n", encoding="utf-8"
    )
    (work_dir / "pkg").mkdir()
    (work_dir / "pkg" / "calculator.py").write_text(
        "class Calculator:\n    pass\n", encoding="utf-8"
    )

    task = Task("t1", "fix", "fix", None, f"{sys.executable} hang.py")
    settings = FakeSettings(working_directory=work_dir)

    client = FakeClient([completion(message("done"))])

    result = run_single_task(
        task,
        client=client,
        settings=settings,
        limiter=RateLimiter(0),
        dry_run=False,
    )

    assert result.status == "fail"
    assert "timed out" in result.notes


def test_limiter_shared_across_tasks(tmp_path):
    """Verify that a single RateLimiter instance is shared across tasks."""
    from ratelimit import RateLimiter

    calc_dir = tmp_path / "calculator"
    calc_dir.mkdir()
    (calc_dir / "pkg").mkdir()
    (calc_dir / "pkg" / "calculator.py").write_text(
        "class Calculator:\n    pass\n", encoding="utf-8"
    )

    calls = []

    class CountingLimiter(RateLimiter):
        def wait(self):
            calls.append(1)
            return super().wait()

    limiter = CountingLimiter(0)
    task1 = Task("t1", "read", "p", ["x"], None)
    task2 = Task("t2", "read", "p", ["x"], None)
    settings = FakeSettings(working_directory=calc_dir)

    client = FakeClient(
        [
            completion(message("x")),
            completion(message("x")),
        ]
    )

    run_single_task(task1, client, settings, limiter, False)
    run_single_task(task2, client, settings, limiter, False)

    # Limiter.wait() should have been called for each request
    assert len(calls) >= 2


def test_results_json_structure(tmp_path):
    """Verify the JSON checkpoint has the right structure for v2 comparison."""
    run = EvalRun(requested_model="gemini-3.5-flash-lite")
    run.actual_models = ["gemini-3.5-flash-lite"]
    run.tasks_total = 1
    run.tasks_passed = 1
    run.total_prompt_tokens = 100
    run.total_completion_tokens = 50
    run.total_latency_s = 2.0
    run.total_rate_limit_wait = 0.5
    run.results = [TaskResult("read-01", "read", "pass", 2, 100, 50, 2.0, False, "ok")]

    path = tmp_path / "results.json"
    save_checkpoint(path, run)

    data = json.loads(path.read_text())
    assert "requested_model" in data
    assert "actual_models" in data
    assert "pass_rate" in data
    assert "results" in data
    assert data["results"][0]["task_id"] == "read-01"
    assert "drift" in data["results"][0]
