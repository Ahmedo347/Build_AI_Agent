import argparse
import random
import sys
import time

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from functions.call_function import available_functions, call_function
from prompts import system_prompt
from ratelimit import RateLimiter
from settings import Settings

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 4.0
MAX_BACKOFF_SECONDS = 60.0


def parse_args() -> argparse.Namespace:
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
        help="Model id (default: $LLM_MODEL or gemini-2.5-flash-lite)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=None,
        help="Minimum seconds between model requests (default: $LLM_MIN_INTERVAL or 4.0)",
    )
    return parser.parse_args()


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


def request_completion(
    client: OpenAI, limiter: RateLimiter, **kwargs
):
    """Call the API, waiting for the rate limit and retrying transient failures."""
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait()
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            if attempt == MAX_RETRIES:
                raise
            hinted = _retry_after_seconds(exc)
            delay = hinted if hinted else min(
                BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS
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


def _normalise_tool_call_ids(message) -> None:
    """Fill in missing tool-call ids so the next turn's tool messages match."""
    for index, tool_call in enumerate(message.tool_calls or []):
        if not tool_call.id:
            try:
                tool_call.id = f"call_{index}_{int(time.time() * 1000) % 100000}"
            except (AttributeError, ValueError):
                pass


def main() -> int:
    args = parse_args()
    load_dotenv()
    settings = Settings.from_env(args)
    client = create_client(settings)

    if not settings.working_directory.is_dir():
        print(f'Error: "{settings.working_directory}" is not a directory')
        return 1

    limiter = RateLimiter(settings.min_interval)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": args.user_prompt},
    ]

    if settings.verbose:
        print(f"Base URL: {settings.base_url}")
        print(f"Model: {settings.model} (key from {settings.api_key_source})")
        print(f"Working directory: {settings.working_directory}")
        print(f"Rate limit: {settings.min_interval:.1f}s between requests")
        print(f"User prompt: {args.user_prompt}")

    prompt_tokens = 0
    completion_tokens = 0
    turns = 0
    models_seen: list[str] = []

    try:
        for turn in range(1, settings.max_iterations + 1):
            turns = turn
            response = request_completion(
                client,
                limiter,
                model=settings.model,
                messages=messages,
                tools=available_functions,
                temperature=0,
                max_tokens=settings.max_tokens,
            )

            if response.model and response.model not in models_seen:
                models_seen.append(response.model)

            if response.usage is not None:
                prompt_tokens += response.usage.prompt_tokens
                completion_tokens += response.usage.completion_tokens

                if settings.verbose:
                    print(f"Prompt tokens: {response.usage.prompt_tokens}")
                    print(f"Response tokens: {response.usage.completion_tokens}")

            choice = response.choices[0]
            message = choice.message

            if choice.finish_reason == "content_filter":
                print(
                    "Error: the provider blocked this response with a safety "
                    "filter (finish_reason=content_filter). Rephrase the request."
                )
                return 1

            if choice.finish_reason == "length":
                print(
                    f"Warning: the model stopped at the {settings.max_tokens} "
                    "token limit; its output may be incomplete."
                )

            _normalise_tool_call_ids(message)
            messages.append(message)

            if not message.tool_calls:
                print(message.content or "(the model returned no content)")
                break

            for tool_call in message.tool_calls:
                result_message = call_function(
                    tool_call,
                    working_directory=str(settings.working_directory),
                    verbose=settings.verbose,
                )

                if not result_message.get("content"):
                    raise RuntimeError("Tool result content cannot be empty")

                if settings.verbose:
                    print(f"-> {result_message['content']}")

                messages.append(result_message)
        else:
            print("Error: Maximum number of iterations reached without a final response")
            return 1
    except APIStatusError as exc:
        print(f"Error: the API rejected the request ({exc.status_code}): {exc.message}")
        if exc.status_code in (401, 403):
            print(
                f"Hint: check {settings.api_key_source or 'LLM_API_KEY'} — "
                f"it was sent to {settings.base_url}"
            )
        return 1
    except APIConnectionError as exc:
        print(f"Error: could not reach the API at {settings.base_url}: {exc}")
        return 1

    served = ", ".join(models_seen) if models_seen else settings.model
    print(
        f"[model={settings.model} | served={served} | turns={turns} "
        f"| prompt_tokens={prompt_tokens} | completion_tokens={completion_tokens} "
        f"| rate_limit_wait={limiter.total_wait:.1f}s]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
