# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**A native Antigravity 2.0 ↔ OpenAI Codex CLI multi-model subagent workbench plugin.**

> Forked from [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti) under the MIT License. **Agy Codex** upgrades the whitelist-isolated MCP App architecture into an out-of-the-box Antigravity plugin with 4-source flexible model discovery, configurable mapping policies, extended reasoning efforts, rolling parallel worktree orchestration, and a Powertools-inspired 3-tab native workbench.

---

## Features

Let **Antigravity** act as orchestrator, code reviewer, and final verifier while delegating bounded tasks to any specified model in your local **Codex CLI**:

- **4-Source Flexible Model & Provider Mapping (Works With or Without `model_catalog_json`)**:
  - Merges models from `custom_models` (UI-editable) > `model_catalog_json` (`auto`/`custom`/`off`) > `~/.codex/models_cache.json` > `config.toml`, plus freeform model strings anywhere;
  - Whitelist-isolated (`--ignore-user-config`) execution skips desktop plugins, MCP servers, hooks, and memories while forwarding model, provider, proxy, Windows sandbox settings (`[windows]`, `[sandbox_workspace_write]`, `allow_login_shell`), and user-specified `extra_whitelist_keys`;
  - Configurable **`catalog_mode`**, **`effort_policy`** (`clamp_with_warning` | `passthrough`), **`context_window_policy`** (`same_model_only` | `always_forward` | `never_forward`), and **`excluded_models`**;
  - Supports extended reasoning efforts (`none / minimal / low / medium / high / xhigh / max / ultra` and custom catalog levels) plus per-task `model`, `provider`, and role `preset` overrides.
- **Parallel Subagent Orchestration & Cross-Model Collaboration**:
  - Model-visible MCP tools: `list_codex_models`, `start_codex_task`, `get_codex_task_status`, `wait_codex_task` (supports `run_ids` + `wait_mode="any"|"all"` for rolling verification; wait timeout is never misreported as task failure), `stop_codex_task` (full process-tree termination), and `open_codex_panel`;
  - Strict 3-tier identity traceability (`requested` vs `effective` vs `observed`);
  - Ships a concise always-on `rules/AGENTS.md`, three progressive-disclosure skills (`codex-dispatch`, `codex-parallel-worktree`, `codex-second-opinion`), and 6 customizable role presets (`explore`, `coding`, `frontend`, `review`, `debug`, `fast`).
- **Native 3-Tab MCP App Workbench (Zero Client Injection, Update-Safe)**:
  - Uses Antigravity's official **MCP App** surfaces (sidebar full-page, right side pane, and inline chat card) with zero Electron CDP injection—completely conflict-free with `agy-powertools`;
  - **Subagents Monitor**: Active slot tracking (`max_concurrent_runs`), live collapsible process timeline, worktree cleanup helpers, click-to-open changed files, host review/context actions, and local Codex session context occupancy bars + spawn trees;
  - **Usage Analytics**: Merges Antigravity runs with read-only local `~/.codex` sessions (`state_*.sqlite` + cached rollout JSONL tails) for tokens, cache hit rate, duration, success rate, daily trends, and model/provider/effort breakdowns (no price/cost estimation);
  - **Models & Config Center**: Searchable directory, live mapping policy editor, preset & custom-model editors, **Dry-Run Redacted Command Preview**, and `doctor` diagnostics.
- **Zero-Bloat Packaging**: 100% Python stdlib backend + pre-bundled single-file `assets/app.html` (no Node.js required for end users).

---

## Prerequisites

- **Antigravity 2.0** (discovers global plugins under `~/.gemini/config/plugins/`)
- **OpenAI Codex CLI** installed and configured (official login or custom provider)
- **Python 3.10+** (**Python 3.11+** recommended for stdlib `tomllib`; end users do not need Node.js)

---

## Installation & Uninstall

### Option 1: Copy-Paste Prompt for Your Agent (Recommended)

> Please install Agy Codex from https://github.com/Chino0w0/agy-codex . Read README.en.md and docs/INSTALL_AGENT.md first, run the cross-platform installer and `doctor` health check, keep my existing plugins, Codex config, and chat history untouched, and do not force-close the client. Report the doctor results and how to activate it when done.

See [docs/INSTALL_AGENT.md](docs/INSTALL_AGENT.md).

### Option 2: Manual CLI Install

```powershell
# Windows (PowerShell)
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
# macOS / Linux
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
sh ./install.sh
```

### CLI Helpers

```bash
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py doctor
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py models --include-hidden
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py preview --preset debug
```

### Uninstall

```powershell
# Windows (pass --keep-data to retain presets and telemetry cache)
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

```bash
# macOS / Linux
sh ./uninstall.sh
```

---

## Acknowledgements & License

- Upstream project: [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)
- License: [MIT License](LICENSE)
