#!/usr/bin/env python3
"""Wrapper CLI for delegating bounded coding/review tasks to OpenAI Codex CLI.

Features:
- Resolves the `codex` binary reliably across non-login shells and common install paths.
- Layers `$CODEX_HOME/worker.config.toml` via `-p worker` when present.
- Enforces explicit sandbox mode (`workspace-write` or `read-only`).
- Auto-adds `--skip-git-repo-check` when target cwd is outside a Git repo.
- Enforces structured JSON handoffs via `--output-schema` and `-o` by default.
- Captures session_id, tokens_used, duration, and full logs for auditing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = SKILL_DIR / "resources" / "handoff_schema.json"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
WORKER_PROFILE_PATH = CODEX_HOME / "worker.config.toml"
DEFAULT_LOG_ROOT = Path(tempfile.gettempdir()) / "codex-worker-runs"


def find_codex_bin() -> str:
    """Locate the `codex` CLI binary across environment overrides, PATH, and standard paths."""
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin

    candidates: list[str] = []
    which_bin = shutil.which("codex")
    if which_bin:
        candidates.append(which_bin)
    candidates.extend(
        [
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            str(Path.home() / ".local" / "bin" / "codex"),
        ]
    )

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    raise FileNotFoundError(
        "codex CLI not found. Checked CODEX_BIN, PATH, /opt/homebrew/bin/codex, "
        "/usr/local/bin/codex, and ~/.local/bin/codex."
    )


def is_git_repo(path: Path) -> bool:
    """Return True if `path` is inside a Git working tree."""
    try:
        res = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    """Read task instructions from `--prompt-file`, `--prompt`, or piped stdin."""
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")
    if prompt is not None:
        return prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("A prompt is required via --prompt, --prompt-file, or stdin.")


def parse_telemetry(log_text: str) -> dict[str, object]:
    """Extract session ID, token usage, and resolved model from Codex CLI logs."""
    session_match = re.search(r"session id:\s*([0-9a-fA-F-]+)", log_text)
    tokens_match = re.search(r"tokens used\s*\n\s*([0-9,]+)", log_text)
    model_match = re.search(r"^model:\s*(.+)$", log_text, re.MULTILINE)
    return {
        "session_id": session_match.group(1) if session_match else None,
        "tokens_used": int(tokens_match.group(1).replace(",", "")) if tokens_match else None,
        "model": model_match.group(1).strip() if model_match else None,
    }


def append_shared_codex_flags(
    cmd: list[str],
    args: argparse.Namespace,
    cwd: Path,
    output_file: Path,
    *,
    sandbox: str,
    allow_ephemeral: bool,
    allow_schema: bool,
    allow_extra_dirs: bool,
    allow_search: bool,
    check_git: bool,
) -> None:
    """Append common Codex CLI flags consistently across subcommands."""
    if not args.no_worker_profile and WORKER_PROFILE_PATH.is_file():
        cmd.extend(["-p", "worker"])
    cmd.extend(["-s", sandbox])
    if args.model:
        cmd.extend(["-m", args.model])
    if args.effort:
        cmd.extend(["-c", f'model_reasoning_effort="{args.effort}"'])
    if allow_search and args.search:
        cmd.append("--search")
    cmd.extend(["-C", str(cwd)])
    if allow_extra_dirs:
        for extra_dir in args.add_dir or []:
            cmd.extend(["--add-dir", str(Path(extra_dir).resolve())])
    if check_git and not is_git_repo(cwd):
        cmd.append("--skip-git-repo-check")
    if allow_ephemeral and not args.persist:
        cmd.append("--ephemeral")
    if allow_schema and not args.raw and DEFAULT_SCHEMA.is_file():
        cmd.extend(["--output-schema", str(DEFAULT_SCHEMA)])
    cmd.extend(["-o", str(output_file)])


def run_codex(
    cmd: list[str], log_file: Path, output_file: Path, timeout: int | None
) -> dict[str, object]:
    """Execute the Codex CLI command, capture logs, and return structured telemetry."""
    env = os.environ.copy()
    path_parts = env.get("PATH", "").split(os.pathsep)
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")):
        if prefix not in path_parts:
            path_parts.insert(0, prefix)
    env["PATH"] = os.pathsep.join(p for p in path_parts if p)

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=timeout,
        )
        exit_code = proc.returncode
        combined_log = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        out = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        err = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        combined_log = out + err + f"\n[codex_run] Timed out after {timeout}s\n"

    duration_sec = round(time.monotonic() - start, 2)
    log_file.write_text(combined_log, encoding="utf-8")

    raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
    parsed_result: object = raw_output
    if raw_output:
        try:
            parsed_result = json.loads(raw_output)
        except json.JSONDecodeError:
            parsed_result = raw_output

    telemetry = parse_telemetry(combined_log)
    return {
        "ok": exit_code == 0 and not timed_out,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_sec": duration_sec,
        "model": telemetry["model"],
        "session_id": telemetry["session_id"],
        "tokens_used": telemetry["tokens_used"],
        "result": parsed_result,
        "output_file": str(output_file),
        "log_file": str(log_file),
    }


def add_shared_options(parser: argparse.ArgumentParser, default_mode: str = "write") -> None:
    """Register CLI flags shared by `exec`, `resume`, and `review`."""
    parser.add_argument("-C", "--cwd", default=".", help="Working directory for Codex.")
    parser.add_argument(
        "--mode",
        choices=["write", "read-only"],
        default=default_mode,
        help=f"Sandbox mode (default: {default_mode}).",
    )
    parser.add_argument("-m", "--model", default=None, help="Override Codex model.")
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        default=None,
        help="Override model_reasoning_effort.",
    )
    parser.add_argument(
        "--persist", action="store_true", help="Keep session on disk (disable --ephemeral)."
    )
    parser.add_argument(
        "--add-dir", action="append", default=[], help="Extra writable directory (repeatable)."
    )
    parser.add_argument(
        "--raw", action="store_true", help="Disable JSON output schema; return raw text."
    )
    parser.add_argument("--search", action="store_true", help="Enable live web search.")
    parser.add_argument(
        "--no-worker-profile", action="store_true", help="Do not pass `-p worker`."
    )
    parser.add_argument(
        "--timeout", type=int, default=600, help="Timeout in seconds (default: 600)."
    )
    parser.add_argument(
        "--log-dir", default=str(DEFAULT_LOG_ROOT), help="Directory for run logs and outputs."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrate OpenAI Codex CLI cleanly.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_exec = subparsers.add_parser("exec", help="Run a new non-interactive Codex task.")
    add_shared_options(p_exec, default_mode="write")
    p_exec.add_argument("--prompt", default=None, help="Task prompt.")
    p_exec.add_argument("--prompt-file", default=None, help="File containing the task prompt.")

    p_resume = subparsers.add_parser("resume", help="Resume an existing Codex session.")
    add_shared_options(p_resume, default_mode="write")
    p_resume.add_argument(
        "session_id", nargs="?", default=None, help="Session ID to resume (or pass --last)."
    )
    p_resume.add_argument("--last", action="store_true", help="Resume the most recent session.")
    p_resume.add_argument("--prompt", default=None, help="Follow-up instructions.")
    p_resume.add_argument(
        "--prompt-file", default=None, help="File containing follow-up instructions."
    )

    p_review = subparsers.add_parser("review", help="Run a read-only code review.")
    add_shared_options(p_review, default_mode="read-only")
    p_review.add_argument("--base", default=None, help="Review changes against a base branch.")
    p_review.add_argument("--commit", default=None, help="Review a specific commit SHA.")
    p_review.add_argument(
        "--uncommitted", action="store_true", help="Review staged, unstaged, and untracked changes."
    )
    p_review.add_argument("--prompt", default=None, help="Custom review instructions.")
    p_review.add_argument(
        "--prompt-file", default=None, help="File containing custom review instructions."
    )

    args = parser.parse_args()
    codex_bin = find_codex_bin()
    cwd = Path(args.cwd).resolve()
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(args.log_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "codex.log"
    output_file = run_dir / ("output.txt" if args.raw or args.subcommand == "review" else "output.json")

    cmd = [codex_bin, "exec"]
    sandbox = "read-only" if args.mode == "read-only" else "workspace-write"
    has_prompt = False

    if args.subcommand == "exec":
        prompt = read_prompt(args.prompt, args.prompt_file)
        append_shared_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            sandbox=sandbox,
            allow_ephemeral=True,
            allow_schema=True,
            allow_extra_dirs=True,
            allow_search=True,
            check_git=True,
        )
        cmd.append(prompt)
        has_prompt = True
    elif args.subcommand == "resume":
        prompt = read_prompt(args.prompt, args.prompt_file)
        append_shared_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            sandbox=sandbox,
            allow_ephemeral=False,
            allow_schema=True,
            allow_extra_dirs=False,
            allow_search=False,
            check_git=True,
        )
        cmd.append("resume")
        if args.last:
            cmd.append("--last")
        elif args.session_id:
            cmd.append(args.session_id)
        else:
            raise ValueError("Provide a session_id or pass --last.")
        cmd.append(prompt)
        has_prompt = True
    elif args.subcommand == "review":
        append_shared_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            sandbox="read-only",
            allow_ephemeral=True,
            allow_schema=False,
            allow_extra_dirs=False,
            allow_search=False,
            check_git=False,
        )
        cmd.append("review")
        if args.uncommitted:
            cmd.append("--uncommitted")
        if args.base:
            cmd.extend(["--base", args.base])
        if args.commit:
            cmd.extend(["--commit", args.commit])
        if args.prompt or args.prompt_file:
            cmd.append(read_prompt(args.prompt, args.prompt_file))
            has_prompt = True
    else:
        raise ValueError(f"Unsupported subcommand: {args.subcommand}")

    summary = run_codex(cmd, log_file, output_file, args.timeout)
    summary["command"] = cmd[:-1] + ["<prompt>"] if has_prompt else cmd
    (run_dir / "meta.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
