import argparse
import os
import random
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

MAX_ITERATIONS = 20
MAX_RETRIES = 4
DEFAULT_MODEL = "cohere/north-mini-code:free"
DEFAULT_WORKING_DIRECTORY = "calculator"
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 4096


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A tool-calling AI coding agent confined to a working directory"
    )
    parser.add_argument("user_prompt", type=str, help="User prompt")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--working-directory",
        default=DEFAULT_WORKING_DIRECTORY,
        help="Directory the agent is allowed to read and write "
        f"(default: {DEFAULT_WORKING_DIRECTORY})",
    )
    return parser.parse_args()


def create_client() -> OpenAI:
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Error: OPENROUTER_API_KEY is not set. Add it to .env "
            "(see .env.example) or export it in your shell."
        )

    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def request_completion(client: OpenAI, **kwargs):
    """Call the API, retrying transient failures with exponential backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            if attempt == MAX_RETRIES:
                raise
            delay = 2**attempt + random.uniform(0, 0.5)
            print(
                f"Transient API error ({type(exc).__name__}), "
                f"retrying in {delay:.1f}s (attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(delay)
        except APIStatusError as exc:
            if attempt == MAX_RETRIES or exc.status_code < 500:
                raise
            delay = 2**attempt + random.uniform(0, 0.5)
            print(
                f"Server error {exc.status_code}, retrying in {delay:.1f}s "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(delay)

    raise RuntimeError("unreachable")


def main() -> int:
    args = parse_args()
    client = create_client()

    if not os.path.isdir(args.working_directory):
        print(f'Error: "{args.working_directory}" is not a directory')
        return 1

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": args.user_prompt},
    ]

    if args.verbose:
        print(f"Model: {model}")
        print(f"Working directory: {args.working_directory}")
        print(f"User prompt: {args.user_prompt}")

    prompt_tokens = 0
    completion_tokens = 0
    turns = 0

    try:
        for turn in range(1, MAX_ITERATIONS + 1):
            turns = turn
            response = request_completion(
                client,
                model=model,
                messages=messages,
                tools=available_functions,
                temperature=0,
                max_tokens=MAX_TOKENS,
            )

            if response.usage is not None:
                prompt_tokens += response.usage.prompt_tokens
                completion_tokens += response.usage.completion_tokens

                if args.verbose:
                    print(f"Prompt tokens: {response.usage.prompt_tokens}")
                    print(f"Response tokens: {response.usage.completion_tokens}")

            choice = response.choices[0]
            message = choice.message
            messages.append(message)

            if choice.finish_reason == "length":
                print(
                    f"Warning: the model stopped at the {MAX_TOKENS} token limit; "
                    "its output is incomplete."
                )

            if not message.tool_calls:
                print(message.content or "(the model returned no content)")
                break

            for tool_call in message.tool_calls:
                result_message = call_function(
                    tool_call,
                    working_directory=args.working_directory,
                    verbose=args.verbose,
                )

                if not result_message.get("content"):
                    raise RuntimeError("Tool result content cannot be empty")

                if args.verbose:
                    print(f"-> {result_message['content']}")

                messages.append(result_message)
        else:
            print("Error: Maximum number of iterations reached without a final response")
            return 1
    except APIStatusError as exc:
        print(f"Error: the API rejected the request ({exc.status_code}): {exc.message}")
        if exc.status_code in (401, 403):
            print("Hint: check that OPENROUTER_API_KEY is valid — https://openrouter.ai/keys")
        return 1
    except APIConnectionError as exc:
        print(f"Error: could not reach the API: {exc}")
        return 1

    print(
        f"[{model} | turns={turns} | prompt_tokens={prompt_tokens} "
        f"| completion_tokens={completion_tokens}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
