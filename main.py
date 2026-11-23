import argparse
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from functions.call_function import available_functions, call_function
from permissions import (
    PermissionRequest,
    Policy,
    authorize,
    build_policy,
    refusal_message,
    stdin_is_tty,
)
from prompts import system_prompt
from ratelimit import RateLimiter
from settings import Settings

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 4.0
MAX_BACKOFF_SECONDS = 60.0


@dataclass
class RunResult:
    """Everything a caller (CLI, eval harness, test) needs to know about a run."""

    exit_code: int = 0
    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    denied_calls: int = 0
    models_seen: list[str] = field(default_factory=list)
    rate_limit_wait: float = 0.0
    final_message: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A tool-calling AI coding agent confined to a working directory"
    )
    parser.add_argument("user_prompt", type=str, help="User prompt")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--working-directory",
        default="calculator",
        help="Directory the agent is allowed to read and write (default: calculator)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL (default: $LLM_BASE_URL or Google AI Studio)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id (default: $LLM_MODEL or gemini-3.5-flash-lite)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=None,
        help="Minimum seconds between model requests (default: $LLM_MIN_INTERVAL or 4.0)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Approve every write and execution without asking (for evals)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Refuse writes and code execution; read-only tools still work",
    )
    return parser.parse_args(argv)


def create_client(settings: Settings) -> OpenAI:
    if not settings.api_key:
        raise SystemExit(settings.missing_key_hint)

    # max_retries=0 on purpose: this module owns retries and backoff, and the
    # SDK's own retries would multiply with ours.
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.request_timeout,
        max_retries=0,
    )


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def request_completion(client: OpenAI, limiter: RateLimiter, **kwargs):
    """Call the API, waiting for the rate limit and retrying transient failures."""
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait()
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            if attempt == MAX_RETRIES:
                raise
            hinted = _retry_after_seconds(exc)
            delay = (
                hinted
                if hinted
                else min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
            )
            delay += random.uniform(0, 0.5)
            print(
                f"Transient API error ({type(exc).__name__}), "
                f"retrying in {delay:.1f}s (attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(delay)
        except APIStatusError as exc:
            if attempt == MAX_RETRIES or exc.status_code < 500:
                raise
            delay = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
            print(
                f"Server error {exc.status_code}, retrying in {delay:.1f}s "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(delay)

    raise RuntimeError("unreachable")


def _normalise_tool_call_ids(message, turn: int) -> None:
    """Fill in missing tool-call ids so the next turn's tool messages match."""
    for index, tool_call in enumerate(message.tool_calls or []):
        if not tool_call.id:
            try:
                tool_call.id = f"call_{turn}_{index}"
            except (AttributeError, ValueError):
                pass


def _tool_arguments(tool_call) -> dict[str, Any]:
    """Parse arguments for display only; ``call_function`` still re-parses them.

    A malformed payload becomes ``{}`` here so the approval prompt can be shown
    instead of crashing. The tool itself will then report the parse error.
    """
    try:
        parsed = json.loads(tool_call.function.arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_agent(
    client: OpenAI,
    prompt: str,
    working_directory: Path,
    *,
    model: str,
    max_iterations: int = 20,
    max_tokens: int = 8192,
    limiter: RateLimiter | None = None,
    policy: Policy | None = None,
    verbose: bool = False,
) -> RunResult:
    """Run the tool-calling loop. Never raises for expected API failures.

    ``policy`` gates ``write_file`` and ``run_python_file``. ``None`` means
    deny: the loop never calls ``input()`` itself, so a caller that forgets a
    policy gets refusals rather than silent executions.
    """
    limiter = limiter if limiter is not None else RateLimiter(0)
    result = RunResult()
    working_directory = Path(working_directory)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        for turn in range(1, max_iterations + 1):
            result.turns = turn
            response = request_completion(
                client,
                limiter,
                model=model,
                messages=messages,
                tools=available_functions,
                temperature=0,
                max_tokens=max_tokens,
            )

            if response.model and response.model not in result.models_seen:
                result.models_seen.append(response.model)

            if response.usage is not None:
                result.prompt_tokens += response.usage.prompt_tokens
                result.completion_tokens += response.usage.completion_tokens

                if verbose:
                    print(f"Prompt tokens: {response.usage.prompt_tokens}")
                    print(f"Response tokens: {response.usage.completion_tokens}")

            choice = response.choices[0]
            message = choice.message

            if choice.finish_reason == "content_filter":
                result.exit_code = 1
                result.error = (
                    "Error: the provider blocked this response with a safety "
                    "filter (finish_reason=content_filter). Rephrase the request."
                )
                return result

            if choice.finish_reason == "length":
                print(
                    f"Warning: the model stopped at the {max_tokens} token limit; "
                    "its output may be incomplete."
                )

            _normalise_tool_call_ids(message, turn)
            messages.append(message)

            if not message.tool_calls:
                result.final_message = (
                    message.content or "(the model returned no content)"
                )
                return result

            for tool_call in message.tool_calls:
                request = PermissionRequest(
                    tool_name=tool_call.function.name,
                    arguments=_tool_arguments(tool_call),
                    working_directory=working_directory,
                )
                decision = authorize(request, policy)

                if not decision.allowed:
                    result.denied_calls += 1
                    result_message = refusal_message(tool_call.id, decision)
                    print(f" - Denied: {request.describe()}")
                    print(f"   {decision.message}")
                    messages.append(result_message)
                    continue

                result_message = call_function(
                    tool_call,
                    working_directory=str(working_directory),
                    verbose=verbose,
                )

                if not result_message.get("content"):
                    raise RuntimeError("Tool result content cannot be empty")

                result.tool_calls += 1

                if verbose:
                    print(f"-> {result_message['content']}")

                messages.append(result_message)

        result.exit_code = 1
        result.error = (
            "Error: Maximum number of iterations reached without a final response"
        )
        return result

    except APIStatusError as exc:
        result.exit_code = 1
        result.error = (
            f"Error: the API rejected the request ({exc.status_code}): {exc.message}"
        )
    except APIConnectionError as exc:
        result.exit_code = 1
        result.error = f"Error: could not reach the API: {exc}"
    finally:
        result.rate_limit_wait = limiter.total_wait

    return result


def format_summary(settings: Settings, result: RunResult) -> str:
    served = ", ".join(result.models_seen) if result.models_seen else settings.model
    denied = f" | denied={result.denied_calls}" if result.denied_calls else ""
    return (
        f"[model={settings.model} | served={served} | turns={result.turns} "
        f"| prompt_tokens={result.prompt_tokens} "
        f"| completion_tokens={result.completion_tokens}"
        f"{denied} "
        f"| rate_limit_wait={result.rate_limit_wait:.1f}s]"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    settings = Settings.from_env(args)
    client = create_client(settings)

    if not settings.working_directory.is_dir():
        print(f'Error: "{settings.working_directory}" is not a directory')
        return 1

    policy = build_policy(
        auto_approve=args.yes,
        dry_run=args.dry_run,
        is_tty=stdin_is_tty(),
    )

    if settings.verbose:
        print(f"Base URL: {settings.base_url}")
        print(f"Model: {settings.model} (key from {settings.api_key_source})")
        print(f"Working directory: {settings.working_directory}")
        print(f"Rate limit: {settings.min_interval:.1f}s between requests")
        print(f"Permissions: {policy.name}")
        print(f"User prompt: {args.user_prompt}")

    result = run_agent(
        client,
        args.user_prompt,
        settings.working_directory,
        model=settings.model,
        max_iterations=settings.max_iterations,
        max_tokens=settings.max_tokens,
        limiter=RateLimiter(settings.min_interval),
        policy=policy,
        verbose=settings.verbose,
    )

    if result.error:
        print(result.error)
        if "401" in result.error or "403" in result.error:
            print(
                f"Hint: check {settings.api_key_source or 'LLM_API_KEY'} — "
                f"it was sent to {settings.base_url}"
            )
    else:
        print(result.final_message)

    print(format_summary(settings, result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
