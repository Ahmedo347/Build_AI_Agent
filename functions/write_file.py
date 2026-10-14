import os

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
        },
    },
}

def write_file(working_directory: str, file_path: str, content: str) -> str:

    working_dir_abs_content = os.path.abspath(working_directory)
    target_file_path = os.path.normpath(os.path.join(working_dir_abs_content, file_path))
    valid_target_file_path = os.path.commonpath([working_dir_abs_content, target_file_path]) == working_dir_abs_content

    if not  valid_target_file_path:
        return f'Error: Cannot write to "{file_path}" as it is outside the permitted working directory'

    if os.path.isdir(target_file_path):
            return f'Error: Cannot write to "{file_path}" as it is a directory'

    parent_dir = os.path.dirname(target_file_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(target_file_path, "w") as f:
         f.write(content)

    return f'Successfully wrote to "{file_path}" ({len(content)} characters written)'
