"""Deterministic permission gate for tools with side effects.

The classification is a hard-coded allow-list, not a model decision:

- ``get_files_info``, ``get_file_content`` and ``search_files`` never ask.
- ``write_file``, ``edit_file`` and ``run_python_file`` always go through the
  policy.

``run_agent`` receives a policy instead of calling ``input()`` itself, so the
loop stays testable and the CLI owns all terminal interaction. A missing or
broken policy denies rather than allows: the gate fails closed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

READ_ONLY_TOOLS = frozenset({"get_files_info", "get_file_content", "search_files"})
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "run_python_file"})

NO_CHANGES = "No changes were made."


@dataclass(frozen=True)
class Decision:
    allowed: bool
    message: str = ""

    @classmethod
    def allow(cls) -> Decision:
        return cls(True)

    @classmethod
    def deny(cls, message: str) -> Decision:
        return cls(False, message)


@dataclass(frozen=True)
class PermissionRequest:
    """What the policy is being asked about, in display-ready form."""

    tool_name: str
    arguments: Mapping[str, Any]
    working_directory: Path

    @property
    def target(self) -> str:
        for key in ("file_path", "directory"):
            value = self.arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return "(unspecified path)"

    def describe(self) -> str:
        details = [f"path: {self.target}"]

        content = self.arguments.get("content")
        if isinstance(content, str):
            unit = "char" if len(content) == 1 else "chars"
            details.append(f"{len(content)} {unit}")

        old_text = self.arguments.get("old_text")
        new_text = self.arguments.get("new_text")
        if isinstance(old_text, str) and isinstance(new_text, str):
            details.append(f"{len(old_text)} -> {len(new_text)} chars")

        args = self.arguments.get("args")
        if isinstance(args, list):
            details.append(f"args: {[str(arg) for arg in args]}")

        return f"{self.tool_name} ({', '.join(details)})"


class Policy(Protocol):
    """Anything callable that answers a mutating-tool request."""

    name: str

    def __call__(self, request: PermissionRequest) -> Decision: ...


@dataclass(frozen=True)
class StaticPolicy:
    """Allow everything, or deny everything with one templated reason."""

    name: str
    template: str | None = None

    def __call__(self, request: PermissionRequest) -> Decision:
        if self.template is None:
            return Decision.allow()
        return Decision.deny(self.template.format(action=request.describe()))


ALLOW_ALL = StaticPolicy("auto-approve (--yes)")

DRY_RUN = StaticPolicy(
    "dry-run",
    template=(
        "Skipped {action} because the agent is running in dry-run mode. "
        f"{NO_CHANGES} Read-only tools still work; describe the change instead "
        "of applying it."
    ),
)

NON_INTERACTIVE = StaticPolicy(
    "non-interactive (fail-closed)",
    template=(
        "Permission denied for {action}: stdin is not a terminal, so approval "
        f"could not be requested. {NO_CHANGES} Re-run with --yes to approve "
        "mutations automatically, or avoid tools that change state."
    ),
)


@dataclass(frozen=True)
class InteractivePolicy:
    """Ask on the terminal. Anything but an explicit yes is a no."""

    input_fn: Callable[[str], str] = input
    name: str = "interactive"

    def __call__(self, request: PermissionRequest) -> Decision:
        prompt = f"Allow {request.describe()}? [y/N] "

        try:
            answer = self.input_fn(prompt)
        except EOFError:
            return Decision.deny(
                f"Permission denied for {request.describe()}: no answer was "
                f"received (end of input). {NO_CHANGES}"
            )

        if str(answer).strip().lower() in {"y", "yes"}:
            return Decision.allow()

        return Decision.deny(
            f"Permission denied for {request.describe()}: the user declined. "
            f"{NO_CHANGES}"
        )


def authorize(request: PermissionRequest, policy: Policy | None) -> Decision:
    """Decide whether ``request`` may run. Reads are always allowed."""
    if request.tool_name not in MUTATING_TOOLS:
        return Decision.allow()

    if policy is None:
        return Decision.deny(
            f"Permission denied for {request.describe()}: no approval policy is "
            f"configured. {NO_CHANGES}"
        )

    try:
        decision = policy(request)
    except Exception as exc:  # noqa: BLE001 - a broken policy must not run tools
        return Decision.deny(
            f"Permission denied for {request.describe()}: the approval check "
            f"failed ({type(exc).__name__}: {exc}). {NO_CHANGES}"
        )

    if not isinstance(decision, Decision):
        return Decision.deny(
            f"Permission denied for {request.describe()}: the approval check "
            f"returned {type(decision).__name__}, not a decision. {NO_CHANGES}"
        )

    return decision


def refusal_message(tool_call_id: str, decision: Decision) -> dict[str, str]:
    """A denied call still gets a ``tool`` message, so the turn stays valid."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": decision.message,
    }


def build_policy(
    *,
    auto_approve: bool,
    dry_run: bool,
    is_tty: bool,
    input_fn: Callable[[str], str] = input,
) -> Policy:
    """Pick the policy for this run. ``--dry-run`` outranks ``--yes``."""
    if dry_run:
        return DRY_RUN
    if auto_approve:
        return ALLOW_ALL
    if not is_tty:
        return NON_INTERACTIVE
    return InteractivePolicy(input_fn=input_fn)


def stdin_is_tty() -> bool:
    """``sys.stdin.isatty()`` without trusting it (it can be ``None``)."""
    stdin = sys.stdin
    try:
        return bool(stdin is not None and stdin.isatty())
    except (AttributeError, ValueError):
        return False
