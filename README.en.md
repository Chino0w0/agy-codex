# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**A native Antigravity 2.0 ↔ OpenAI Codex CLI multi-model subagent workbench plugin.**

> Forked from [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti) under the MIT License. **Agy Codex** upgrades the whitelist-isolated MCP App architecture into a **true out-of-the-box** Antigravity plugin: default Codex subagent routing on install, 4-source model discovery, instant Codex/native switching, and a clean 4-tab native workbench.

---

## Features

Let **Antigravity** act as orchestrator, code reviewer, and final verifier while your local **Codex CLI** handles general subagent delegations out of the box:

- **True Out-of-the-Box Default Codex Routing (Switchable Anytime)**:
  - Bundled **`hooks.json` (`SessionStart` + `PreToolUse`)** works immediately after install: whenever the **Codex Linkage** switch is ON (enabled by default), general subagent delegations (coding, refactoring, debugging, review, research, parallel tasks) route to `agy-codex` MCP automatically without requiring you to mention "Codex" manually in chat;
  - High-contrast **Codex Linkage** toggle buttons sit in the persistent topbar and every view, restoring native Antigravity subagents immediately when turned OFF;
  - Smart passthrough & fail-open safety: Antigravity-exclusive subagents (`browser`, `image-generator`, etc.) and explicit user overrides always pass through natively, and if Codex CLI is not installed or logged in yet, the hook checks readiness in `<5ms` and fails open to native subagents without blocking new users;
  - **Compliant Default Model Inheritance**: automatically resolves `chat-explicit -> preset.model -> workbench defaults.model -> compliant ~/.codex/config.toml default model` (filtered by Orchestrator guard and exclusion list) so new users can run tasks immediately with zero UI setup.
- **4-Source Flexible Model Mapping & Orchestrator Guard**:
  - Merges models from `custom_models` (UI-editable) > `model_catalog_json` (`auto`/`custom`/`off`) > `~/.codex/models_cache.json` > `config.toml`, plus freeform model strings anywhere;
  - Whitelist-isolated (`--ignore-user-config`) execution skips heavy desktop plugins, MCP servers, hooks, and memories while forwarding model, provider, proxy, Windows sandbox settings, subprocess-scoped `safe.directory=*`, and opted-in MCP namespaces;
  - Dual Orchestrator protection (`exclude_orchestrator_models`), extended reasoning efforts (`none / minimal / low / medium / high / xhigh / max / ultra` and custom catalog levels), and configurable **`catalog_mode`**, **`effort_policy`**, **`context_window_policy`**, **`native_passthrough_subagents`**, and **`excluded_models`**.
- **Concurrent Subagent Orchestration, Conversation Isolation & Health Watchdog (`v1.5.0`)**:
  - Model-visible MCP tools: `list_codex_models`, `start_codex_task` (with `context_files`, `acceptance_criteria`, `non_goals`, default `timeout=0` unlimited runtime backed by live stall/doom-loop health monitoring and stderr root-cause extraction), `get_codex_task_status`, `wait_codex_task` (`run_ids` + `wait_mode="any"|"all"`, safely capped at 120s per poll to avoid MCP transport timeouts), `stop_codex_task`, `close_codex_task`, and `open_codex_panel`;
  - **Multi-Conversation Isolation**: When running multiple Antigravity conversations in parallel, the right `aux_pane` automatically scopes Active Monitor and batch archiving to the current conversation (`conversation_id` / `parent_session_id`), with a one-click toggle to inspect all parallel conversations when needed;
  - Concise model-facing dispatch briefs cut MCP text payload size by ~90%, hardened `run_id` path boundaries, cross-process OS kernel-locked `stats-ledger.jsonl` upserts, and staged rollback-safe cross-platform installers;
  - Concurrent non-blocking `serve_stdio` JSON-RPC server, cross-process `pid` liveness checks, ephemeral `--ephemeral --thread-source subagent` execution, Windows stdio pipe deadlock fixes, and accurate `partial` handoff detection;
  - Ships a concise always-on `rules/AGENTS.md`, three progressive-disclosure skills (`codex-dispatch`, `codex-parallel-worktree`, `codex-second-opinion`), and 6 customizable role presets.
