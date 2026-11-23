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

    denial = denied_reason(target, root)
    if denial:
        return None, f'Error: Cannot {action} "{path}" as it is {denial}'

    return target, None


# Paths the agent must never touch, even when they sit inside the working
# directory. The first entry matters most: `.env` holds the agent's own key.
DENIED_DIR_NAMES = {".git", ".ssh", ".aws", ".gnupg", ".docker", ".config"}
DENIED_FILE_NAMES = {
    ".env",
    ".netrc",
    ".pgpass",
    "credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}


def denied_reason(target: Path, root: Path) -> str | None:
    """Return a human-readable reason if ``target`` is a protected path."""
    try:
        relative = target.relative_to(root)
    except ValueError:
        return "outside the permitted working directory"

    for parent in relative.parents:
        if parent == Path("."):
            continue
        if parent.name in DENIED_DIR_NAMES:
            return f'a protected path ("{parent.name}" is off limits)'

    name = relative.name
    if name in DENIED_DIR_NAMES:
        return f'a protected path ("{name}" is off limits)'
    if name in DENIED_FILE_NAMES:
        return "a protected path (secrets are off limits)"
    if name.startswith(".env.") and name != ".env.example":
        return "a protected path (secrets are off limits)"
    if target.suffix.lower() in DENIED_SUFFIXES:
        return "a protected path (key material is off limits)"

    return None
