# Build AI Agent

A small but defensible coding agent: an LLM in a loop with four sandboxed tools,
rooted at a single working directory. Built to understand how tool-calling agents
actually work — file access, code execution, error recovery — without a framework
hiding the interesting parts.

```
you ──> system prompt + tools ──> LLM ──┬──> final answer
                                        └──> tool calls ──> results ──> loop
```

## Tools

| Tool | Description |
| --- | --- |
| `get_files_info` | List a directory: name, size, whether it is a directory |
| `get_file_content` | Read a file, capped at 10,000 characters |
| `write_file` | Create or overwrite a file (creates parent directories) |
| `run_python_file` | Execute a Python file with optional arguments, 30s timeout |

Every path is resolved against the working directory and rejected if it escapes,
**including via symlinks**. A deny-list blocks `.env`, `.git/`, `.ssh/`, `*.pem`
and similar even when they are inside the working directory, so the agent can
never read its own credentials. Executed scripts also get a scrubbed environment.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env     # then paste your key
```

### Provider

Any OpenAI-compatible endpoint works; only three variables matter.

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Google AI Studio compat layer |
| `LLM_API_KEY` | — | Required. Fallbacks: `FREELLMAPI_API_KEY`, then a provider-specific key (`OPENROUTER_API_KEY`, `GOOGLE_API_KEY`) only when the base URL matches that provider |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Pin an exact version; avoid `-latest` aliases when comparing runs |
| `LLM_MIN_INTERVAL` | `4.0` | Seconds between requests (≈15 RPM). Raise it if you hit 429s |
| `LLM_TIMEOUT` | `120` | Per-request timeout in seconds |
| `LLM_MAX_TOKENS` | `8192` | Output cap |
| `LLM_MAX_ITERATIONS` | `20` | Tool-loop guard |

Get a free key at <https://aistudio.google.com/apikey>. Free-tier prompts are
used to improve Google's models — don't point the agent at sensitive directories.

Switching provider is a `.env` edit, not a code change:

```bash
# OpenRouter
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=cohere/north-mini-code:free

# FreeLLMAPI (self-hosted gateway, Docker)
LLM_BASE_URL=http://localhost:3001/v1

# Ollama (local)
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
```

## Usage

```bash
uv run main.py "run the tests in calculator and fix anything that fails"
uv run main.py "add a modulo operator to pkg/calculator.py" --verbose
uv run main.py "explain what pkg/render.py does" --working-directory calculator
uv run main.py "list the files here" --model gemini-3.5-flash --min-interval 6
```

| Flag | Description |
| --- | --- |
| `--verbose` | Show base URL, resolved key source, model, tool arguments and output, per-turn tokens |
| `--working-directory` | Sandbox root (default: `calculator`) |
| `--base-url` | Override `LLM_BASE_URL` |
| `--model` | Override `LLM_MODEL` |
| `--min-interval` | Override `LLM_MIN_INTERVAL` |

A run ends with a one-line summary:

```
[model=gemini-3.5-flash-lite | served=gemini-3.5-flash-lite | turns=4
 | prompt_tokens=3120 | completion_tokens=214 | rate_limit_wait=12.0s]
```

`served` is the model the provider actually answered with — worth logging when a
gateway (FreeLLMAPI, OpenRouter `auto`) may route elsewhere.

## Design notes

- **Tool errors are values, not exceptions.** A failed tool returns `Error: ...`
  as the `tool` message, so the model can read the failure and correct itself.
  `call_function` never raises, which keeps every `tool_call_id` answered.
- **Containment happens once.** All four tools share `functions/paths.py`, which
  resolves both root and target with `Path.resolve()` before checking
  `is_relative_to`. Resolving first is what defeats symlink escapes.
- **The sandbox is advisory, not a jail.** Path checks stop path traversal, not a
  motivated script. Real isolation needs a container.
- **Rate limiting is per request, not per task.** One agent turn issues several
  requests back to back, so a delay between tasks does not stop 429s. The limiter
  sleeps before every request, and retries honour `Retry-After`.
- **The SDK's retries are disabled** (`max_retries=0`) so backoff lives in one
  place instead of multiplying.
- **Output is bounded on both ends.** Reads stop at 10k characters, subprocess
  output at 20k, so one noisy file cannot blow the context window.
- **Safety-filtered responses are detected** (`finish_reason == "content_filter"`)
  and reported, rather than surfacing an empty message.

## Layout

```
main.py                     CLI, agent loop, retries, token accounting
settings.py                 Config resolution: CLI > env > defaults
ratelimit.py                Minimum-interval limiter between requests
prompts.py                  System prompt
functions/paths.py          Shared containment + secret deny-list
functions/call_function.py  Tool dispatch: schema -> implementation, never raises
functions/*.py              One tool per module, schema next to implementation
calculator/                 Tutorial fixture: a small calculator app to operate on
test_*.py                   Smoke scripts for each tool
```

## Known limits

- Single working directory per run; no multi-repo support.
- No approval prompt before writes or execution — run it on fixture code.
- No conversation persistence or `--resume`.
- Execution is not containerized.
- Tool schemas stay deliberately plain (no `additionalProperties`, defaults or
  nested unions) because provider compatibility layers reject some JSON Schema
  keywords.

## Credit

Based on the Boot.dev "Build an AI Agent" course project, then fixed and
hardened: dead code removed, symlink escape closed, `sys.executable` used
instead of relying on `PATH`, subprocess environment scrubbed, output capped,
`utf-8` enforced, retries added, provider made configurable, and the smoke
scripts made non-destructive.
