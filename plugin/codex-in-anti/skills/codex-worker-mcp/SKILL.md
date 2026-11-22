---
name: codex-worker-mcp
description: >-
  Delegate bounded coding, debugging, or refactoring tasks to OpenAI Codex CLI (GPT)
  using the Codex Worker MCP App so the user sees a live, collapsible Codex-native
  process stream inline in Antigravity. Trigger when delegating work to Codex / GPT
  and the `codex_worker` MCP tools (`start_codex_task`, `wait_codex_task`) are available.
---

# Codex Worker MCP Orchestration

Use the `codex_worker` MCP App tools when delegating bounded execution tasks to OpenAI Codex CLI (GPT) so the user gets a **live, collapsible Codex-style process viewer** directly in Antigravity.

## Core Architecture & Responsibilities

1. **Main agent owns orchestration, Code Review, and final verification**:
   - Antigravity designs the task, inspects diffs, performs Code Review, and verifies tests/checks before reporting completion.
   - Codex CLI acts strictly as a whitelist-isolated, bounded execution worker.
2. **Always pair `start_codex_task` + `wait_codex_task`**:
   - First call `start_codex_task` with `prompt`, `cwd`, and optional `mode` (`write` | `read-only`), `effort` (`low` | `medium` | `high`), `worktree`, `persist`, or `session_id` (to resume a persistent session).
   - `start_codex_task` returns immediately (~50ms) with a `run_id` and mounts the live inline process viewer in the conversation.
   - Immediately call `wait_codex_task(run_id=...)` to wait for completion and receive the structured handoff JSON (`ok`, `status`, `changed_files`, `result`, `usage`, `worktree`, `log_file`).
3. **Side-pane dashboard (`open_codex_panel`)**:
   - Call `open_codex_panel` when the user asks to view the Codex Worker monitor or review past/parallel runs.
4. **Never trust self-reported results blindly**:
   - After `wait_codex_task` finishes, inspect `git diff` or read the changed files and verify critical tests yourself before reporting completion.
