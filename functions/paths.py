from pathlib import Path


def resolve_path(
    working_directory: str, path: str, action: str = "access"
) -> tuple[Path | None, str | None]:
    """Resolve ``path`` relative to ``working_directory``, refusing anything outside it.

    Both sides are fully resolved before comparing, so a symlink cannot be used to
    escape the working directory. Returns ``(resolved_path, None)`` on success and
    ``(None, error_message)`` on failure.
    """
    try:
        root = Path(working_directory).resolve()
    except (OSError, RuntimeError):
        return None, f'Error: Invalid working directory: "{working_directory}"'

    try:
        target = (root / path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f'Error: Could not resolve "{path}": {exc}'

    if not target.is_relative_to(root):
        return None, (
            f'Error: Cannot {action} "{path}" as it is outside the permitted '
            "working directory"
        )

    return target, None
