---
name: codex-worker
description: >-
  Delegate bounded coding, debugging, or refactoring tasks to OpenAI Codex CLI (GPT)
  using whitelist config isolation, strict sandbox controls, live stderr progress streaming,
  optional Git worktree isolation, and structured JSON handoffs. Trigger when the user asks
  to delegate implementation or debugging work to Codex / GPT.
---

# Codex Worker Delegation Skill

Use this skill when Antigravity acts as the **orchestrator, code reviewer, and final verifier**, and delegates bounded execution tasks to OpenAI Codex CLI (`codex`).

## Core Architecture & Responsibilities

1. **Main agent owns orchestration, Code Review, and final verification**:
   - Antigravity designs the task, reviews diffs, performs Code Review, and verifies tests/checks before reporting completion.
   - Codex CLI acts strictly as an isolated, bounded execution worker (`exec` / `resume`), not as an autonomous reviewer replacing the main agent.
2. **Always invoke the wrapper script**: Use `~/.gemini/config/skills/codex-worker/scripts/codex_run.py` instead of raw `codex exec`. It automatically:
   - locates `codex` across macOS, Linux, and Windows (including non-login shells);
   - runs in **`isolated` whitelist mode** by default (`--ignore-user-config`), preserving `$CODEX_HOME/auth.json` and extracting only `model`, `model_provider`, provider settings, reasoning effort, and proxy env settings—eliminating 100% of desktop plugins, MCPs, hooks, memories, and chronicle overhead;
   - streams live execution milestones (`session=...`, `run: ...`, `done (exit=...)`, `turn done (...)`) to `stderr` via `--json` while keeping `stdout` pure JSON;
   - enforces explicit sandbox boundaries (`workspace-write` or `read-only`) and adds `--skip-git-repo-check` outside Git repos;
   - enforces structured JSON delivery via `~/.gemini/config/skills/codex-worker/resources/handoff_schema.json`.
3. **Give each file one writer**: Never edit the same files concurrently while Codex is running on them. For parallel tasks in a Git repo, pass `--worktree`.

## When to Delegate vs. Handle Directly

- **Delegate to Codex (`exec`)**: Self-contained module implementation, boilerplate/script generation, targeted bug fixes with clear repro steps, or parallelizable tasks using `--worktree`.
- **Handle directly (Antigravity)**: Code Review, architectural decisions, trivial 1-2 line edits, quick file reads, or tasks requiring Antigravity-only tools (browser, MCP sidecars, user Q&A).

## Standard Commands

### 0. Health Check (`doctor`)
Verify `codex` CLI, login auth, config parsing, schema validity, and `git` availability:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py doctor
```

### 1. Single-Shot Implementation Task (Default: `isolated` + `workspace-write` + `--ephemeral`)
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<workdir>" \
  --prompt "Goal: ... Scope: ... Constraints: ... Verification: ..."
```

### 2. Parallel / Clean-Tree Execution with Git Worktree (`--worktree`)
Runs Codex in a fresh isolated Git worktree (`$TMPDIR/codex-worktrees/<name>`) on a new branch `codex/<name>` from `HEAD`, leaving your main working tree untouched:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<git-repo>" \
  --worktree feat-module \
  --prompt "Implement module X and run unit tests."
```
The output JSON includes `worktree.path`, `worktree.branch`, and `worktree.cleanup_command`. Review the diff on that branch before merging or cherry-picking.

### 3. Multi-Turn Task (Persist Session & Resume)
Start with `--persist` so `session_id` is saved:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<workdir>" \
  --persist \
  --prompt "<initial task>"
```
Follow up on the same session using the returned `session_id`:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py resume \
  -C "<workdir>" \
  "<session_id>" \
  --prompt "<follow-up fix or refinement>"
```

### 4. Read-Only Investigation
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<workdir>" \
  --mode read-only \
  --prompt "Investigate ... and list root causes without modifying files."
```

### 5. Tuning Model, Config Mode, and Scope
- Switch config isolation mode: `--config-mode isolated|profile|inherit` (default: `isolated`)
- Switch model: `-m <model_name>`
- Adjust reasoning effort: `--effort low|medium|high`
- Grant extra writable directory: `--add-dir <dir>`
- Suppress live stderr milestones: `--quiet`
- Long prompt from file: `--prompt-file <path>`

## Prompting Checklist (Before Delegating)

Every delegation prompt should state:
1. **Goal**: Exact deliverable.
2. **Scope**: Files/directories allowed to modify (and what NOT to touch).
3. **Context**: Key conventions, constraints, or error logs (avoid pasting entire files Codex can read itself).
4. **Verification**: Exact test/build/lint commands Codex must run before finishing.

## Post-Run Verification & Review Checklist (Main Agent)

1. Inspect the JSON summary printed by `codex_run.py`: `ok`, `result.status`, `result.changed_files`, `result.verification`, `result.risks`, `tokens_used`, `usage`, and `log_file`.
2. If `ok` is `false` or `result.status != "done"`, inspect `log_file` before deciding whether to `resume` or take over directly.
3. **Always review changes yourself**: Run `git diff` (or `read_file` on changed files / worktree diff) and re-run key verification checks before reporting completion to the user.
