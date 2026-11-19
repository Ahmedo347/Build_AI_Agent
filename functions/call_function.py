import json
from collections.abc import Callable

from functions.get_files_info import (
    get_file_content,
    get_files_info,
    schema_get_file_content,
    schema_get_files_info,
)
from functions.run_python_file import run_python_file, schema_run_python_file
from functions.write_file import schema_write_file, write_file

available_functions = [
    schema_get_files_info,
    schema_write_file,
    schema_run_python_file,
    schema_get_file_content,
]

function_map: dict[str, Callable[..., str]] = {
    "get_file_content": get_file_content,
    "get_files_info": get_files_info,
    "run_python_file": run_python_file,
    "write_file": write_file,
}


def call_function(
    tool_call, working_directory: str, verbose: bool = False
) -> dict[str, str]:
    """Execute a single tool call and return the matching ``tool`` chat message.

    Never raises: any failure is reported to the model as tool output so the
    conversation stays valid and the agent can recover.
    """

    def tool_message(content: str) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": content,
        }

    function_name = tool_call.function.name

    try:
        function_args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        return tool_message(f"Error: could not parse arguments for {function_name}: {exc}")

    if not isinstance(function_args, dict):
        return tool_message(
            f"Error: arguments for {function_name} must be a JSON object, "
            f"got {type(function_args).__name__}"
        )

    if verbose:
        print(f" - Calling function: {function_name}({function_args})")
    else:
        print(f" - Calling function: {function_name}")

    func = function_map.get(function_name)
    if func is None:
        return tool_message(f"Error: Unknown function: {function_name}")

    try:
        result = func(working_directory=working_directory, **function_args)
    except TypeError as exc:
        return tool_message(f"Error: bad arguments for {function_name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a tool must never kill the agent loop
        return tool_message(f"Error: {function_name} failed: {type(exc).__name__}: {exc}")

    return tool_message(str(result) or "(no output)")
