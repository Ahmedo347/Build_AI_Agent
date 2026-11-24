"""Automated eval harness for the AI agent.

This module runs evaluation tasks in-process using the agent's run_agent()
function, providing isolation, checkpointing, and detailed reporting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from main import run_agent
from permissions import ALLOW_ALL
from ratelimit import RateLimiter
from settings import Settings

EVALS_DIR = Path(__file__).parent
TASKS_FILE = EVALS_DIR / "tasks.json"
RESULTS_DIR = EVALS_DIR / "results"
SCRATCH_ROOT = Path(".scratch")


@dataclass
class Task:
    id: str
    category: str
    prompt: str
    expected_substrings: list[str] | None
    verify_cmd: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=data["id"],
            category=data["category"],
            prompt=data["prompt"],
            expected_substrings=data.get("expected_substrings"),
            verify_cmd=data.get("verify_cmd"),
        )


@dataclass
class TaskResult:
    task_id: str
    category: str
    status: str  # "pass" | "fail" | "error" | "skipped"
    turns: int
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    rate_limit_wait: float = 0.0
    drift: bool = False
    notes: str = ""
    final_message: str | None = None
    error: str | None = None
    models_seen: list[str] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)

    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalRun:
    requested_model: str
    actual_models: list[str] = field(default_factory=list)
    tasks_total: int = 0
    tasks_passed: int = 0
    tasks_failed: int = 0
    tasks_error: int = 0
    tasks_skipped: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_latency_s: float = 0.0
    total_rate_limit_wait: float = 0.0
    results: list[TaskResult] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    finished_at: str | None = None

    @property
    def pass_rate(self) -> float:
        if self.tasks_total == 0:
            return 0.0
        return (self.tasks_passed / self.tasks_total) * 100

    @property
    def avg_turns(self) -> float:
        if self.tasks_total == 0:
            return 0.0
        return sum(r.turns for r in self.results) / self.tasks_total

    @property
    def error_rate(self) -> float:
        if self.tasks_total == 0:
            return 0.0
        return ((self.tasks_failed + self.tasks_error) / self.tasks_total) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "actual_models": self.actual_models,
            "tasks_total": self.tasks_total,
            "tasks_passed": self.tasks_passed,
            "tasks_failed": self.tasks_failed,
            "tasks_error": self.tasks_error,
            "tasks_skipped": self.tasks_skipped,
            "pass_rate": round(self.pass_rate, 2),
            "avg_turns": round(self.avg_turns, 2),
            "error_rate": round(self.error_rate, 2),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_latency_s": round(self.total_latency_s, 2),
            "total_rate_limit_wait": round(self.total_rate_limit_wait, 2),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [r.to_dict() for r in self.results],
        }


def load_tasks(tasks_file: Path) -> tuple[str, list[Task]]:
    data = json.loads(tasks_file.read_text(encoding="utf-8"))
    model = data.get("model", "gemini-3.5-flash-lite")
    tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
    return model, tasks


def load_checkpoint(results_file: Path) -> dict[str, TaskResult]:
    if not results_file.exists():
        return {}
    data = json.loads(results_file.read_text(encoding="utf-8"))
    return {item["task_id"]: TaskResult(**item) for item in data.get("results", [])}


def save_checkpoint(results_file: Path, run: EvalRun) -> None:
    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def verify_task(task: Task, work_dir: Path) -> tuple[bool, str]:
    """Run verification for a task. Returns (passed, notes)."""
    if task.verify_cmd is None:
        if task.expected_substrings is None:
            return True, "no verification required"
        return True, "no verify_cmd; will check expected_substrings"

    try:
        result = subprocess.run(
            task.verify_cmd,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "PYTHONPATH": str(work_dir)},
        )
        if result.returncode == 0:
            return True, "verify_cmd exited 0"
        return (
            False,
            f"verify_cmd failed (exit {result.returncode}): {result.stderr.strip()}",
        )
    except subprocess.TimeoutExpired:
        return False, "verify_cmd timed out after 30s"
    except OSError as exc:
        return False, f"verify_cmd error: {type(exc).__name__}: {exc}"


def check_substrings(
    message: str | None, expected: list[str] | None
) -> tuple[bool, str]:
    if not expected:
        return True, ""
    if message is None:
        return False, "no final message from agent"
    missing = [s for s in expected if s.lower() not in message.lower()]
    if missing:
        return False, f"missing expected substrings: {missing}"
    return True, f"all {len(expected)} substrings present"


def run_single_task(
    task: Task,
    client,
    settings: Settings,
    limiter: RateLimiter,
    dry_run: bool,
) -> TaskResult:
    """Execute a single evaluation task in an isolated temporary directory."""
    start_time = time.perf_counter()

    if dry_run:
        return TaskResult(
            task_id=task.id,
            category=task.category,
            status="skipped",
            turns=0,
            prompt_tokens=0,
            completion_tokens=0,
            latency_s=0.0,
            rate_limit_wait=0.0,
            drift=False,
            notes="dry-run mode",
            tool_counts={},
        )

    with tempfile.TemporaryDirectory(
        dir=str(SCRATCH_ROOT), prefix=f"eval_{task.id}_"
    ) as tmpdir:
        work_dir = Path(tmpdir) / "calculator"
        shutil.copytree(settings.working_directory, work_dir)

        result = run_agent(
            client,
            task.prompt,
            work_dir,
            model=settings.model,
            max_iterations=settings.max_iterations,
            max_tokens=settings.max_tokens,
            limiter=limiter,
            policy=ALLOW_ALL,
            verbose=False,
        )

        latency = time.perf_counter() - start_time

        drift = any(m != settings.model for m in result.models_seen)

        if result.error:
            return TaskResult(
                task_id=task.id,
                category=task.category,
                status="error",
                turns=result.turns,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_s=latency,
                rate_limit_wait=result.rate_limit_wait,
                drift=drift,
                notes=result.error,
                error=result.error,
                models_seen=result.models_seen,
                tool_counts=result.tool_counts,
            )

        verify_passed, verify_notes = verify_task(task, work_dir)
        substring_passed, substring_notes = check_substrings(
            result.final_message, task.expected_substrings
        )

        passed = verify_passed and substring_passed
        notes_parts = []
        if verify_notes:
            notes_parts.append(f"verify: {verify_notes}")
        if substring_notes:
            notes_parts.append(f"content: {substring_notes}")
        notes = "; ".join(notes_parts)

        return TaskResult(
            task_id=task.id,
            category=task.category,
            status="pass" if passed else "fail",
            turns=result.turns,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_s=latency,
            rate_limit_wait=result.rate_limit_wait,
            drift=drift,
            notes=notes,
            final_message=result.final_message,
            models_seen=result.models_seen,
            tool_counts=result.tool_counts,
        )


def generate_markdown_report(run: EvalRun, output_path: Path) -> str:
    lines = []
    lines.append(f"# Eval Report: {run.requested_model}")
    lines.append("")
    lines.append(f"**Date:** {run.started_at}")
    lines.append(f"**Requested Model:** {run.requested_model}")
    lines.append(
        f"**Actual Models Seen:** {', '.join(run.actual_models) if run.actual_models else 'N/A'}"
    )
    lines.append(f"**Total Tasks:** {run.tasks_total}")
    lines.append(
        f"**Passed:** {run.tasks_passed} | **Failed:** {run.tasks_failed} | **Errors:** {run.tasks_error} | **Skipped:** {run.tasks_skipped}"
    )
    lines.append(f"**Pass Rate:** {run.pass_rate:.1f}%")
    lines.append(f"**Average Turns:** {run.avg_turns:.1f}")
    lines.append(f"**Total Prompt Tokens:** {run.total_prompt_tokens:,}")
    lines.append(f"**Total Completion Tokens:** {run.total_completion_tokens:,}")
    lines.append(
        f"**Total Tokens:** {run.total_prompt_tokens + run.total_completion_tokens:,}"
    )
    lines.append(f"**Total Latency:** {run.total_latency_s:.1f}s")
    lines.append(f"**Total Rate Limit Wait:** {run.total_rate_limit_wait:.1f}s")
    lines.append("")
    lines.append("## Per-Task Results")
    lines.append("")
    lines.append(
        "| ID | Category | Status | Turns | Tokens | Latency (s) | Drift | Notes |"
    )
    lines.append(
        "|----|----------|--------|-------|--------|-------------|-------|-------|"
    )

    for r in run.results:
        status_emoji = {"pass": "✅", "fail": "❌", "error": "⚠️", "skipped": "⏭️"}.get(
            r.status, "❓"
        )
        tokens = r.total_tokens()
        drift = "⚠️" if r.drift else ""
        notes = r.notes.replace("|", "\\|") if r.notes else ""
        lines.append(
            f"| {r.task_id} | {r.category} | {status_emoji} {r.status} | {r.turns} | {tokens:,} | {r.latency_s:.1f} | {drift} | {notes} |"
        )

    lines.append("")
    md = "\n".join(lines)
    output_path.write_text(md, encoding="utf-8")
    return md
