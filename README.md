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
**including via symlinks**. Executed scripts receive a scrubbed environment, so
they cannot read the agent's own API key.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # then paste your key from https://openrouter.ai/keys
```

## Usage

```bash
uv run main.py "run the tests in calculator and fix anything that fails"
uv run main.py "add a modulo operator to pkg/calculator.py" --verbose
uv run main.py "explain what pkg/render.py does" --working-directory calculator
```

| Flag | Description |
| --- | --- |
| `--verbose` | Show model, tool arguments, tool output, and per-turn token counts |
| `--working-directory` | Sandbox root (default: `calculator`) |

Configuration is read from the environment:

| Variable | Default | Description |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Required |
| `OPENROUTER_MODEL` | `cohere/north-mini-code:free` | Any OpenRouter model that supports tool calling |

A run ends with a one-line summary:

```
[cohere/north-mini-code:free | turns=4 | prompt_tokens=3120 | completion_tokens=214]
```

## Design notes

- **Tool errors are values, not exceptions.** A failed tool returns
  `Error: ...` as the `tool` message, so the model can read the failure and
  correct itself. `call_function` never raises — that keeps the conversation
  well-formed (every `tool_call_id` always gets an answer).
- **Containment happens once.** All four tools share `functions/paths.py`, which
  resolves both the root and the target with `Path.resolve()` before checking
  `is_relative_to`. Resolving first is what defeats symlink escapes.
- **The sandbox is advisory, not a jail.** Path checks stop path traversal, not a
  motivated script. Real isolation needs a container.
- **Transient failures are retried.** 429s, timeouts, connection errors and 5xx
  responses back off exponentially with jitter instead of one blanket `sleep`.
- **Output is bounded on both ends.** Reads stop at 10k characters, subprocess
  output at 20k, so one noisy file or script cannot blow the context window.

## Layout

```
main.py                     CLI, agent loop, retries, token accounting
prompts.py                  System prompt
functions/paths.py          Shared path containment
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

## Credit

Based on the Boot.dev "Build an AI Agent" course project, then fixed and
hardened: dead code removed, symlink escape closed, `sys.executable` used
instead of relying on `PATH`, subprocess environment scrubbed, output capped,
`utf-8` enforced, retries added, and the smoke scripts made non-destructive.
