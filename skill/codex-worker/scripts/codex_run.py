#!/usr/bin/env python3
"""Wrapper CLI for delegating bounded coding tasks to OpenAI Codex CLI.

Features:
- Resolves the `codex` binary across macOS, Linux, and Windows (including non-login shells).
- Whitelist-isolated config mode (`--config-mode isolated`, default) via `--ignore-user-config`:
  keeps `$CODEX_HOME/auth.json` and extracts only model, provider, reasoning, and proxy settings
  without loading desktop plugins, MCP servers, hooks, memories, or chronicle.
- Streams real-time progress milestones to stderr from `codex exec --json` while keeping stdout pure JSON.
- Supports isolated Git worktrees (`--worktree [NAME]`) for safe parallel workers.
- Enforces explicit sandbox mode (`workspace-write` or `read-only`) and structured JSON handoffs.
- Provides a built-in `doctor` subcommand to validate local prerequisites before running tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_SCHEMA = (
    SKILL_DIR / "resources" / "handoff_schema.json"
    if (SKILL_DIR / "resources" / "handoff_schema.json").is_file()
    else SCRIPT_DIR / "handoff_schema.json"
)
DEFAULT_LOG_ROOT = Path(tempfile.gettempdir()) / "codex-worker-runs"
DEFAULT_WORKTREE_ROOT = Path(tempfile.gettempdir()) / "codex-worktrees"

SHELL_WRAPPER_RE = re.compile(
    r"^(?:/opt/homebrew/bin/bash|/bin/bash|/bin/zsh|/usr/bin/env\s+\w+|bash|zsh)\s+-(?:l?c)\s+(.*)$",
    re.DOTALL,
)

DEFAULT_DEVELOPER_INSTRUCTIONS = (
    "You are running as a non-interactive worker invoked by an orchestrator agent. "
    "Execute the bounded task directly and thoroughly within the workspace. "
    "Do not spawn subagents, do not ask interactive follow-up questions, and do not modify "
    "files outside the requested scope. Run concrete verification commands (tests, linters, "
    "or scripts) for every change when possible. Report concise, factual results: changed "
    "file paths, exact verification commands and outcomes, and any remaining risks or blockers."
)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(api_?key|bearer|secret|password|authorization|cookie|credential|_token$|^token$)",
    re.IGNORECASE,
)


def get_codex_home() -> Path:
    """Resolve CODEX_HOME dynamically from environment or `~/.codex`."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def find_codex_bin() -> str:
    """Locate the `codex` CLI binary across macOS, Linux, and Windows."""
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin

    candidates: list[str] = []
    for name in ("codex", "codex.cmd", "codex.exe") if os.name == "nt" else ("codex",):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            candidates.append(str(Path(appdata) / "npm" / "codex.cmd"))
        if localappdata:
            candidates.append(str(Path(localappdata) / "Programs" / "codex" / "codex.exe"))
    else:
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
        "codex CLI not found. Checked CODEX_BIN, PATH, and standard OS install locations."
    )


