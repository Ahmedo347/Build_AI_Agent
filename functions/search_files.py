"""Search file contents inside the working directory.

Pure Python on purpose: no ``rg``/``grep`` binary to install, and every
candidate goes through :func:`functions.paths.resolve_path` so symlink escapes
and deny-listed secrets are skipped before a single byte is read.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path

from functions.paths import DENIED_DIR_NAMES, resolve_path

MAX_MATCHES = 100
MAX_CHARS = 10_000
MAX_LINE_CHARS = 300
MAX_FILE_BYTES = 2_000_000

schema_search_files = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": (
            "Searches file contents inside the working directory and returns "
            "matching lines as 'path:line: text'. Prefer this over reading every "
            "file when you need to find where something is defined or used."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The text to find. A literal substring unless regex is true."
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": (
                        "Directory to search, relative to the working directory. "
                        "Defaults to the working directory itself."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Optional filename filter such as '*.py' or 'pkg/*.py'. "
                        "Defaults to all text files."
                    ),
                },
                "regex": {
                    "type": "boolean",
                    "description": (
                        "Treat query as a Python regular expression. "
                        "Defaults to false (literal substring)."
                    ),
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Match case-sensitively. Defaults to true.",
                },
            },
            "required": ["query"],
        },
    },
}


def search_files(
    working_directory: str,
    query: str,
    directory: str = ".",
    glob: str | None = None,
    regex: bool = False,
    case_sensitive: bool = True,
) -> str:
    if not isinstance(query, str) or not query:
        return "Error: query must be a non-empty string"

    search_root, error = resolve_path(working_directory, directory, action="search")
    if error:
        return error

    if not search_root.is_dir():
        return f'Error: "{directory}" is not a directory'

    try:
        working_root = Path(working_directory).resolve()
    except (OSError, RuntimeError, ValueError):
        return f'Error: Invalid working directory: "{working_directory}"'

    try:
        matcher = _build_matcher(query, regex=regex, case_sensitive=case_sensitive)
    except re.error as exc:
        return f"Error: invalid regular expression: {exc}"

    matches: list[str] = []
    total_chars = 0
    skipped_large = 0
    truncated = False

    for path in _iter_candidate_files(search_root):
        target, error = resolve_path(
            working_directory,
            os.path.relpath(path, working_root),
            action="search",
        )
        if error or target is None or not target.is_file():
            continue

        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                skipped_large += 1
                continue
        except OSError:
            continue

        if glob and not _matches_glob(target, working_root, glob):
            continue

        if _looks_binary(target):
            continue

        display_path = _display_path(target, working_root)

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    line = raw_line.rstrip("\n").rstrip("\r")
                    if not matcher(line):
                        continue

                    snippet = line.strip()
                    if len(snippet) > MAX_LINE_CHARS:
                        snippet = snippet[:MAX_LINE_CHARS] + "…"

                    entry = f"{display_path}:{line_number}: {snippet}"
                    if (
                        len(matches) >= MAX_MATCHES
                        or total_chars + len(entry) + 1 > MAX_CHARS
                    ):
                        truncated = True
                        break

                    matches.append(entry)
                    total_chars += len(entry) + 1
        except OSError:
            continue

        if truncated:
            break

    if not matches:
        if truncated:
            return (
                f'Search for "{query}" was truncated before any match could be '
                "returned; narrow the query or add a glob."
            )
        return f'No matches for "{query}" in "{directory}".'

    output = "\n".join(matches)
    if truncated:
        output += (
            f"\n[...search truncated at {len(matches)} matches / {MAX_CHARS} "
            "characters; narrow the query or add a glob]"
        )
    if skipped_large:
        output += (
            f"\n[...skipped {skipped_large} file(s) larger than {MAX_FILE_BYTES} bytes]"
        )
    return output


def _build_matcher(
    query: str, *, regex: bool, case_sensitive: bool
) -> Callable[[str], bool]:
    """Return a predicate so literal and regex searches share one call site."""
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query, flags)
        return lambda line: pattern.search(line) is not None

    if case_sensitive:
        return lambda line: query in line

    lowered = query.lower()
    return lambda line: lowered in line.lower()


def _iter_candidate_files(root: Path) -> Iterator[Path]:
    """Walk ``root`` without following symlinks, pruning denied directories."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in DENIED_DIR_NAMES and name != "__pycache__"
        )
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def _matches_glob(target: Path, working_root: Path, pattern: str) -> bool:
    relative = target.relative_to(working_root).as_posix()
    return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(target.name, pattern)


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return True


def _display_path(target: Path, working_root: Path) -> str:
    try:
        return target.relative_to(working_root).as_posix()
    except ValueError:
        return target.name
