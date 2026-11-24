#!/usr/bin/env python3
"""Run the automated eval harness.

Usage:
  python -m evals.run_eval --help
  python -m evals.run_eval                    # run all tasks, save to baseline
  python -m evals.run_eval --resume           # continue from checkpoint
  python -m evals.run_eval --dry-run          # validate tasks without LLM calls
  python -m evals.run_eval --only fix-01,fix-02
  python -m evals.run_eval --limit 5
  python -m evals.run_eval --model gemini-3.5-flash --base-url https://...
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from evals.harness import (
    EvalRun,
    generate_markdown_report,
    load_checkpoint,
    load_tasks,
    run_single_task,
    save_checkpoint,
)
from main import create_client, parse_args
from ratelimit import RateLimiter
from settings import Settings

SCRATCH_ROOT = Path(".scratch")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run automated evaluation tasks against the AI agent"
    )
    parser.add_argument(
        "--tasks",
        default="evals/tasks.json",
        help="Path to tasks JSON file (default: evals/tasks.json)",
    )
    parser.add_argument(
        "--output",
        default="evals/results/baseline.md",
        help="Output Markdown report path (default: evals/results/baseline.md)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of tasks to run",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated list of task IDs to run (e.g., fix-01,fix-02)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint, skipping already completed tasks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate tasks and fixture copying without calling the LLM",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model (default: from tasks.json or LLM_MODEL)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override base URL (default: from LLM_BASE_URL)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=None,
        help="Minimum seconds between requests (default: LLM_MIN_INTERVAL or 4.0)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    load_dotenv()

    SCRATCH_ROOT.mkdir(exist_ok=True)

    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        print(f"Error: tasks file not found: {tasks_path}", file=sys.stderr)
        return 1

    requested_model, all_tasks = load_tasks(tasks_path)

    if args.model:
        requested_model = args.model

    if requested_model.startswith("auto") and not args.dry_run:
        print(
            f"Error: Model pinning violation — '{requested_model}' starts with 'auto'. "
            f"Use an exact model ID (e.g., gemini-3.5-flash-lite) or run with --dry-run.",
            file=sys.stderr,
        )
        return 1

    cli_args = parse_args(
        [
            "dummy prompt",
            "--model",
            requested_model or "",
            *(["--base-url", args.base_url] if args.base_url else []),
            *(["--min-interval", str(args.min_interval)] if args.min_interval else []),
            "--working-directory",
            "calculator",
        ]
    )
    settings = Settings.from_env(cli_args)

    if args.only:
        only_ids = {s.strip() for s in args.only.split(",")}
        all_tasks = [t for t in all_tasks if t.id in only_ids]
        if len(all_tasks) != len(only_ids):
            missing = only_ids - {t.id for t in all_tasks}
            print(f"Warning: task IDs not found: {missing}", file=sys.stderr)

    if args.limit is not None:
        all_tasks = all_tasks[: args.limit]

    if not all_tasks:
        print("No tasks to run", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            f"[dry-run] Would run {len(all_tasks)} tasks with model={requested_model}"
        )
        for task in all_tasks:
            print(f"  - {task.id} [{task.category}]: {task.prompt[:80]}...")
            if task.verify_cmd:
                print(f"    verify: {task.verify_cmd}")
        print("[dry-run] Validation passed — no LLM calls made")
        return 0

    results_path = Path(args.output).with_suffix(".json")
    checkpoint = load_checkpoint(results_path) if args.resume else {}

    print(f"Running {len(all_tasks)} tasks with model={requested_model}")
    print(f"Checkpoint file: {results_path}")

    client = create_client(settings)
    limiter = RateLimiter(settings.min_interval)

    run = EvalRun(requested_model=requested_model)
    run.tasks_total = len(all_tasks)

    completed_from_checkpoint = 0
    for task in all_tasks:
        if task.id in checkpoint:
            prev = checkpoint[task.id]
            run.results.append(prev)
            run.tasks_passed += 1 if prev.status == "pass" else 0
            run.tasks_failed += 1 if prev.status == "fail" else 0
            run.tasks_error += 1 if prev.status == "error" else 0
            run.tasks_skipped += 1 if prev.status == "skipped" else 0
            run.total_prompt_tokens += prev.prompt_tokens
            run.total_completion_tokens += prev.completion_tokens
            run.total_latency_s += prev.latency_s
            run.actual_models.extend(prev.models_seen)
            completed_from_checkpoint += 1
            print(f"  [resume] Skipping {task.id} (already {prev.status})")
            continue

        print(f"  Running {task.id} [{task.category}]...")
        start = time.perf_counter()
        result = run_single_task(task, client, settings, limiter, dry_run=False)
        elapsed = time.perf_counter() - start

        run.results.append(result)
        run.tasks_passed += 1 if result.status == "pass" else 0
        run.tasks_failed += 1 if result.status == "fail" else 0
        run.tasks_error += 1 if result.status == "error" else 0
        run.tasks_skipped += 1 if result.status == "skipped" else 0
        run.total_prompt_tokens += result.prompt_tokens
        run.total_completion_tokens += result.completion_tokens
        run.total_latency_s += elapsed
        run.total_rate_limit_wait += result.latency_s
        run.actual_models.extend(result.models_seen)

        save_checkpoint(results_path, run)

        status_emoji = {"pass": "✅", "fail": "❌", "error": "⚠️", "skipped": "⏭️"}.get(
            result.status, "❓"
        )
        drift_marker = " ⚠️ DRIFT" if result.drift else ""
        print(
            f"    {status_emoji} {result.status.upper()} ({result.turns} turns, "
            f"{result.total_tokens()} tokens, {elapsed:.1f}s){drift_marker}"
        )
        if result.notes:
            print(f"      Notes: {result.notes}")

    run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run.actual_models = list(dict.fromkeys(run.actual_models))

    output_path = Path(args.output)
    generate_markdown_report(run, output_path)
    save_checkpoint(results_path, run)

    print()
    print(f"Results saved to: {output_path}")
    print(f"Checkpoint saved to: {results_path}")
    print()
    print(
        f"Summary: {run.tasks_passed}/{run.tasks_total} passed "
        f"({run.pass_rate:.1f}%), "
        f"avg turns: {run.avg_turns:.1f}, "
        f"total tokens: {run.total_prompt_tokens + run.total_completion_tokens:,}"
    )

    if run.tasks_failed > 0 or run.tasks_error > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
