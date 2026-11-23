"""Runtime configuration, resolved from CLI flags, then env vars, then defaults.

Any OpenAI-compatible endpoint works. Google AI Studio's compat layer is the
default because it needs no local gateway:

    LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    LLM_API_KEY=...
    LLM_MODEL=gemini-2.5-flash-lite
"""

from __future__ import annotations

import os
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_WORKING_DIRECTORY = "calculator"

# Keep under the free tier's requests-per-minute cap. Gemini 2.5 Flash Lite
# allows ~15 RPM, so 4s between requests is the safe floor.
DEFAULT_MIN_INTERVAL = 4.0
DEFAULT_REQUEST_TIMEOUT = 120.0
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_MAX_TOKENS = 8192

# Provider-specific key names, tried after the generic ones and only when the
# base URL matches the provider, so a stale OpenRouter key can never be sent to
# Google (or vice versa).
PROVIDER_KEYS = {
    "openrouter.ai": ("OPENROUTER_API_KEY",),
    "generativelanguage.googleapis.com": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    api_key_source: str
    model: str
    working_directory: Path
    min_interval: float
    request_timeout: float
    max_iterations: int
    max_tokens: int
    verbose: bool

    @classmethod
    def from_env(cls, args: Namespace, *, environ: dict[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        base_url = (args.base_url or _first(env, "LLM_BASE_URL") or DEFAULT_BASE_URL).strip()
        if not base_url.endswith("/"):
            base_url += "/"

        api_key, api_key_source = _resolve_api_key(env, base_url)

        return cls(
            base_url=base_url,
            api_key=api_key,
            api_key_source=api_key_source,
            model=args.model or _first(env, "LLM_MODEL") or _legacy_model(env, base_url) or DEFAULT_MODEL,
            working_directory=Path(args.working_directory),
            min_interval=_as_float(
                args.min_interval,
                _first(env, "LLM_MIN_INTERVAL"),
                DEFAULT_MIN_INTERVAL,
            ),
            request_timeout=_as_float(
                None, _first(env, "LLM_TIMEOUT"), DEFAULT_REQUEST_TIMEOUT
            ),
            max_iterations=_as_int(
                None, _first(env, "LLM_MAX_ITERATIONS"), DEFAULT_MAX_ITERATIONS
            ),
            max_tokens=_as_int(None, _first(env, "LLM_MAX_TOKENS"), DEFAULT_MAX_TOKENS),
            verbose=bool(args.verbose),
        )

    @property
    def missing_key_hint(self) -> str:
        return (
            f"Error: no API key for {self.base_url}.\n"
            "Set LLM_API_KEY in .env (see .env.example)."
        )


def _first(env: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value.strip()
    return None


def _resolve_api_key(env: dict[str, str], base_url: str) -> tuple[str, str]:
    generic = _first(env, "LLM_API_KEY")
    if generic:
        return generic, "LLM_API_KEY"

    freellmapi = _first(env, "FREELLMAPI_API_KEY")
    if freellmapi:
        return freellmapi, "FREELLMAPI_API_KEY"

    for host, names in PROVIDER_KEYS.items():
        if host not in base_url:
            continue
        for name in names:
            value = _first(env, name)
            if value:
                return value, name

    return "", ""


def _legacy_model(env: dict[str, str], base_url: str) -> str | None:
    if "openrouter.ai" not in base_url:
        return None
    return _first(env, "OPENROUTER_MODEL")


def _as_float(cli_value: float | None, env_value: str | None, default: float) -> float:
    if cli_value is not None:
        return float(cli_value)
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass
    return default


def _as_int(cli_value: int | None, env_value: str | None, default: int) -> int:
    if cli_value is not None:
        return int(cli_value)
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    return default
