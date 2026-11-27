# Changelog

All notable changes to **Agy Codex (`agy-codex`)** are documented in this file.

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
