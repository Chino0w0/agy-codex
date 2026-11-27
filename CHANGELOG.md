# Changelog

All notable changes to **Agy Codex (`agy-codex`)** are documented in this file.

## v1.3.0

- **Concise Model-Facing MCP Briefs**: Added `format_models_brief`, `format_start_brief`, `format_wait_or_status_brief`, and `format_stop_or_close_brief` in `mcp_server.py` so `list_codex_models` and task lifecycle tools return compact, human/LLM-readable plain-text dispatch briefs (~1–2 KB instead of raw 16 KB catalog JSON dumps) while keeping `structuredContent` backward-compatible for tests and app panels.
- **1-Shot Hook Dispatch Cheat Sheet**: Updated `routing_hook.py` (`SessionStart` and `PreToolUse`) to surface the compliant default model, active slot cap, `start_codex_task` 600s default, and `wait_codex_task` 120s poll pattern directly so Antigravity can dispatch without redundant `list_codex_models` round-trips.
- **Safe Split Timeouts (`600s` Worker vs `120s` Clamped Wait Poll)**: Capped `wait_codex_task.timeout` to `1..120` seconds (default `120`) to eliminate Antigravity's 180s MCP transport timeout errors while keeping `start_codex_task.timeout` at `600s` for multi-directory scans and complex subtasks.
- **Accurate Timeout Command Diagnostics & Worker Efficiency**: Stopped overwriting empty timeout handoffs with opening preamble text; timeouts now preserve `completed_commands` count and `codex.log` guidance in `error` and `work_digest`. Updated `SUBAGENT_ROLE_PREFIX` / `NESTED_SUBAGENT_PREFIX` and `profiles/worker.config.toml` so workers skip unrelated global skills and write concise handoff JSON before timeout.
- **Atomic `stats-ledger.jsonl` Upsert**: Updated `codex_telemetry.append_ledger_record()` to upsert by `run_id` atomically under `_LEDGER_LOCK` so closing/archiving a finished run never leaves duplicate lines on disk.

## v1.2.0

- **Out-of-the-Box Default Codex Routing**: Bundled cross-platform `plugin/agy-codex/hooks.json` (`SessionStart` + `PreToolUse`) and `server/routing_hook.py` so general subagent delegations default to `agy-codex` MCP right after installation.
- **Smart Fail-Open & Passthrough**: Zero-subprocess `<5ms` readiness check automatically falls back to native Antigravity subagents when Codex CLI or credentials are missing; Antigravity-exclusive capabilities (`browser`, `image-generator`, `flutter_a11y_agent`, `bundle-scrubber`, `session-analyst`) and explicit user native overrides always pass through.
- **4-Tier Compliant Default Model Resolution**: Resolves default subagent model via `chat-explicit -> preset.model -> defaults.model -> compliant ~/.codex/config.toml model` while enforcing `excluded_models` and `exclude_orchestrator_models`.
- **Per-Setting Controls & Tests**: Added `intercept_native_subagents` toggle and `native_passthrough_subagents` allowlist in **Models & Config Center**, cross-platform `install.py` command adaptation for `hooks.json`, and dedicated unit tests in `tests/test_routing_hook.py` and `tests/test_installer.py`.

## v1.1.2

- **4-Tab Workbench & High-Contrast Linkage Switch**: Upgraded the MCP App UI to four focused tabs (**Active Monitor**, **History**, **Usage Stats**, **Models & Config**) with prominent Codex linkage toggle buttons in the topbar and all views.
- **History & Telemetry Separation**: Grouped History strictly by parent Antigravity sessions with 4-row card hierarchy, configurable `1` to `168` hour TTL (24h default), and permanent `stats-ledger.jsonl` analytics (zero cost estimation).
- **Concurrency & Windows Pipe Fixes**: Upgraded `mcp_server.py` to concurrent `ThreadPoolExecutor` (`serve_stdio`), detached child `stdin=subprocess.DEVNULL` in all `subprocess.run()` sites to fix Windows pipe deadlocks, injected subprocess-only `safe.directory=*` for worktrees, and added `close_codex_task` + 6-part dispatch contract (`context_files`, `acceptance_criteria`, `non_goals`) + accurate `partial` status handling.

## v1.0.0

- Initial fork from `Silhouette-my/codex-in-anti` into the standalone Antigravity 2.0 `agy-codex` plugin with 4-source model discovery, whitelist-isolated `codex exec` runner, rolling parallel worktree orchestration, cross-platform installers, and prebuilt single-file MCP App frontend.