- **Native 4-Tab MCP App Workbench (Direct Multi-Turn Chat + Online Update Sync)**:
  - Uses Antigravity's official **MCP App** surfaces (sidebar `plugins` entry, right `aux_pane`, and inline chat card) with zero Electron CDP injection;
  - **Active Monitor & User-Direct Multi-Turn Chat (U2A)**: Single persistent Codex linkage switch and spinning force-refresh button with toast feedback in the topbar; narrow side-pane uses a full-width capsule popover switcher (`< >` stepping, background alert dots, and conversation scope pill); click **+ New Chat** to message Codex directly (`persist=true`, `raw=true`, works even when auto-delegation is paused) with continuous multi-turn follow-ups and one-click handoff to Antigravity for review;
  - **History**: Grouped strictly by Antigravity parent session in clean 4-row cards (status + title, role/archive badges, 2-line digest, compact telemetry line) with configurable 1 to 168h retention (24h default) and reopen support;
  - **Usage Stats**: Tracks Antigravity-dispatched runs via permanent compact `stats-ledger.jsonl` (tokens, cache hit rate, daily trend, model/role/provider/effort breakdowns, copyable error snippets, no cost estimation);
  - **Models & Config + Online Update Sync**: Built-in pure-stdlib **GitHub Online Update & Sync** card (GitHub Releases + `main` fallback, 12h cache, strict host/size/Zip-Slip verification, and atomic in-place sync via `install.py`, plus topbar badge dot and dismissible banner); selective `?` help tooltips, instant toggles, forwarded MCP checkboxes, presets editor, **Dry-Run Redacted Command Preview**, and `doctor` diagnostics.