def build_exec_env() -> dict[str, str]:
    """Ensure standard CLI install directories are present in PATH."""
    env = os.environ.copy()
    path_parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    if os.name == "nt":
        extra = [
            p
            for p in (
                os.environ.get("APPDATA") and str(Path(os.environ["APPDATA"]) / "npm"),
                os.environ.get("LOCALAPPDATA")
                and str(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "codex"),
            )
            if p
        ]
    else:
        extra = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            str(Path.home() / ".local" / "bin"),
        ]
    for prefix in reversed(extra):
        if prefix not in path_parts:
            path_parts.insert(0, prefix)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def run_git(cwd: Path, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a git command in `cwd`."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def is_git_repo(path: Path) -> bool:
    """Return True if `path` is inside a Git working tree."""
    try:
        res = run_git(path, ["rev-parse", "--is-inside-work-tree"], timeout=5)
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def get_git_root(path: Path) -> Path:
    """Resolve the top-level Git repository directory for `path`."""
    res = run_git(path, ["rev-parse", "--show-toplevel"], timeout=5)
    if res.returncode != 0 or not res.stdout.strip():
        raise ValueError(f"--worktree requires a Git repository, but {path} is not inside one.")
    return Path(res.stdout.strip()).resolve()


def create_isolated_worktree(cwd: Path, name: str | None, run_id: str) -> dict[str, str]:
    """Create a fresh Git worktree under `$TMPDIR/codex-worktrees` from HEAD."""
    base_repo = get_git_root(cwd)
    head_check = run_git(base_repo, ["rev-parse", "--verify", "HEAD"], timeout=5)
    if head_check.returncode != 0:
        raise ValueError(
            f"Cannot create worktree for {base_repo}: repository has no commits (HEAD missing)."
        )

    slug = name.strip() if name else run_id
    if not slug or not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise ValueError(
            f"Invalid worktree name {slug!r}. Use only letters, digits, '.', '_', or '-'."
        )

    DEFAULT_WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    wt_path = DEFAULT_WORKTREE_ROOT / slug
    branch = f"codex/{slug}"

    if wt_path.exists():
        raise FileExistsError(
            f"Worktree path already exists: {wt_path}. Choose another name or remove it first."
        )

    res = run_git(base_repo, ["worktree", "add", "-b", branch, str(wt_path), "HEAD"], timeout=20)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        raise RuntimeError(f"Failed to create git worktree at {wt_path}: {detail}")

    cleanup_cmd = (
        f"git -C {shlex.quote(str(base_repo))} worktree remove {shlex.quote(str(wt_path))}"
        if os.name != "nt"
        else f'git -C "{base_repo}" worktree remove "{wt_path}"'
    )
    return {
        "path": str(wt_path),
        "branch": branch,
        "base_repo": str(base_repo),
        "cleanup_command": cleanup_cmd,
    }


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    """Read task instructions from `--prompt-file`, `--prompt`, or piped stdin."""
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")
    if prompt is not None:
        return prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("A prompt is required via --prompt, --prompt-file, or stdin.")


def load_toml_file(path: Path) -> tuple[dict[str, Any], str | None]:
    """Load a TOML file with `tomllib`, returning `(data, error_message)`."""
    if not path.is_file():
        return {}, None
    if tomllib is None:
        return {}, "Python < 3.11 lacks stdlib tomllib"
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return (data if isinstance(data, dict) else {}), None
    except Exception as exc:
        return {}, f"Failed to parse {path}: {exc}"


def to_toml_key(key: str) -> str:
    """Format a TOML key safely for `-c dotted.path=value`."""
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def to_toml_literal(value: Any) -> str:
    """Serialize a Python scalar into a valid TOML literal string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Unsupported TOML scalar type: {type(value).__name__}")


def collect_isolated_overrides(
    codex_home: Path,
    *,
    cli_model: str | None = None,
    cli_effort: str | None = None,
) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    """Build whitelisted `(dotted_key, scalar_value)` overrides for `--ignore-user-config`."""
    base_cfg, base_err = load_toml_file(codex_home / "config.toml")
    worker_cfg, worker_err = load_toml_file(codex_home / "worker.config.toml")
    if base_err:
        raise RuntimeError(base_err)
    if worker_err:
        raise RuntimeError(worker_err)

    overrides: dict[str, Any] = {}

    # 1. Scalar fields whitelisted from base config.toml
    for key in ("model", "model_provider"):
        val = base_cfg.get(key)
        if isinstance(val, (str, bool, int, float)):
            overrides[key] = val

    # 2. Overrides whitelisted from worker.config.toml
    for key in (
        "model",
        "model_provider",
        "model_reasoning_effort",
        "personality",
        "tool_output_token_limit",
        "developer_instructions",
    ):
        val = worker_cfg.get(key)
        if isinstance(val, (str, bool, int, float)):
            overrides[key] = val

    # 3. Safe non-interactive worker defaults
    overrides["approval_policy"] = "never"
    overrides["suppress_unstable_features_warning"] = True
    if "developer_instructions" not in overrides:
        overrides["developer_instructions"] = DEFAULT_DEVELOPER_INSTRUCTIONS

    # Apply explicit CLI overrides for provider selection accuracy
    if cli_model:
        overrides["model"] = cli_model
    if cli_effort:
        overrides["model_reasoning_effort"] = cli_effort

    # 4. Active model provider table (only scalar settings)
    active_provider = overrides.get("model_provider")
    provider_keys: list[str] = []
    if isinstance(active_provider, str) and active_provider:
        base_providers = base_cfg.get("model_providers", {})
        worker_providers = worker_cfg.get("model_providers", {})
        merged_provider: dict[str, Any] = {}
        if isinstance(base_providers, dict) and isinstance(base_providers.get(active_provider), dict):
            merged_provider.update(base_providers[active_provider])
        if isinstance(worker_providers, dict) and isinstance(
            worker_providers.get(active_provider), dict
        ):
            merged_provider.update(worker_providers[active_provider])

        p_prefix = f"model_providers.{to_toml_key(active_provider)}"
        for p_key, p_val in merged_provider.items():
            if isinstance(p_val, (str, bool, int, float)):
                dotted = f"{p_prefix}.{to_toml_key(p_key)}"
                overrides[dotted] = p_val
                provider_keys.append(p_key)

    # 5. Shell environment policy (inherit + proxy/env `set` scalars)
    env_policy_keys: list[str] = []
    for cfg in (base_cfg, worker_cfg):
        policy = cfg.get("shell_environment_policy")
        if not isinstance(policy, dict):
            continue
        inherit = policy.get("inherit")
        if isinstance(inherit, str):
            overrides["shell_environment_policy.inherit"] = inherit
        env_set = policy.get("set")
        if isinstance(env_set, dict):
            for e_key, e_val in env_set.items():
                if isinstance(e_val, (str, bool, int, float)):
                    overrides[f"shell_environment_policy.set.{to_toml_key(e_key)}"] = e_val
                    if e_key not in env_policy_keys:
                        env_policy_keys.append(e_key)

    meta = {
        "model": overrides.get("model"),
        "model_provider": active_provider,
        "provider_keys": provider_keys,
        "env_set_keys": env_policy_keys,
        "whitelisted_keys": list(overrides.keys()),
    }
    return list(overrides.items()), meta


def resolve_config_mode(requested_mode: str, no_worker_profile: bool, quiet: bool) -> str:
    """Resolve effective config mode (`isolated`, `profile`, or `inherit`)."""
    if no_worker_profile:
        return "inherit"
    if requested_mode == "isolated" and tomllib is None:
        if not quiet:
            print(
                "[codex] warning: Python < 3.11 lacks tomllib; falling back to profile mode.",
                file=sys.stderr,
                flush=True,
            )
        return "profile"
    return requested_mode


def parse_telemetry(log_text: str) -> dict[str, object]:
    """Fallback regex telemetry parser for non-JSON Codex CLI output."""
    session_match = re.search(r"session id:\s*([0-9a-fA-F-]+)", log_text)
    tokens_match = re.search(r"tokens used\s*\n\s*([0-9,]+)", log_text)
    model_match = re.search(r"^model:\s*(.+)$", log_text, re.MULTILINE)
    return {
        "session_id": session_match.group(1) if session_match else None,
        "tokens_used": int(tokens_match.group(1).replace(",", "")) if tokens_match else None,
        "model": model_match.group(1).strip() if model_match else None,
    }


def truncate_text(text: str, limit: int = 100) -> str:
    """Collapse whitespace and truncate long strings for clean stderr progress lines."""
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def strip_shell_wrapper(command: str) -> str:
    """Strip `/bin/zsh -lc '...'` wrapper for cleaner Codex-style command display."""
    text = command.strip()
    match = SHELL_WRAPPER_RE.match(text)
    if not match:
        return text
    inner = match.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
        inner = inner[1:-1]
    return inner


def is_structured_handoff_text(text: str) -> bool:
    """Detect whether an agent_message is the final JSON schema payload."""
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and {"status", "summary", "changed_files"}.issubset(obj.keys())


def get_shared_runs_dir() -> Path:
    """Resolve the shared runs directory read by the Codex Worker MCP panel."""
    env_dir = os.environ.get("CODEX_WORKER_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser() / "runs"
    return Path.home() / ".gemini" / "antigravity" / "plugin_data" / "codex-in-anti" / "runs"


class PanelStateSyncer:
    """Best-effort atomic sync of live CLI run progress into the MCP panel runs directory."""

    def __init__(self, runs_dir: Path, meta: dict[str, Any]) -> None:
        self.run_id = str(meta["run_id"])
        self.state_path = runs_dir / self.run_id / "state.json"
        self.started_at = float(meta.get("started_at") or time.time())
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self.state: dict[str, Any] = {
            "run_id": self.run_id,
            "source": "cli",
            "pid": None,
            "ok": False,
            "status": "running",
            "cwd": meta.get("cwd"),
            "mode": meta.get("mode"),
            "config_mode": meta.get("config_mode"),
            "model": meta.get("model"),
            "effort": meta.get("effort"),
            "prompt_preview": str(meta.get("prompt") or "")[:180],
            "started_at": self.started_at,
            "finished_at": None,
            "elapsed_sec": 0.0,
            "duration_sec": None,
            "exit_code": None,
            "session_id": None,
            "tokens_used": None,
            "usage": None,
            "worktree": meta.get("worktree"),
            "result": None,
            "error": None,
            "log_file": meta.get("log_file"),
            "output_file": meta.get("output_file"),
        }

    def write(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            if self.state["duration_sec"] is None:
                self.state["elapsed_sec"] = round(max(0.0, time.time() - self.started_at), 2)
            payload = {
                **self.state,
                "events": self.events,
                "seq": self.seq,
            }
            tmp_path = self.state_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.state_path)
        except OSError:
            pass

    def append_event(self, payload: dict[str, Any]) -> None:
        self.seq += 1
        payload["seq"] = self.seq
        payload.setdefault("ts", round(max(0.0, time.time() - self.started_at), 2))
        self.events.append(payload)

    def was_cancelled_externally(self) -> bool:
        try:
            if not self.state_path.is_file():
                return False
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("status") == "cancelled"
        except Exception:
            return False


def emit_progress(quiet: bool, message: str) -> None:
    """Print a live progress line to stderr unless `--quiet` is enabled."""
    if not quiet:
        print(f"[codex] {message}", file=sys.stderr, flush=True)


def sanitize_command_for_meta(cmd: list[str], has_prompt: bool) -> list[str]:
    """Redact potential secret `-c` values and prompt text before storing/printing metadata."""
    redacted: list[str] = []
    i = 0
    last_idx = len(cmd) - 1 if has_prompt else len(cmd)
    while i < last_idx:
        tok = cmd[i]
        if tok == "-c" and i + 1 < last_idx:
            arg = cmd[i + 1]
            key_part = arg.split("=", 1)[0].split(".")[-1].strip('"')
            if SENSITIVE_KEY_PATTERN.search(key_part) and key_part != "tool_output_token_limit":
                redacted.extend(["-c", f'{arg.split("=", 1)[0]}="<redacted>"'])
            else:
                redacted.extend(["-c", arg])
            i += 2
            continue
        redacted.append(tok)
        i += 1
    if has_prompt:
        redacted.append("<prompt>")
    return redacted


def append_codex_flags(
    cmd: list[str],
    args: argparse.Namespace,
    cwd: Path,
    output_file: Path,
    *,
    config_mode: str,
    codex_home: Path,
    sandbox: str,
    allow_ephemeral: bool,
    allow_schema: bool,
    allow_extra_dirs: bool,
    allow_search: bool,
    allow_json: bool,
    check_git: bool,
) -> dict[str, Any]:
    """Append Codex CLI flags consistently across `exec`, `resume`, and `review`."""
    isolated_meta: dict[str, Any] = {}
    worker_profile_path = codex_home / "worker.config.toml"

    if config_mode == "isolated":
        cmd.append("--ignore-user-config")
        overrides, isolated_meta = collect_isolated_overrides(
            codex_home,
            cli_model=args.model,
            cli_effort=args.effort,
        )
        for key, val in overrides:
            cmd.extend(["-c", f"{key}={to_toml_literal(val)}"])
    elif config_mode == "profile":
        if worker_profile_path.is_file():
            cmd.extend(["-p", "worker"])
        if args.model:
            cmd.extend(["-m", args.model])
        if args.effort:
            cmd.extend(["-c", f'model_reasoning_effort="{args.effort}"'])
    else:  # inherit
        if args.model:
            cmd.extend(["-m", args.model])
        if args.effort:
            cmd.extend(["-c", f'model_reasoning_effort="{args.effort}"'])

    cmd.extend(["-s", sandbox])
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
    if allow_json:
        cmd.append("--json")
    cmd.extend(["-o", str(output_file)])
    return isolated_meta


def run_codex(
    cmd: list[str],
    log_file: Path,
    output_file: Path,
    timeout: int | None,
    *,
    quiet: bool,
    fallback_model: str | None = None,
    panel_meta: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Execute Codex CLI via Popen, stream milestones to stderr, and capture structured telemetry."""
    env = build_exec_env()
    start = time.monotonic()
    timed_out = False
    log_lines: list[str] = []

    syncer = PanelStateSyncer(get_shared_runs_dir(), panel_meta) if panel_meta is not None else None

    session_id: str | None = None
    last_agent_message = ""
    usage_totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    saw_usage = False

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    if syncer is not None:
        syncer.state["pid"] = proc.pid
        syncer.write()

    def on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        emit_progress(quiet, f"timeout after {timeout}s; terminating process")
        try:
            proc.terminate()
        except OSError:
            pass

    timer: threading.Timer | None = None
    if timeout and timeout > 0:
        timer = threading.Timer(timeout, on_timeout)
        timer.daemon = True
        timer.start()

    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            log_lines.append(raw_line)
            stripped = raw_line.strip()
            if not stripped:
                continue

            if stripped.startswith("{"):
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    event = None
                if isinstance(event, dict):
                    etype = event.get("type")
                    if etype == "thread.started":
                        tid = event.get("thread_id")
                        if isinstance(tid, str) and tid:
                            session_id = tid
                            emit_progress(quiet, f"session={tid}")
                            if syncer is not None:
                                syncer.state["session_id"] = tid
                                syncer.append_event({"kind": "session", "session_id": tid})
                                syncer.write()
                    elif etype in ("item.started", "item.completed"):
                        item = event.get("item") or {}
                        item_id = str(item.get("id") or "")
                        itype = str(item.get("type") or "")
                        if itype == "command_execution":
                            raw_cmd = str(item.get("command") or "")
                            cmd_txt = truncate_text(raw_cmd, 100)
                            if etype == "item.started":
                                emit_progress(quiet, f"run: {cmd_txt}")
                            else:
                                emit_progress(quiet, f"done (exit={item.get('exit_code')}): {cmd_txt}")
                            if syncer is not None:
                                syncer.append_event(
                                    {
                                        "kind": "command",
                                        "item_id": item_id,
                                        "command": raw_cmd,
                                        "display_command": strip_shell_wrapper(raw_cmd),
                                        "status": str(item.get("status") or ""),
                                        "exit_code": item.get("exit_code"),
                                        "output": str(item.get("aggregated_output") or "")[:12000],
                                    }
                                )
                                syncer.write()
                        elif itype == "file_change":
                            if etype == "item.completed":
                                emit_progress(quiet, itype)
                            if syncer is not None:
                                changes = item.get("changes") or []
                                norm_changes = [
                                    {
                                        "path": str(c.get("path") or ""),
                                        "kind": str(c.get("kind") or "modify"),
                                    }
                                    for c in changes
                                    if isinstance(c, dict)
                                ]
                                syncer.append_event(
                                    {
                                        "kind": "file_change",
                                        "item_id": item_id,
                                        "status": str(item.get("status") or ""),
                                        "changes": norm_changes,
                                    }
                                )
                                syncer.write()
                        elif itype == "agent_message" and etype == "item.completed":
                            txt = item.get("text")
                            if isinstance(txt, str):
                                last_agent_message = txt
                                if syncer is not None and txt.strip() and not is_structured_handoff_text(txt):
                                    syncer.append_event(
                                        {
                                            "kind": "commentary",
                                            "item_id": item_id,
                                            "text": txt.strip(),
                                        }
                                    )
                                    syncer.write()
                        elif etype == "item.completed" and itype:
                            emit_progress(quiet, itype)
                    elif etype == "turn.completed":
                        u = event.get("usage") or {}
                        if isinstance(u, dict):
                            saw_usage = True
                            in_t = int(u.get("input_tokens") or 0)
                            c_t = int(u.get("cached_input_tokens") or 0)
                            out_t = int(u.get("output_tokens") or 0)
                            r_t = int(u.get("reasoning_output_tokens") or 0)
                            usage_totals["input_tokens"] += in_t
                            usage_totals["cached_input_tokens"] += c_t
                            usage_totals["output_tokens"] += out_t
                            usage_totals["reasoning_output_tokens"] += r_t
                            emit_progress(
                                quiet, f"turn done (in={in_t}, cached={c_t}, out={out_t})"
                            )
                            if syncer is not None:
                                syncer.state["usage"] = dict(usage_totals)
                                syncer.state["tokens_used"] = (
                                    usage_totals["input_tokens"] + usage_totals["output_tokens"]
                                )
                                syncer.append_event(
                                    {
                                        "kind": "turn_done",
                                        "usage": {
                                            "input_tokens": in_t,
                                            "cached_input_tokens": c_t,
                                            "output_tokens": out_t,
                                            "reasoning_output_tokens": r_t,
                                        },
                                    }
                                )
                                syncer.write()
                    elif etype in ("error", "turn.failed"):
                        err_obj = event.get("error")
                        msg = (
                            event.get("message")
                            or (err_obj.get("message") if isinstance(err_obj, dict) else None)
                            or stripped
                        )
                        emit_progress(quiet, f"error: {truncate_text(str(msg), 160)}")
                        if syncer is not None:
                            syncer.state["error"] = str(msg)
                            syncer.append_event({"kind": "error", "message": str(msg)})
                            syncer.write()
                    continue

            if "ERROR" in stripped or "fatal" in stripped.lower():
                emit_progress(quiet, truncate_text(stripped, 160))
                if syncer is not None:
                    syncer.append_event({"kind": "log", "text": stripped})
                    syncer.write()

        if timed_out and proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait()
    finally:
        if timer is not None:
            timer.cancel()

    if timed_out:
        log_lines.append(f"\n[codex_run] Timed out after {timeout}s\n")

    exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
    duration_sec = round(time.monotonic() - start, 2)
    combined_log = "".join(log_lines)
    log_file.write_text(combined_log, encoding="utf-8")

    raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
    if not raw_output and last_agent_message:
        raw_output = last_agent_message.strip()

    parsed_result: object = raw_output
    if raw_output:
        try:
            parsed_result = json.loads(raw_output)
        except json.JSONDecodeError:
            parsed_result = raw_output

    fallback = parse_telemetry(combined_log)
    resolved_session = session_id or fallback["session_id"]
    resolved_tokens = (
        usage_totals["input_tokens"] + usage_totals["output_tokens"]
        if saw_usage
        else fallback["tokens_used"]
    )
    resolved_model = fallback["model"] or fallback_model

    if syncer is not None:
        cancelled = syncer.was_cancelled_externally()
        final_status = (
            "cancelled"
            if cancelled
            else ("done" if (exit_code == 0 and not timed_out) else "error")
        )
        syncer.state.update(
            {
                "pid": None,
                "ok": final_status == "done",
                "status": final_status,
                "model": resolved_model,
                "finished_at": time.time(),
                "duration_sec": duration_sec,
                "elapsed_sec": duration_sec,
                "exit_code": exit_code,
                "session_id": resolved_session,
                "tokens_used": resolved_tokens,
                "usage": dict(usage_totals) if saw_usage else None,
                "result": parsed_result,
                "error": (
                    "Cancelled by user."
                    if cancelled
                    else (
                        f"Timed out after {timeout}s"
                        if timed_out
                        else (None if exit_code == 0 else (syncer.state.get("error") or f"codex exited with code {exit_code}"))
                    )
                ),
            }
        )
        syncer.write()

    summary: dict[str, object] = {
        "ok": exit_code == 0 and not timed_out,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_sec": duration_sec,
        "model": resolved_model,
        "session_id": resolved_session,
        "tokens_used": resolved_tokens,
        "result": parsed_result,
        "output_file": str(output_file),
        "log_file": str(log_file),
    }
    if saw_usage:
        summary["usage"] = usage_totals
    return summary


def run_doctor() -> int:
    """Validate local prerequisites (Python, codex CLI, auth, configs, schema, git)."""
    codex_home = get_codex_home()
    env = build_exec_env()
    checks: dict[str, dict[str, Any]] = {}
    all_required_ok = True

    # 1. Python & tomllib
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks["python"] = {
        "ok": sys.version_info >= (3, 10),
        "required": True,
        "version": py_ver,
        "tomllib_available": tomllib is not None,
    }
    if not checks["python"]["ok"]:
        all_required_ok = False

    # 2. codex binary + version
    try:
        codex_bin = find_codex_bin()
        ver_proc = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        ver_str = (ver_proc.stdout or ver_proc.stderr).strip()
        codex_ok = ver_proc.returncode == 0
        checks["codex_bin"] = {
            "ok": codex_ok,
            "required": True,
            "path": codex_bin,
            "version": ver_str,
        }
    except Exception as exc:
        checks["codex_bin"] = {
            "ok": False,
            "required": True,
            "error": str(exc),
        }
    if not checks["codex_bin"]["ok"]:
        all_required_ok = False

    # 3. auth.json
    auth_file = codex_home / "auth.json"
    auth_ok = auth_file.is_file() and auth_file.stat().st_size > 0
    checks["auth"] = {
        "ok": auth_ok,
        "required": True,
        "path": str(auth_file),
        "hint": None if auth_ok else "Run 'codex login' to authenticate.",
    }
    if not auth_ok:
        all_required_ok = False

    # 4. config.toml & worker.config.toml + whitelisted summary
    base_cfg_path = codex_home / "config.toml"
    worker_cfg_path = codex_home / "worker.config.toml"
    _, base_err = load_toml_file(base_cfg_path)
    _, worker_err = load_toml_file(worker_cfg_path)

    whitelist_summary: dict[str, Any] = {}
    whitelist_err: str | None = None
    if tomllib is not None and not base_err and not worker_err:
        try:
            _, whitelist_summary = collect_isolated_overrides(codex_home)
        except Exception as exc:
            whitelist_err = str(exc)

    config_ok = (base_err is None) and (worker_err is None) and (whitelist_err is None)
    checks["config"] = {
        "ok": config_ok,
        "required": True,
        "codex_home": str(codex_home),
        "config_toml_exists": base_cfg_path.is_file(),
        "worker_config_exists": worker_cfg_path.is_file(),
        "whitelisted_summary": whitelist_summary,
        "error": base_err or worker_err or whitelist_err,
    }
    if not config_ok:
        all_required_ok = False

    # 5. handoff_schema.json
    schema_ok = False
    schema_err: str | None = None
    if DEFAULT_SCHEMA.is_file():
        try:
            json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
            schema_ok = True
        except Exception as exc:
            schema_err = str(exc)
    else:
        schema_err = "Schema file missing"
    checks["schema"] = {
        "ok": schema_ok,
        "required": True,
        "path": str(DEFAULT_SCHEMA),
        "error": schema_err,
    }
    if not schema_ok:
        all_required_ok = False

    # 6. git (informational, required for --worktree)
    git_bin = shutil.which("git", path=env.get("PATH"))
    git_ok = False
    git_ver: str | None = None
    if git_bin:
        try:
            gp = subprocess.run([git_bin, "--version"], capture_output=True, text=True, timeout=5)
            git_ok = gp.returncode == 0
            git_ver = gp.stdout.strip()
        except Exception:
            git_ok = False
    checks["git"] = {
        "ok": git_ok,
        "required": False,
        "path": git_bin,
        "version": git_ver,
    }

    report = {
        "ok": all_required_ok,
        "recommended_config_mode": "isolated" if tomllib is not None else "profile",
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all_required_ok else 1


def add_shared_options(parser: argparse.ArgumentParser, default_mode: str = "write") -> None:
    """Register CLI flags shared by `exec`, `resume`, and `review`."""
    parser.add_argument("-C", "--cwd", default=".", help="Working directory for Codex.")
    parser.add_argument(
        "--mode",
        choices=["write", "read-only"],
        default=default_mode,
        help=f"Sandbox mode (default: {default_mode}).",
    )
    parser.add_argument(
        "--config-mode",
        choices=["isolated", "profile", "inherit"],
        default="isolated",
        help="Config isolation mode: isolated (whitelist via --ignore-user-config, default), profile (-p worker), or inherit.",
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
        "--no-worker-profile",
        action="store_true",
        help="Deprecated alias for `--config-mode inherit`.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live stderr progress milestones.",
    )
    parser.add_argument(
        "--no-panel-sync",
        action="store_true",
        help="Do not sync live run progress to the Codex Worker MCP panel runs directory.",
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
    p_exec.add_argument(
        "--worktree",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help="Run inside a newly created isolated Git worktree (optionally named NAME).",
    )
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

    subparsers.add_parser("doctor", help="Check local Codex CLI, auth, config, and schema health.")

    args = parser.parse_args()
    if args.subcommand == "doctor":
        return run_doctor()

    codex_bin = find_codex_bin()
    codex_home = get_codex_home()
    initial_cwd = Path(args.cwd).resolve()
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(args.log_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "codex.log"
    output_file = run_dir / (
        "output.txt" if args.raw or args.subcommand == "review" else "output.json"
    )

    effective_config_mode = resolve_config_mode(
        args.config_mode, args.no_worker_profile, args.quiet
    )
    worktree_info: dict[str, str] | None = None
    cwd = initial_cwd
    if args.subcommand == "exec" and args.worktree is not None:
        worktree_info = create_isolated_worktree(
            initial_cwd, args.worktree if args.worktree else None, run_id
        )
        cwd = Path(worktree_info["path"])
        emit_progress(
            args.quiet,
            f"worktree={worktree_info['path']} (branch={worktree_info['branch']})",
        )

    cmd = [codex_bin, "exec"]
    sandbox = "read-only" if args.mode == "read-only" else "workspace-write"
    has_prompt = False
    prompt_text = ""
    isolated_meta: dict[str, Any] = {}

    if args.subcommand == "exec":
        prompt_text = read_prompt(args.prompt, args.prompt_file)
        isolated_meta = append_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            config_mode=effective_config_mode,
            codex_home=codex_home,
            sandbox=sandbox,
            allow_ephemeral=True,
            allow_schema=True,
            allow_extra_dirs=True,
            allow_search=True,
            allow_json=True,
            check_git=True,
        )
        cmd.append(prompt_text)
        has_prompt = True
    elif args.subcommand == "resume":
        prompt_text = read_prompt(args.prompt, args.prompt_file)
        isolated_meta = append_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            config_mode=effective_config_mode,
            codex_home=codex_home,
            sandbox=sandbox,
            allow_ephemeral=False,
            allow_schema=True,
            allow_extra_dirs=False,
            allow_search=False,
            allow_json=True,
            check_git=True,
        )
        cmd.append("resume")
        if args.last:
            cmd.append("--last")
        elif args.session_id:
            cmd.append(args.session_id)
        else:
            raise ValueError("Provide a session_id or pass --last.")
        cmd.append(prompt_text)
        has_prompt = True
    elif args.subcommand == "review":
        isolated_meta = append_codex_flags(
            cmd,
            args,
            cwd,
            output_file,
            config_mode=effective_config_mode,
            codex_home=codex_home,
            sandbox="read-only",
            allow_ephemeral=True,
            allow_schema=False,
            allow_extra_dirs=False,
            allow_search=False,
            allow_json=False,
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
            prompt_text = read_prompt(args.prompt, args.prompt_file)
            cmd.append(prompt_text)
            has_prompt = True
    else:
        raise ValueError(f"Unsupported subcommand: {args.subcommand}")

    fallback_model = (
        args.model
        or (isolated_meta.get("model") if isinstance(isolated_meta.get("model"), str) else None)
    )
    panel_meta: dict[str, Any] | None = None
    if args.subcommand in ("exec", "resume") and not args.no_panel_sync:
        panel_meta = {
            "run_id": run_id,
            "cwd": str(cwd),
            "mode": args.mode,
            "config_mode": effective_config_mode,
            "model": fallback_model,
            "effort": args.effort,
            "prompt": prompt_text,
            "worktree": worktree_info,
            "started_at": time.time(),
            "log_file": str(log_file),
            "output_file": str(output_file),
        }

    summary = run_codex(
        cmd,
        log_file,
        output_file,
        args.timeout,
        quiet=args.quiet,
        fallback_model=fallback_model,
        panel_meta=panel_meta,
    )
    summary["config_mode"] = effective_config_mode
    if worktree_info is not None:
        summary["worktree"] = worktree_info
    summary["command"] = sanitize_command_for_meta(cmd, has_prompt)

    (run_dir / "meta.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
