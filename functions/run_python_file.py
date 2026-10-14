import os
import subprocess

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
        },
    },
}

def run_python_file(
    working_directory: str, file_path: str, args: list[str] | None = None
) -> str:
    working_dir_abs_py = os.path.abspath(working_directory)
    target_file_path = os.path.normpath(os.path.join(working_dir_abs_py, file_path))
    valid_target_file_path = (
        os.path.commonpath([working_dir_abs_py, target_file_path])
        == working_dir_abs_py
    )

    if not valid_target_file_path:
        return f'Error: Cannot execute "{file_path}" as it is outside the permitted working directory'

    if not os.path.isfile(target_file_path):
        return f'Error: "{file_path}" does not exist or is not a regular file'

    if not file_path.endswith(".py"):
        return f'Error: "{file_path}" is not a Python file'

    command = ["python", target_file_path]
    if args:
        command.extend(args)

    try:
        completed_process = subprocess.run(
            command,
            cwd=working_dir_abs_py,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        return f"Error: executing Python file: {e}"

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

    return "\n".join(output_parts)