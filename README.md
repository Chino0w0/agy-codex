# codex-in-anti

[English](README.md) | [简体中文](README.zh-CN.md)

Delegate bounded coding, debugging, refactoring, and independent code review tasks from **Antigravity** to **OpenAI Codex CLI (GPT)**—using a lightweight worker profile, strict sandbox boundaries, and structured JSON handoffs.

## Why `codex-in-anti`?

Running `codex exec` directly against a desktop `~/.codex/config.toml` pulls in GUI plugins, heavy MCP servers, hooks, memories, and nested subagent instructions on every call. In a controlled local benchmark across 3 real tasks (read-only analysis, greenfield module + tests, and bug fix + verification), routing through `-p worker` cut total token usage by **45.2%** (from **108,617** to **59,550** tokens, with up to **64.2%** savings on standalone generation) and reduced total runtime by **8.1%** (up to **17.3%** faster on single tasks) while keeping outputs identical and verified.

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

## Benchmark

Measured sequentially on macOS with `codex-cli 0.171.0`. Both variants shared the exact same model, `model_reasoning_effort = "medium"`, `--output-schema`, prompts, and clean workspaces; the only difference was `--no-worker-profile` (desktop config with bundled plugins, hooks, memories, `chronicle`, and MCP servers enabled) vs. the default `-p worker` profile (`worker.config.toml`).

| Task | Mode | Baseline Time | Worker Time | Time Delta | Baseline Tokens | Worker Tokens | Token Delta | Compactions (B/W) |
| :--- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| **T1**: Read-only analysis of `codex_run.py` | `read-only` | 18.16s | 17.99s | -0.9% | 37,692 | 24,703 | **-34.5%** | 0 / 0 |
| **T2**: Create `math_utils.py` + 6+ `assert` tests & run | `write` | 21.60s | 17.86s | **-17.3%** | 35,516 | 12,730 | **-64.2%** | 0 / 0 |
| **T3**: Fix `parser.py` bugs to pass `test_parser.py` | `write` | 16.85s | 16.17s | -4.0% | 35,409 | 22,117 | **-37.5%** | 0 / 0 |
| **Total (3 tasks)** | - | **56.61s** | **52.02s** | **-8.1%** | **108,617** | **59,550** | **-45.2%** | 0 / 0 |

**What caused the difference?**
- Inspecting the execution logs showed the baseline runs fired `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` hooks on every tool call, injected desktop plugins/MCP schemas into context (even invoking a desktop MCP glob tool inside an empty directory during T2), and emitted `chronicle` feature warnings.
- With `-p worker`, Codex skipped hooks, desktop plugins, memories, and MCP servers—going straight to shell inspection, patching, and running verification tests with all assertions verified passing post-run.
- In environments where desktop plugins or remote OAuth MCP servers also stall startup or trigger context compactions, latency and token savings can be even larger.

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