- **Zero-Bloat Packaging**: 100% Python stdlib backend + pre-bundled single-file `assets/app.html` (no Node.js required for end users), with one-step CLI or Agent-driven install and clean uninstall.

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
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py models --query gpt --exclude-filtered
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py preview --preset debug
```

### Upgrade & Reload

```bash
git pull --ff-only
```

- **Windows**: `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`
- **macOS / Linux**: `sh ./install.sh`
- Existing `~/.codex/worker.config.toml` and `workbench-config.json` settings are preserved. Refresh `agy-codex` in **MCP Servers** settings (or reopen Antigravity) after upgrading an active session.

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

## Quick Start (Out-of-the-Box Usage)

1. **Zero-Config Default Routing**: With **Codex Linkage** ON by default (`enabled=true`), just ask Antigravity to split modules, implement features, fix bugs, review diffs, or investigate code. Built-in `hooks.json` routes general subagent tasks to Codex CLI automatically and opens the live side-pane monitor.
2. **Switch Between Codex & Native Anytime**: Use the high-contrast **Codex Linkage** button in the persistent topbar or inside any of the 4 workbench views to pause or resume Codex dispatch immediately, or type "without Codex / use native subagent" in chat for a single-turn bypass.
3. **Compliant Default Model Resolution**: Resolves in order `chat-explicit -> preset.model -> workbench defaults.model -> compliant ~/.codex/config.toml default`. When `exclude_orchestrator_models=true` leaves no compliant default, Antigravity calls `list_codex_models` and lets you pick first.
4. **Session-Grouped History & Usage Stats**: Closed task tabs are organized cleanly under their parent Antigravity conversation in **History** (default 24h TTL, configurable 1 to 168h), while **Usage Stats** reads the permanent `stats-ledger.jsonl` for call counts, success rates, and token cache hit trends (zero cost estimation).

---

## Key Settings Reference

Editable in **Models & Config Center** and persisted at `~/.gemini/antigravity/plugin_data/agy-codex/workbench-config.json`:

| Setting | Default | Purpose |
| :--- | :--- | :--- |
| `enabled` | `true` | Master **Codex Linkage** switch (turn off to revert to native Antigravity subagents anytime) |
| `intercept_native_subagents` | `true` | Automatically redirect general `invoke_subagent` calls to `agy-codex` while `enabled=true` |
| `exclude_orchestrator_models` | `true` | Dual anti-nesting guard excluding `orchestrator` models from Codex subagent selection |
| `defaults.model` | `null` | Pinned default Codex model (`null` inherits compliant `~/.codex/config.toml` default) |
| `mapping.native_passthrough_subagents` | `["browser", "image-generator"]` | Native-only subagents always allowed natively |
| `mapping.retention_hours` | `24` | Hours to retain full archived run event logs (`1` to `168`) |
| `mapping.forwarded_mcp_servers` | `[]` | Opt-in allowlist of Codex MCP servers to forward into isolated worker runs |

---

## Troubleshooting (FAQ)

- **What if I install Agy Codex before installing or logging into Codex CLI?**
  The `PreToolUse` hook runs a `<5ms` local readiness check (CLI binary + `auth.json` or configured custom provider). If Codex CLI is not ready yet, it automatically fails open to native Antigravity subagents and prints a quick setup notice on session start.
- **Why do browser or image tasks still use Antigravity native subagents?**
  Capabilities unique to Antigravity such as `browser` and `image-generator` (plus accessibility/diagnostic built-ins) are allowed through automatically and configurable via `mapping.native_passthrough_subagents`.
- **How do I inspect environment health?**
  Run `python ~/.gemini/config/plugins/agy-codex/server/codex_run.py doctor` to verify `python`, `codex_bin`, `config`, `auth`, `schema`, and `git`.

---

## Project Structure

```text
agy-codex/
├── CHANGELOG.md                                # Release changelog
├── docs/
│   ├── INSTALL_AGENT.md                        # Agent auto-install guide
│   └── superpowers/specs/                      # Design docs
├── install.py / install.ps1 / install.sh       # Cross-platform installer
├── uninstall.py / uninstall.ps1 / uninstall.sh # Cross-platform uninstaller
├── profiles/
│   └── worker.config.toml                      # Lightweight Codex worker config template
├── plugin/
│   └── agy-codex/                              # Native Antigravity plugin bundle
│       ├── plugin.json                         # Plugin manifest (v1.4.0)
│       ├── mcp_config.json                     # MCP server entrypoint
│       ├── hooks.json                          # SessionStart / PreToolUse default routing hooks
│       ├── assets/
│       │   ├── app.html                        # Prebuilt single-file MCP App UI
│       │   └── logo.svg                        # Plugin icon
│       ├── rules/
│       │   └── AGENTS.md                       # Default Codex routing & 6-part contract rules
│       ├── skills/
│       │   ├── codex-dispatch/SKILL.md         # Bounded single-task dispatch skill
│       │   ├── codex-parallel-worktree/SKILL.md# Parallel worktree & rolling verification skill
│       │   └── codex-second-opinion/SKILL.md   # Cross-model review & root-cause skill
│       ├── server/
│       │   ├── mcp_server.py                   # Concurrent stdio MCP App server
│       │   ├── routing_hook.py                 # Lifecycle routing & fail-open readiness hook
│       │   ├── codex_run.py                    # Runner wrapper + doctor/models/preview CLI
│       │   ├── codex_catalog.py                # 4-source catalog, presets & whitelist engine
│       │   ├── codex_telemetry.py              # stats-ledger persistence & archive pruning
│       │   └── handoff_schema.json             # Structured handoff JSON Schema
│       └── ui-src/                             # Frontend TypeScript sources
└── tests/                                      # Python unittest suite (including hooks & installer)
```

---

## Acknowledgements & License

- Upstream project: [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)
- License: [MIT License](LICENSE)
