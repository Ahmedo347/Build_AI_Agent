from functions.paths import resolve_path

schema_write_file = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Writes content to a file within the working directory",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to write, relative to the working directory",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    },
}


def write_file(working_directory: str, file_path: str, content: str) -> str:
    target_file, error = resolve_path(working_directory, file_path, action="write to")
    if error:
        return error

    if target_file.is_dir():
        return f'Error: Cannot write to "{file_path}" as it is a directory'

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError as exc:
        return f'Error: Could not write to "{file_path}": {exc}'

    return f'Successfully wrote to "{file_path}" ({len(content)} characters written)'
