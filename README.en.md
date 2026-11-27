# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**A native Antigravity 2.0 ↔ OpenAI Codex CLI multi-model subagent workbench plugin.**

> Forked from [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti) under the MIT License. **Agy Codex** upgrades the whitelist-isolated MCP App architecture into an out-of-the-box Antigravity plugin with 4-source model discovery, Orchestrator anti-nesting protection, 6-part self-contained dispatch contracts, and a 4-tab Antigravity-linked workbench.

---

## Features

Let **Antigravity** act as orchestrator, code reviewer, and final verifier while delegating bounded tasks to any specified model in your local **Codex CLI**:

- **4-Source Flexible Model Mapping & Orchestrator Guard**:
  - Merges models from `custom_models` (UI-editable) > `model_catalog_json` (`auto`/`custom`/`off`) > `~/.codex/models_cache.json` > `config.toml`, plus freeform model strings anywhere;
  - Whitelist-isolated (`--ignore-user-config`) execution skips heavy desktop plugins, MCP servers, hooks, and memories while forwarding model, provider, proxy, Windows sandbox settings, subprocess-scoped `safe.directory=*`, and opted-in MCP namespaces;
  - Dual Orchestrator protection (`exclude_orchestrator_models`), extended reasoning efforts (`none / minimal / low / medium / high / xhigh / max / ultra` and custom catalog levels), and configurable **`catalog_mode`**, **`effort_policy`**, **`context_window_policy`**, and **`excluded_models`**.
- **Concurrent Subagent Orchestration & Contracted Dispatch (`v1.1.2`)**:
  - Model-visible MCP tools: `list_codex_models`, `start_codex_task` (with `context_files`, `acceptance_criteria`, `non_goals`), `get_codex_task_status`, `wait_codex_task` (`run_ids` + `wait_mode="any"|"all"`), `stop_codex_task`, `close_codex_task`, and `open_codex_panel`;
  - Concurrent non-blocking `serve_stdio` JSON-RPC server, cross-process `pid` liveness checks, ephemeral `--ephemeral --thread-source subagent` execution, Windows stdio pipe deadlock fixes, and accurate `partial` handoff detection;
  - Ships a concise always-on `rules/AGENTS.md`, three progressive-disclosure skills (`codex-dispatch`, `codex-parallel-worktree`, `codex-second-opinion`), and 6 customizable role presets.
- **Native 4-Tab MCP App Workbench (Zero Client Injection, Instant Toggle Everywhere)**:
  - Uses Antigravity's official **MCP App** surfaces (sidebar `plugins` entry, right `aux_pane`, and inline chat card) with zero Electron CDP injection, featuring a high-contrast **Codex linkage switch** in both the topbar and every view so users can switch between Codex and native Antigravity subagents anytime;
  - **Active Monitor**: Running-first tab ordering, live incremental event sync, timeout countdown, expandable full dispatch prompt, click-to-open files, and one-click archive/clear controls;
  - **History**: Grouped strictly by Antigravity parent session in clean 4-row cards (status + title, role/archive badges, 2-line digest, compact telemetry line) with configurable 1 to 168h retention (24h default) and reopen support;
  - **Usage Stats**: Tracks Antigravity-dispatched runs via permanent compact `stats-ledger.jsonl` (tokens, cache hit rate, daily trend, model/role/provider/effort breakdowns, copyable error snippets, no cost estimation);
  - **Models & Config Center**: Selective `?` help tooltips, instant Codex linkage & Orchestrator switches, forwarded MCP checkboxes, presets editor, **Dry-Run Redacted Command Preview**, and `doctor` diagnostics.
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
