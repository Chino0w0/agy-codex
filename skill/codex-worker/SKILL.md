---
name: codex-worker
description: >-
  Delegate bounded coding, debugging, refactoring, or independent code review tasks to
  OpenAI Codex CLI (GPT) using a lightweight worker profile, strict sandbox controls, and
  structured JSON handoffs. Trigger when the user asks to delegate work to Codex / GPT,
  run a second-opinion code review with Codex, or orchestrate multi-model coding workflows.
---

# Codex Worker Delegation Skill

Use this skill when Antigravity acts as the **orchestrator & verifier** and delegates bounded execution or independent review tasks to OpenAI Codex CLI (`codex`).

## Core Principles

1. **Always use the wrapper script**: Invoke `~/.gemini/config/skills/codex-worker/scripts/codex_run.py` instead of raw `codex exec`. It automatically:
   - resolves the `codex` binary across `CODEX_BIN`, `PATH`, `/opt/homebrew/bin/codex`, `/usr/local/bin/codex`, and `~/.local/bin/codex`;
   - applies `~/.codex/worker.config.toml` (`-p worker`) when present to disable desktop plugins, GUI MCPs, hooks, memories, chronicle, and nested subagent spawning;
   - enforces explicit sandbox boundaries (`workspace-write` or `read-only`);
   - adds `--skip-git-repo-check` outside Git repos;
   - enforces structured JSON delivery via `~/.gemini/config/skills/codex-worker/resources/handoff_schema.json`.
2. **Never trust self-reported results blindly**: After Codex finishes a write task, inspect `git status` / `git diff` (or read changed files) and verify critical tests before reporting completion to the user.
3. **Give each file one writer**: Do not edit the same files concurrently while Codex is running on them.

## When to Delegate vs. Handle Directly

- **Delegate to Codex (`exec`)**: Self-contained module implementation, boilerplate/script generation, targeted bug fixes with clear repro steps, or parallelizable tasks on disjoint files.
- **Delegate to Codex (`review`)**: Independent second-opinion code review on uncommitted diffs, a branch, or a commit (`read-only` sandbox).
- **Handle directly**: Trivial 1-2 line edits, quick file reads, or tasks requiring Antigravity-only tools (browser, MCP sidecars, user Q&A).

## Standard Commands

### 1. Single-Shot Implementation Task (Default: `workspace-write` + `--ephemeral`)
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<workdir>" \
  --prompt "Goal: ... Scope: ... Constraints: ... Verification: ..."
```

### 2. Multi-Turn Task (Persist Session & Resume)
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

### 3. Read-Only Analysis or Code Review
Read-only investigation:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C "<workdir>" \
  --mode read-only \
  --prompt "Analyze ... and list root causes without modifying files."
```
Built-in Git diff review:
```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py review \
  -C "<workdir>" \
  --uncommitted \
  --raw
```

### 4. Tuning Model, Reasoning Effort, and Scope
- Switch model: `-m <model_name>`
- Adjust reasoning effort: `--effort low|medium|high` (use `low` for mechanical edits, `high` for hard bugs)
- Grant extra writable directory: `--add-dir <dir>`
- Long prompt from file: `--prompt-file <path>`

## Prompting Checklist (Before Delegating)

Every delegation prompt should state:
1. **Goal**: Exact deliverable.
2. **Scope**: Files/directories allowed to modify (and what NOT to touch).
3. **Context**: Key conventions, constraints, or error logs (avoid pasting entire files Codex can read itself).
4. **Verification**: Exact test/build/lint commands Codex must run before finishing.

## Post-Run Verification Checklist

1. Check the JSON summary printed by `codex_run.py`: `ok`, `result.status`, `result.changed_files`, `result.verification`, `result.risks`, `tokens_used`, and `log_file`.
2. If `ok` is `false` or `result.status != "done"`, inspect `log_file` before deciding whether to `resume` or take over directly.
3. For any code changes, inspect the actual diff (`git diff` or `read_file`) and re-run key verification checks if evidence is incomplete.
