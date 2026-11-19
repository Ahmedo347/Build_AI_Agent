import os

from functions.paths import resolve_path

schema_get_files_info = {
    "type": "function",
    "function": {
        "name": "get_files_info",
        "description": "Lists files in a specified directory relative to the working directory, providing file size and directory status",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to list files from, relative to the working directory (default is the working directory itself)",
                },
            },
            "required": ["directory"],
            "additionalProperties": False,
        },
    },
}

schema_get_file_content = {
    "type": "function",
    "function": {
        "name": "get_file_content",
        "description": "Reads and returns the content of a file within the working directory",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read, relative to the working directory",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
}

MAX_CHARS = 10_000


def get_files_info(working_directory: str, directory: str = ".") -> str:
    target_dir, error = resolve_path(working_directory, directory, action="list")
    if error:
        return error

    if not target_dir.is_dir():
        return f'Error: "{directory}" is not a directory'

    files_info = []
    try:
        entries = sorted(os.scandir(target_dir), key=lambda entry: entry.name)
    except OSError as exc:
        return f'Error: Could not list "{directory}": {exc}'

    for entry in entries:
        try:
            file_size = entry.stat().st_size
            is_dir = entry.is_dir()
        except OSError as exc:
            files_info.append(f"- {entry.name}: could not stat ({exc})")
            continue

        files_info.append(
            f"- {entry.name}: file_size={file_size} bytes, is_dir={is_dir}"
        )

    return "\n".join(files_info)


def get_file_content(working_directory: str, file_path: str) -> str:
    target_file, error = resolve_path(working_directory, file_path, action="read")
    if error:
        return error

    if not target_file.is_file():
        return f'Error: File not found or is not a regular file: "{file_path}"'

    try:
        with open(target_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_CHARS)
            if f.read(1):
                content += (
                    f'\n[...File "{file_path}" truncated at {MAX_CHARS} characters]'
                )
    except OSError as exc:
        return f'Error: Could not read "{file_path}": {exc}'

    return content
