# codex-in-anti

[简体中文](README.md) | [English](README.en.md)

[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-black)](https://linux.do/)

Delegate bounded coding, debugging, and refactoring tasks from **Antigravity** to **OpenAI Codex CLI (GPT)**—while keeping orchestration, Code Review, and final verification in the main agent's hands.

## Why `codex-in-anti`?

Running `codex exec` directly against a desktop `~/.codex/config.toml` pulls in GUI plugins, heavy MCP servers, hooks, memories, and nested subagent instructions on every call. In a controlled local benchmark across 3 real tasks (read-only analysis, greenfield module + tests, and bug fix + verification), `-p worker` cut total token usage by **45.2%** (from **108,617** to **59,550** tokens, with up to **64.2%** savings on standalone generation) and reduced total runtime by **8.1%** (up to **17.3%** faster on single tasks) while keeping outputs verified.

Building on that, `codex-in-anti` uses **whitelist isolation (`--ignore-user-config`)** by default for complete zero-bloat execution:

- **Whitelist config isolation (default `isolated` mode)**: Passes `--ignore-user-config` to skip `~/.codex/config.toml` (keeping `auth.json`), and uses stdlib `tomllib` to whitelist only `model`, `model_provider`, reasoning effort, active provider scalars, and proxy settings via `-c`. All desktop plugins, third-party MCP servers, hooks, memories, and `chronicle` are 100% excluded with zero blacklist maintenance (gracefully falls back to `-p worker` on Python 3.10).
- **Clear separation of duties & strict sandboxing**: Antigravity (the main agent) owns task design, Code Review, and final verification; Codex CLI acts strictly as a bounded execution worker locked to `workspace-write` (or `read-only` for investigation).
- **Live progress stream + native-style MCP App UI plugin**:
  - **Inline process card & side-pane monitor**: Ships an Antigravity MCP App plugin (`plugin/codex-in-anti`) that renders a live, collapsible Codex-native timeline inline in chat or in the side pane—live shell commands, exit badges and expandable output, clickable changed-file chips (opening directly in Antigravity's side pane), live elapsed timer, live In/Cached/Output token counter, and a one-click `Stop` button;
  - **Zero build step for users**: Pre-bundled single-file HTML UI (`assets/app.html`) means end users do not need Node.js installed;
  - **CLI streaming**: Standalone skill mode streams concise milestones to `stderr` from `codex exec --json` while keeping `stdout` pure JSON enforced by `--output-schema` (`status`, `changed_files`, `verification`, `risks`, `next_steps`).
- **Git worktree isolation (`--worktree`)**: Spin up an isolated temporary Git worktree branch per task so parallel workers never step on each other or dirty your main working tree.
- **Built-in `doctor` & cross-platform installer**: Zero-dependency `install.py` works across macOS, Linux, and Windows and runs `codex_run.py doctor` automatically to validate readiness.

## Prerequisites

- **Antigravity** (user skills under `~/.gemini/config/skills`, plugins under `~/.gemini/config/plugins`)
- **OpenAI Codex CLI** installed and authenticated (`codex login`)
- **Python 3.10+** (**Python 3.11+** recommended for stdlib `tomllib` whitelist isolation; Python 3.10 falls back to `profile` mode; **Node.js is not required** for end users)

## Quick Start

Works on **macOS, Linux, and Windows**:

```bash
git clone https://github.com/Silhouette-my/codex-in-anti.git
cd codex-in-anti
python3 install.py
```

On macOS / Linux you can also run `./install.sh` (which delegates to `install.py`). By default, the installer:
1. Installs the standalone `codex-worker` skill to `~/.gemini/config/skills/codex-worker/` (skip with `--plugin-only`).
2. Installs the **Codex Worker MCP App plugin** to `~/.gemini/config/plugins/codex-in-anti/` (skip with `--skill-only`).
3. Copies `profiles/worker.config.toml` to `~/.codex/worker.config.toml` (skips if already present; pass `--force` to overwrite).
4. Runs `codex_run.py doctor` to verify CLI resolution, login auth, config parsing, and schema validity.

Override target paths with flags or environment variables:

```bash
python3 install.py --skills-dir /custom/skills --plugins-dir /custom/plugins --codex-home /custom/.codex
# Or via environment variables:
ANTIGRAVITY_SKILLS_DIR=/custom/skills ANTIGRAVITY_PLUGINS_DIR=/custom/plugins CODEX_HOME=/custom/.codex python3 install.py
```

## Usage

### 0. Native-Style Live Process Viewer (MCP App Plugin)

Once installed, Antigravity automatically discovers the `Codex Worker` plugin (`codex_worker` MCP server + `codex-worker-mcp` skill):
- When Antigravity delegates a task via `start_codex_task`, an **inline live Codex timeline card** appears immediately in the conversation (streaming commands, output, and file changes in real time) while the main agent awaits completion via `wait_codex_task` to review the diff.
- Open **Codex Worker** anytime from the side pane `+` menu or left sidebar (`open_codex_panel`) to monitor active runs or inspect past run timelines; tasks launched externally from the terminal via `codex_run.py` also **auto-sync live** into this panel.

### 1. Environment Health Check (`doctor`)

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py doctor
```

Checks Python version / `tomllib` availability, `codex` binary path & version, `auth.json` presence, `config.toml` / `worker.config.toml` parsing + whitelisted key summary, `handoff_schema.json`, and `git`.

### 2. Inside Antigravity (Natural Language)

Once installed, Antigravity automatically discovers the `codex-worker` skill. You can say:

- *"Delegate implementing module X to Codex, then review the diff and verify tests."*
- *"Use `--worktree` to have Codex implement these two independent modules in parallel before I merge."*
- *"Ask GPT via Codex to investigate the failing test in `tests/test_api.py` in read-only mode."*

### 3. Direct Wrapper CLI (`codex_run.py`)

Run a single-shot write task (default `isolated` whitelist + `workspace-write` + `--ephemeral`):

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --effort medium \
  --prompt "Implement parse_config() in src/config.py and run pytest tests/test_config.py."
```

Run inside an isolated Git worktree branch (keeps your main working tree clean):

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --worktree feat-config \
  --prompt "Refactor config loading, add unit tests, and run pytest."
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

### Wrapper Options

| Flag | Description |
| :--- | :--- |
| `-C`, `--cwd` | Working directory for Codex (default: `.`) |
| `--mode` | `write` (`workspace-write`) or `read-only` |
| `--config-mode` | `isolated` (whitelist via `--ignore-user-config`, default), `profile` (`-p worker`), or `inherit` |
| `--worktree [NAME]` | (`exec` only) Run in an isolated temporary Git worktree branch |
| `-m`, `--model` | Override Codex model |
| `--effort` | Override `model_reasoning_effort` (`low` \| `medium` \| `high`) |
| `--persist` | Keep Codex session on disk instead of `--ephemeral` |
| `--add-dir` | Grant additional writable directory (repeatable) |
| `--raw` | Disable JSON output schema and return plain text |
| `--search` | Enable live web search |
| `--quiet` | Suppress live stderr progress milestones |
| `--no-panel-sync` | Disable live state sync to the Codex Worker MCP panel |
| `--timeout` | Subprocess timeout in seconds (default: `600`) |
| `--log-dir` | Directory for per-run logs and artifacts (default: `$TMPDIR/codex-worker-runs`) |

## Live Progress & Output Format

While Codex runs, concise milestones stream to `stderr` (keeping `stdout` pure JSON):

```text
[codex] session=01a201b6-c7d9-7822-81d9-86c49f2aeabf
[codex] run: /bin/zsh -lc 'pytest tests/test_config.py'
[codex] done (exit=0): /bin/zsh -lc 'pytest tests/test_config.py'
[codex] turn done (in=18420, cached=12800, out=312)
```

Each run prints a JSON summary to `stdout` and archives raw JSONL events (`codex.log`), `output.json` (or `output.txt`), and `meta.json`:

```json
{
  "ok": true,
  "exit_code": 0,
  "timed_out": false,
  "duration_sec": 12.98,
  "model": "gpt-...",
  "session_id": "...",
  "tokens_used": 18732,
  "config_mode": "isolated",
  "usage": {
    "input_tokens": 18420,
    "cached_input_tokens": 12800,
    "output_tokens": 312,
    "reasoning_output_tokens": 0
  },
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

When `--worktree` is used, the JSON summary also includes a `worktree` object (`path`, `branch`, `base_repo`, `cleanup_command`).

## Benchmark

The controlled comparison below measured `-p worker` profile mode against the full desktop baseline (`isolated` whitelist mode goes even further by ignoring all custom user plugins and third-party MCP servers without maintaining a blacklist):

Measured sequentially on macOS with `codex-cli 0.171.0`. Both variants shared the exact same model, `model_reasoning_effort = "medium"`, `--output-schema`, prompts, and clean workspaces; the only difference was `--no-worker-profile` (desktop config with bundled plugins, hooks, memories, `chronicle`, and MCP servers enabled) vs. `-p worker` (`worker.config.toml`).

| Task | Mode | Baseline Time | Worker Time | Time Delta | Baseline Tokens | Worker Tokens | Token Delta | Compactions (B/W) |
| :--- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| **T1**: Read-only analysis of `codex_run.py` | `read-only` | 18.16s | 17.99s | -0.9% | 37,692 | 24,703 | **-34.5%** | 0 / 0 |
| **T2**: Create `math_utils.py` + 6+ `assert` tests & run | `write` | 21.60s | 17.86s | **-17.3%** | 35,516 | 12,730 | **-64.2%** | 0 / 0 |
| **T3**: Fix `parser.py` bugs to pass `test_parser.py` | `write` | 16.85s | 16.17s | -4.0% | 35,409 | 22,117 | **-37.5%** | 0 / 0 |
| **Total (3 tasks)** | - | **56.61s** | **52.02s** | **-8.1%** | **108,617** | **59,550** | **-45.2%** | 0 / 0 |

**What caused the difference?**
- Baseline runs fired `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` hooks on every tool call, injected desktop plugins/MCP schemas into context (even invoking a desktop MCP glob tool inside an empty directory during T2), and emitted `chronicle` feature warnings.
- With worker isolation, Codex skipped hooks, desktop plugins, memories, and MCP servers—going straight to shell inspection, patching, and running verification tests with all assertions verified passing post-run.
- In environments where desktop plugins or remote OAuth MCP servers also stall startup or trigger context compactions, latency and token savings can be even larger.

## Customization

Edit `~/.codex/worker.config.toml` to customize defaults:
- **Pin a model or reasoning effort**: Uncomment `model = "..."` or adjust `model_reasoning_effort` (or pass `-m` / `--effort` per run).
- **Whitelist behavior**: In default `isolated` mode, `codex_run.py` automatically forwards `model`, `model_provider`, active provider settings, and `[shell_environment_policy]` from `~/.codex/config.toml`. If you run on Python 3.10 (`profile` fallback) and have extra custom MCP servers in `~/.codex/config.toml`, add `[mcp_servers.<name>]\nenabled = false` to `worker.config.toml`.

## Repository Structure

```text
codex-in-anti/
├── install.py                                  # Cross-platform installer (macOS / Linux / Windows)
├── install.sh                                  # POSIX wrapper invoking install.py
├── plugin/
│   └── codex-in-anti/                          # Antigravity MCP App live-viewer plugin
│       ├── plugin.json                         # Plugin manifest
│       ├── mcp_config.json                     # MCP server config
│       ├── assets/
│       │   ├── app.html                        # Pre-bundled single-file MCP App HTML UI
│       │   └── logo.svg                        # Plugin icon
│       ├── server/
│       │   └── mcp_server.py                   # Zero-dependency Python MCP App server
│       ├── skills/
│       │   └── codex-worker-mcp/
│       │       └── SKILL.md                    # MCP App orchestration skill
│       └── ui-src/                             # Frontend TypeScript source (rebuild only)
├── profiles/
│   └── worker.config.toml                      # Codex CLI worker configuration
├── skill/
│   └── codex-worker/
│       ├── SKILL.md                            # Standalone CLI orchestration skill
│       ├── resources/
│       │   └── handoff_schema.json           # JSON Schema enforced via --output-schema
│       └── scripts/
│           └── codex_run.py                    # Zero-dependency Python wrapper CLI
├── README.md
├── README.en.md
└── LICENSE
```

## Community

Shared and discussed with the [LINUX DO](https://linux.do/) community.

## License

[MIT](LICENSE)
