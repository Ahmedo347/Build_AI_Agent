import os
import subprocess
import sys
from pathlib import Path

from functions.paths import resolve_path

schema_run_python_file = {
    "type": "function",
    "function": {
        "name": "run_python_file",
        "description": "Executes a Python file within the working directory and returns its output",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the Python file to execute, relative to the working directory",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arguments to pass to the Python script",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
}

TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 20_000

# Environment variables matching these markers are not passed to executed scripts,
# so the agent cannot read its own API keys out of a child process.
SECRET_ENV_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _scrubbed_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_ENV_MARKERS)
    }


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (
        text[:MAX_OUTPUT_CHARS]
        + f"\n[...output truncated at {MAX_OUTPUT_CHARS} characters]"
    )


def run_python_file(
    working_directory: str, file_path: str, args: list[str] | None = None
) -> str:
    target_file, error = resolve_path(
        working_directory, file_path, action="execute"
    )
    if error:
        return error

    if not target_file.is_file():
        return f'Error: "{file_path}" does not exist or is not a regular file'

    if target_file.suffix != ".py":
        return f'Error: "{file_path}" is not a Python file'

    command = [sys.executable, str(target_file), *(args or [])]

    try:
        completed_process = subprocess.run(
            command,
            cwd=str(Path(working_directory).resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=_scrubbed_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: executing Python file timed out after {TIMEOUT_SECONDS}s"
    except OSError as exc:
        return f"Error: executing Python file: {exc}"

    output_parts = []

    if completed_process.returncode != 0:
        output_parts.append(f"Process exited with code {completed_process.returncode}")

    if not completed_process.stdout and not completed_process.stderr:
        output_parts.append("No output produced")
    else:
        if completed_process.stdout:
            output_parts.append(f"STDOUT:\n{completed_process.stdout}")
        if completed_process.stderr:
            output_parts.append(f"STDERR:\n{completed_process.stderr}")

    return _truncate("\n".join(output_parts))
