# codex-in-anti

[English](README.md) | [简体中文](README.zh-CN.md)

Delegate bounded coding, debugging, refactoring, and independent code review tasks from **Antigravity** to **OpenAI Codex CLI (GPT)**—using a lightweight worker profile, strict sandbox boundaries, and structured JSON handoffs.

## Why `codex-in-anti`?

Running `codex exec` directly against a desktop `~/.codex/config.toml` pulls in GUI plugins, heavy MCP servers, hooks, memories, and nested subagent prompts on every call. In our benchmark on a simple Python task, a raw `codex exec` call consumed **~598k tokens** and triggered context compaction twice, whereas routing through the `worker` profile dropped usage to **~22k tokens (~96% reduction)** and cut runtime from **~70s to ~13s**.

`codex-in-anti` packages that setup into a clean, drop-in workflow:

- **Zero-touch on your desktop config**: Uses `~/.codex/worker.config.toml` via `-p worker`, leaving your `~/.codex/config.toml` untouched.
- **Strict sandbox enforcement**: Locks `exec` to `workspace-write` (or `read-only`) and `review` to `read-only`—never inheriting global `danger-full-access`.
- **Structured JSON handoffs**: Enforces `--output-schema` + `-o` so Antigravity receives clean `status`, `changed_files`, `verification`, `risks`, and `next_steps` instead of parsing noisy terminal logs.
- **Multi-turn `resume` & Git `review`**: Supports ephemeral single-shot tasks (`--ephemeral`), persistent multi-turn sessions (`--persist` + `resume <session_id>`), and second-opinion `review` runs.
- **Zero third-party dependencies**: Pure Python 3.10+ standard library wrapper (`codex_run.py`) that also resolves `codex` across non-login shells and auto-handles non-Git directories (`--skip-git-repo-check`).

## Prerequisites

- **Antigravity** (with user skills under `~/.gemini/config/skills`)
- **OpenAI Codex CLI** installed and authenticated (`codex login`)
- **Python 3.10+**

## Quick Start

```bash
git clone https://github.com/Silhouette-my/codex-in-anti.git
cd codex-in-anti
./install.sh
```

By default, `./install.sh`:
1. Installs the `codex-worker` skill to `~/.gemini/config/skills/codex-worker/`.
2. Copies `profiles/worker.config.toml` to `~/.codex/worker.config.toml` (skips if it already exists; pass `--force` to overwrite).

You can override target paths via `ANTIGRAVITY_SKILLS_DIR` and `CODEX_HOME`:

```bash
ANTIGRAVITY_SKILLS_DIR=/custom/skills CODEX_HOME=/custom/.codex ./install.sh
```

## Usage

### 1. Inside Antigravity (Natural Language)

Once installed, Antigravity automatically discovers the `codex-worker` skill. You can simply say:

- *"Delegate implementing module X to Codex, then inspect the diff and verify tests."*
- *"Run a second-opinion code review on my uncommitted changes with Codex."*
- *"Ask GPT via Codex to debug the failing test in `tests/test_api.py` in read-only mode."*

### 2. Direct Wrapper CLI (`codex_run.py`)

Run a single-shot write task:

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --effort medium \
  --prompt "Implement parse_config() in src/config.py and run pytest tests/test_config.py."
```

Run a persistent multi-turn task and resume by `session_id`:

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --persist \
  --prompt "Refactor the auth middleware and run the test suite."

python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py resume \
  -C /path/to/repo \
  <session_id> \
  --prompt "Fix the edge case when token expiry is missing."
```

Run a read-only code review on uncommitted Git changes:

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py review \
  -C /path/to/repo \
  --uncommitted \
  --raw
```

### Wrapper Options

| Flag | Description |
| :--- | :--- |
| `-C`, `--cwd` | Working directory for Codex (default: `.`) |
| `--mode` | `write` (`workspace-write`) or `read-only` |
| `-m`, `--model` | Override Codex model |
| `--effort` | Override `model_reasoning_effort` (`low` \| `medium` \| `high`) |
| `--persist` | Keep Codex session on disk instead of `--ephemeral` |
| `--add-dir` | Grant additional writable directory (repeatable) |
| `--raw` | Disable JSON output schema and return plain text |
| `--search` | Enable live web search |
| `--no-worker-profile` | Skip `-p worker` and use your base `~/.codex/config.toml` |
| `--timeout` | Subprocess timeout in seconds (default: `600`) |
| `--log-dir` | Directory for per-run logs and artifacts (default: `$TMPDIR/codex-worker-runs`) |

## Output Format

Each run prints a JSON summary to `stdout` and archives `codex.log`, `output.json` (or `output.txt`), and `meta.json` under the run directory:

```json
{
  "ok": true,
  "exit_code": 0,
  "timed_out": false,
  "duration_sec": 12.98,
  "model": "gpt-...",
  "session_id": "...",
  "tokens_used": 22138,
  "result": {
    "status": "done",
    "summary": "...",
    "changed_files": ["..."],
    "verification": [
      {
        "command": "pytest",
        "passed": true,
        "notes": "All tests passed."
      }
    ],
    "risks": [],
    "next_steps": []
  },
  "output_file": "/tmp/codex-worker-runs/.../output.json",
  "log_file": "/tmp/codex-worker-runs/.../codex.log"
}
```

## Customization

Edit `~/.codex/worker.config.toml` to match your local setup:
- **Pin a model**: Uncomment `model = "..."` (or pass `-m <model>` per invocation).
- **Disable custom heavy MCP servers**: If your `~/.codex/config.toml` configures extra MCP servers (e.g., Notion, Canva, Jira), add `[mcp_servers.<name>]\nenabled = false` to `worker.config.toml` to prevent OAuth/startup errors in headless runs.

## Repository Structure

```text
codex-in-anti/
├── install.sh                                  # Idempotent installer script
├── profiles/
│   └── worker.config.toml                      # Lightweight Codex CLI profile (-p worker)
├── skill/
│   └── codex-worker/
│       ├── SKILL.md                            # Antigravity orchestration skill instructions
│       ├── resources/
│       │   └── handoff_schema.json           # JSON Schema enforced via --output-schema
│       └── scripts/
│           └── codex_run.py                    # Zero-dependency Python wrapper CLI
├── README.md
├── README.zh-CN.md
└── LICENSE
```

## License

[MIT](LICENSE)
