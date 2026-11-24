"""Exact-match surgical replacement inside a file.

No unified diffs: the model supplies the exact text it wants replaced. The
uniqueness guard is the safety net — a fragment that is absent or ambiguous
changes nothing and explains how to retry.
"""

from __future__ import annotations

from functions.paths import resolve_path

schema_edit_file = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replaces one exact occurrence of old_text with new_text in an "
            "existing file. Prefer this over write_file when changing only part "
            "of a file. If old_text is missing or appears more than once, "
            "nothing is changed and the error explains how to make it unique."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "The path to the file to edit, relative to the working "
                        "directory"
                    ),
                },
                "old_text": {
                    "type": "string",
                    "description": (
                        "The exact existing text to replace, including its "
                        "whitespace. It must appear exactly once in the file."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": "The text that should replace old_text",
                },
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    },
}


def edit_file(
    working_directory: str, file_path: str, old_text: str, new_text: str
) -> str:
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "Error: old_text and new_text must be strings"

    target_file, error = resolve_path(working_directory, file_path, action="edit")
    if error:
        return error

    if not target_file.is_file():
        return (
            f'Error: File not found or is not a regular file: "{file_path}". '
            "edit_file only edits existing files; use write_file to create one."
        )

    if old_text == "":
        return (
            "Error: old_text must not be empty. Provide the exact existing text "
            "to replace. No changes were made."
        )

    try:
        with open(target_file, "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return f'Error: Could not read "{file_path}": {exc}'

    occurrences = content.count(old_text)
    if occurrences == 0:
        return (
            f'Error: old_text was not found in "{file_path}". Re-read the file '
            "and copy the exact text, including whitespace. No changes were made."
        )

    if occurrences > 1:
        return (
            f'Error: old_text appears {occurrences} times in "{file_path}", so '
            "the edit would be ambiguous. Add surrounding lines to make it "
            "unique. No changes were made."
        )

    updated = content.replace(old_text, new_text, 1)

    try:
        with open(target_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
    except OSError as exc:
        return f'Error: Could not write to "{file_path}": {exc}'

    delta = len(updated) - len(content)
    sign = "+" if delta >= 0 else ""
    return (
        f'Successfully edited "{file_path}" (1 replacement; '
        f"{len(content)} -> {len(updated)} chars, {sign}{delta})"
    )
