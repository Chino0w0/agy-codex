#!/usr/bin/env python3
"""Wrapper CLI & shared Codex execution helpers for Agy Codex."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import codex_catalog  # noqa: E402
import codex_telemetry  # noqa: E402

DEFAULT_SCHEMA = (
    SCRIPT_DIR / "handoff_schema.json"
    if (SCRIPT_DIR / "handoff_schema.json").is_file()
    else SCRIPT_DIR.parent / "resources" / "handoff_schema.json"
)
DEFAULT_LOG_ROOT = Path(tempfile.gettempdir()) / "agy-codex-runs"
DEFAULT_WORKTREE_ROOT = Path(tempfile.gettempdir()) / "agy-codex-worktrees"

SHELL_WRAPPER_RE = re.compile(
    r'^(?:'
    r'"?[^"\r\n]*?(?:bash|zsh)(?:\.exe)?"?\s+-(?:l?c)'
    r'|/usr/bin/env\s+\w+\s+-(?:l?c)'
    r'|"?[^"\r\n]*?(?:pwsh|powershell)(?:\.exe)?"?(?:\s+-\w+)*\s+-(?:c|Command)'
    r'|"?[^"\r\n]*?cmd(?:\.exe)?"?\s+/c'
    r')\s+(.*)$',
    re.IGNORECASE | re.DOTALL,
)


def get_codex_home(explicit: Path | str | None = None) -> Path:
    return codex_catalog.get_codex_home(explicit)


def get_shared_runs_dir(data_dir: Path | str | None = None) -> Path:
    return codex_catalog.get_plugin_data_dir(data_dir) / "runs"


def find_codex_bin() -> str:
    """Locate `codex` CLI executable across Windows, macOS, and Linux (skipping .ps1 on Windows)."""
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin

    candidates: list[str] = []
    if os.name == "nt":
        for name in ("codex.exe", "codex.cmd", "codex.bat", "codex"):
            found = shutil.which(name)
            if found and not found.lower().endswith(".ps1"):
                candidates.append(found)
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(str(Path(localappdata) / "Programs" / "codex" / "codex.exe"))
        if appdata:
            candidates.append(str(Path(appdata) / "npm" / "codex.cmd"))
    else:
        found = shutil.which("codex")
        if found:
            candidates.append(found)
        candidates.extend(
            [
                "/opt/homebrew/bin/codex",
                "/usr/local/bin/codex",
                str(Path.home() / ".local" / "bin" / "codex"),
            ]
        )

    for candidate in candidates:
        if candidate and Path(candidate).is_file() and not candidate.lower().endswith(".ps1"):
            return candidate

    raise FileNotFoundError(
        "codex CLI executable not found. Checked CODEX_BIN, PATH, and standard OS install locations."
    )


def build_exec_env() -> dict[str, str]:
    """Ensure standard CLI install dirs and UTF-8 Python settings are present in env."""
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
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def terminate_process_tree(proc_or_pid: subprocess.Popen[Any] | int | None) -> None:
    """Terminate a process and all its descendants cleanly across Windows and POSIX."""
    if proc_or_pid is None:
        return
    pid = proc_or_pid.pid if isinstance(proc_or_pid, subprocess.Popen) else int(proc_or_pid)
    if pid <= 0:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass
        if isinstance(proc_or_pid, subprocess.Popen):
            try:
                proc_or_pid.terminate()
            except OSError:
                pass
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        return

    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        return
    except OSError:
        pass
    if isinstance(proc_or_pid, subprocess.Popen):
        try:
            proc_or_pid.terminate()
        except OSError:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def run_git(cwd: Path, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def is_git_repo(path: Path) -> bool:
    try:
        res = run_git(path, ["rev-parse", "--is-inside-work-tree"], timeout=5)
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def get_git_root(path: Path) -> Path:
    res = run_git(path, ["rev-parse", "--show-toplevel"], timeout=5)
    if res.returncode != 0 or not res.stdout.strip():
        raise ValueError(f"--worktree requires a Git repository, but {path} is not inside one.")
    return Path(res.stdout.strip()).resolve()


def get_worktree_root(data_dir: Path | str | None = None) -> Path:
    """Resolve isolated Git worktree directory outside TEMP to avoid Windows split writable root conflicts."""
    return (codex_catalog.get_plugin_data_dir(data_dir) / "worktrees").resolve()


def create_isolated_worktree(
    cwd: Path,
    name: str | None,
    run_id: str,
    *,
    data_dir: Path | str | None = None,
) -> dict[str, str]:
    """Create a fresh Git worktree under plugin_data/worktrees from HEAD using a canonical long path."""
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

    wt_root = get_worktree_root(data_dir)
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = (wt_root / slug).resolve()
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
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8")
    if prompt is not None:
        return prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("A prompt is required via --prompt, --prompt-file, or stdin.")


def resolve_config_mode(requested_mode: str, no_worker_profile: bool, quiet: bool) -> str:
    if no_worker_profile:
        return "inherit"
    if requested_mode == "isolated" and codex_catalog.tomllib is None:
        if not quiet:
            print(
                "[codex] warning: Python < 3.11 lacks tomllib; falling back to profile mode.",
                file=sys.stderr,
                flush=True,
            )
        return "profile"
    return requested_mode


def parse_telemetry(log_text: str) -> dict[str, Any]:
    """Extract observed session_id, tokens_used, model, and effort from Codex logs."""
    session_match = re.search(r"session id:\s*([0-9a-fA-F-]+)", log_text)
    tokens_match = re.search(r"tokens used\s*\n\s*([0-9,]+)", log_text)
    model_match = re.search(r"^model:\s*(.+)$", log_text, re.MULTILINE)
    effort_match = re.search(r"^reasoning effort:\s*(.+)$", log_text, re.MULTILINE)
    return {
        "session_id": session_match.group(1) if session_match else None,
        "tokens_used": int(tokens_match.group(1).replace(",", "")) if tokens_match else None,
        "model": model_match.group(1).strip() if model_match else None,
        "effort": effort_match.group(1).strip() if effort_match else None,
    }


def truncate_text(text: str, limit: int = 100) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def strip_shell_wrapper(command: str) -> str:
    """Strip bash/zsh/powershell/pwsh/cmd shell wrapper for cleaner step titles."""
    text = command.strip()
    match = SHELL_WRAPPER_RE.match(text)
    if not match:
        return text
    inner = match.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
        inner = inner[1:-1]
    return inner


def is_structured_handoff_text(text: str) -> bool:
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and {"status", "summary", "changed_files"}.issubset(obj.keys())


def extract_mcp_tool_call_event(
    item: dict[str, Any],
) -> tuple[dict[str, Any], str | None, str | None]:
    """Build normalized timeline command event and optional observed identity from `mcp_tool_call`."""
    item_id = str(item.get("id") or "")
    server_name = str(item.get("server") or "mcp")
    tool_name = str(item.get("tool") or "")
    label = f"mcp:{server_name}.{tool_name}" if tool_name else f"mcp:{server_name}"
    status = str(item.get("status") or "")
    err_obj = item.get("error")
    res_obj = item.get("result")

    out_parts: list[str] = []
    obs_model: str | None = None
    obs_effort: str | None = None

    if isinstance(err_obj, dict):
        msg = err_obj.get("message") or json.dumps(err_obj, ensure_ascii=False)
        out_parts.append(str(msg))
    elif err_obj:
        out_parts.append(str(err_obj))

    if isinstance(res_obj, dict):
        content_list = res_obj.get("content")
        if isinstance(content_list, list):
            for block in content_list:
                if not isinstance(block, dict):
                    continue
                txt = block.get("text")
                if not isinstance(txt, str) or not txt.strip():
                    continue
                out_parts.append(txt.strip())
                if tool_name == "get_runtime_identity":
                    try:
                        parsed = json.loads(txt)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        m_val = parsed.get("model")
                        e_val = parsed.get("reasoning_effort") or parsed.get("effort")
                        if isinstance(m_val, str) and m_val.strip():
                            obs_model = m_val.strip()
                        if isinstance(e_val, str) and e_val.strip():
                            obs_effort = e_val.strip()
        if not out_parts and res_obj.get("structured_content") is not None:
            out_parts.append(json.dumps(res_obj["structured_content"], ensure_ascii=False))
    elif res_obj is not None:
        out_parts.append(str(res_obj))

    is_done = status in ("completed", "failed", "error") or res_obj is not None or err_obj is not None
    exit_code: int | None = None if not is_done else (1 if (err_obj or status == "failed") else 0)
    ev = {
        "kind": "command",
        "item_id": item_id,
        "command": label,
        "display_command": label,
        "status": status,
        "exit_code": exit_code,
        "output": "\n".join(out_parts)[:4000],
    }
    return ev, obs_model, obs_effort


def sanitize_command_for_meta(cmd: list[str], has_prompt: bool) -> list[str]:
    """Redact potential secret `-c` values and prompt text before storing/displaying."""
    redacted: list[str] = []
    i = 0
    last_idx = len(cmd) - 1 if has_prompt else len(cmd)
    while i < last_idx:
        tok = cmd[i]
        if tok == "-c" and i + 1 < last_idx:
            arg = cmd[i + 1]
            key_part = arg.split("=", 1)[0]
            if codex_catalog.is_sensitive_key(key_part):
                redacted.extend(["-c", f'{key_part}="<redacted>"'])
            else:
                redacted.extend(["-c", arg])
            i += 2
            continue
        redacted.append(tok)
        i += 1
    if has_prompt:
        redacted.append("<prompt>")
    return redacted


def build_codex_command(
    codex_bin: str,
    cwd: Path,
    output_file: Path,
    *,
    subcommand: str = "exec",
    codex_home: Path | None = None,
    data_dir: Path | None = None,
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_effort: str | None = None,
    preset: str | None = None,
    cli_mode: str | None = None,
    cli_config_mode: str | None = None,
    no_worker_profile: bool = False,
    persist: bool = False,
    raw: bool = False,
    search: bool = False,
    add_dir: list[str] | None = None,
    allow_ephemeral: bool = True,
    allow_schema: bool = True,
    allow_extra_dirs: bool = True,
    allow_search: bool = True,
    allow_json: bool = True,
    check_git: bool = True,
    quiet: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    """Build full Codex CLI command and resolution metadata consistently for CLI, MCP, and preview."""
    chome = get_codex_home(codex_home)
    ddir = codex_catalog.get_plugin_data_dir(data_dir)

    resolved_cfg = codex_catalog.resolve_effective_run_config(
        chome,
        ddir,
        cli_model=cli_model,
        cli_provider=cli_provider,
        cli_effort=cli_effort,
        preset=preset,
        cli_mode=cli_mode,
        cli_config_mode=cli_config_mode,
    )
    eff = resolved_cfg["effective"]
    req_cfg_mode = cli_config_mode or eff.get("config_mode") or "isolated"
    effective_config_mode = resolve_config_mode(req_cfg_mode, no_worker_profile, quiet)
    eff_mode = eff.get("mode") or "write"
    sandbox = "read-only" if (subcommand == "review" or eff_mode == "read-only") else "workspace-write"

    cmd: list[str] = [codex_bin, "exec"]
    meta: dict[str, Any] = {
        "requested": resolved_cfg["requested"],
        "effective": {**eff, "config_mode": effective_config_mode, "mode": eff_mode},
        "catalog_path": resolved_cfg["catalog_path"],
        "warnings": list(resolved_cfg["warnings"]),
        "whitelisted_keys": [],
        "redacted_overrides": {},
    }

    worker_profile_path = chome / "worker.config.toml"

    if effective_config_mode == "isolated":
        cmd.append("--ignore-user-config")
        overrides, iso_meta = codex_catalog.collect_isolated_overrides(
            chome,
            ddir,
            cli_model=cli_model,
            cli_provider=cli_provider,
            cli_effort=cli_effort,
            preset=preset,
            cli_mode=eff_mode,
            cli_config_mode=effective_config_mode,
        )
        for key, val in overrides:
            cmd.extend(["-c", f"{key}={codex_catalog.to_toml_literal(val)}"])
        meta["whitelisted_keys"] = iso_meta["whitelisted_keys"]
        meta["redacted_overrides"] = iso_meta["redacted_overrides"]
        meta["provider_keys"] = iso_meta["provider_keys"]
        meta["warnings"] = iso_meta["warnings"]
    elif effective_config_mode == "profile":
        if worker_profile_path.is_file():
            cmd.extend(["-p", "worker"])
        if eff.get("model"):
            cmd.extend(["-m", str(eff["model"])])
        if eff.get("provider"):
            cmd.extend(["-c", f"model_provider={codex_catalog.to_toml_literal(eff['provider'])}"])
        if eff.get("effort"):
            cmd.extend(["-c", f"model_reasoning_effort={codex_catalog.to_toml_literal(eff['effort'])}"])
    else:  # inherit
        if eff.get("model"):
            cmd.extend(["-m", str(eff["model"])])
        if eff.get("provider"):
            cmd.extend(["-c", f"model_provider={codex_catalog.to_toml_literal(eff['provider'])}"])
        if eff.get("effort"):
            cmd.extend(["-c", f"model_reasoning_effort={codex_catalog.to_toml_literal(eff['effort'])}"])

    if effective_config_mode in ("profile", "inherit"):
        wb_cfg = resolved_cfg["wb_cfg"]
        worker_cfg = resolved_cfg["worker_cfg"]
        allow_nested = bool((wb_cfg.get("mapping") or {}).get("allow_nested_subagents", False))
        if not allow_nested:
            cmd.extend(["-c", "agents.enabled=false"])
            cmd.extend(["-c", "features.multi_agent=false"])
        dev_inst = codex_catalog.build_worker_developer_instructions(
            worker_cfg, allow_nested=allow_nested
        )
        cmd.extend(["-c", f"developer_instructions={codex_catalog.to_toml_literal(dev_inst)}"])

    cmd.extend(["-s", sandbox])
    if allow_search and search:
        cmd.append("--search")
    cmd.extend(["-C", str(cwd)])
    if allow_extra_dirs:
        for extra_dir in add_dir or []:
            cmd.extend(["--add-dir", str(Path(extra_dir).resolve())])
    if check_git and not is_git_repo(cwd):
        cmd.append("--skip-git-repo-check")
    if allow_ephemeral and not persist:
        cmd.append("--ephemeral")
    if allow_schema and not raw and DEFAULT_SCHEMA.is_file():
        cmd.extend(["--output-schema", str(DEFAULT_SCHEMA)])
    if allow_json:
        cmd.append("--json")
    cmd.extend(["-o", str(output_file)])

    return cmd, meta


class PanelStateSyncer:
    """Best-effort atomic sync of live CLI run progress into the Agy Codex MCP panel runs dir."""

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
            "provider": meta.get("provider"),
            "effort": meta.get("effort"),
            "preset": meta.get("preset"),
            "persist": bool(meta.get("persist", False)),
            "requested": meta.get("requested"),
            "effective": meta.get("effective"),
            "observed": {"model": None, "effort": None},
            "warnings": meta.get("warnings") or [],
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
            payload = {**self.state, "events": self.events, "seq": self.seq}
            codex_catalog.atomic_write_text(
                self.state_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            )
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
    if not quiet:
        print(f"[codex] {message}", file=sys.stderr, flush=True)


def run_codex(
    cmd: list[str],
    log_file: Path,
    output_file: Path,
    timeout: int | None,
    *,
    quiet: bool,
    fallback_model: str | None = None,
    panel_meta: dict[str, Any] | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute Codex CLI via Popen, stream milestones to stderr, and capture telemetry."""
    env = build_exec_env()
    start = time.monotonic()
    timed_out = False
    log_lines: list[str] = []

    syncer = (
        PanelStateSyncer(get_shared_runs_dir(data_dir), panel_meta)
        if panel_meta is not None
        else None
    )

    session_id: str | None = None
    last_agent_message = ""
    observed_model: str | None = None
    observed_effort: str | None = None
    usage_totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    saw_usage = False

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "env": env,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(cmd, **popen_kwargs)
    if syncer is not None:
        syncer.state["pid"] = proc.pid
        syncer.write()

    def on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        emit_progress(quiet, f"timeout after {timeout}s; terminating process tree")
        terminate_process_tree(proc)

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
                    if isinstance(event.get("model"), str) and event["model"].strip():
                        observed_model = event["model"].strip()
                    if isinstance(event.get("reasoning_effort"), str) and event["reasoning_effort"].strip():
                        observed_effort = event["reasoning_effort"].strip()
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
                                emit_progress(
                                    quiet, f"done (exit={item.get('exit_code')}): {cmd_txt}"
                                )
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
                        elif itype == "mcp_tool_call":
                            mcp_ev, obs_m, obs_e = extract_mcp_tool_call_event(item)
                            if obs_m:
                                observed_model = obs_m
                            if obs_e:
                                observed_effort = obs_e
                            if etype == "item.started":
                                emit_progress(quiet, f"mcp: {mcp_ev['display_command']}")
                            else:
                                emit_progress(
                                    quiet,
                                    f"mcp done (exit={mcp_ev['exit_code']}): {mcp_ev['display_command']}",
                                )
                            if syncer is not None:
                                syncer.append_event(mcp_ev)
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
                terminate_process_tree(proc)
                proc.wait(timeout=5)
        else:
            proc.wait()
    finally:
        if timer is not None:
            timer.cancel()
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass

    if timed_out:
        log_lines.append(f"\n[codex_run] Timed out after {timeout}s\n")

    exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
    duration_sec = round(time.monotonic() - start, 2)
    combined_log = "".join(log_lines)
    log_file.write_text(combined_log, encoding="utf-8", newline="\n")

    raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
    if not raw_output and last_agent_message:
        raw_output = last_agent_message.strip()

    parsed_result: Any = raw_output
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
    obs_model = observed_model or fallback["model"]
    obs_effort = observed_effort or fallback["effort"]
    resolved_model = obs_model or fallback_model

    handoff_status = parsed_result.get("status") if isinstance(parsed_result, dict) else None
    handoff_summary = (
        codex_catalog._clean_optional_str(parsed_result.get("summary"))
        if isinstance(parsed_result, dict)
        else None
    )

    if syncer is not None:
        cancelled = syncer.was_cancelled_externally()
        if cancelled:
            final_status = "cancelled"
        elif timed_out or exit_code != 0:
            final_status = "error"
        elif handoff_status == "blocked":
            final_status = "blocked"
        elif handoff_status == "failed":
            final_status = "error"
        else:
            final_status = "done"

        err_msg: str | None = None
        if final_status == "cancelled":
            err_msg = "Cancelled by user."
        elif final_status == "blocked":
            err_msg = syncer.state.get("error") or handoff_summary or "Worker reported status=blocked."
        elif final_status == "error":
            if timed_out:
                err_msg = f"Timed out after {timeout}s"
            elif exit_code != 0:
                err_msg = syncer.state.get("error") or f"codex exited with code {exit_code}"
            else:
                err_msg = syncer.state.get("error") or handoff_summary or "Worker reported status=failed."

        syncer.state.update(
            {
                "pid": None,
                "ok": final_status == "done",
                "status": final_status,
                "model": resolved_model,
                "observed": {"model": obs_model, "effort": obs_effort},
                "finished_at": time.time(),
                "duration_sec": duration_sec,
                "elapsed_sec": duration_sec,
                "exit_code": exit_code,
                "session_id": resolved_session,
                "tokens_used": resolved_tokens,
                "usage": dict(usage_totals) if saw_usage else None,
                "result": parsed_result,
                "error": err_msg,
            }
        )
        syncer.write()
    else:
        if timed_out or exit_code != 0:
            final_status = "error"
        elif handoff_status == "blocked":
            final_status = "blocked"
        elif handoff_status == "failed":
            final_status = "error"
        else:
            final_status = "done"

    summary: dict[str, Any] = {
        "ok": final_status == "done",
        "status": final_status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_sec": duration_sec,
        "model": resolved_model,
        "observed": {"model": obs_model, "effort": obs_effort},
        "session_id": resolved_session,
        "tokens_used": resolved_tokens,
        "result": parsed_result,
        "output_file": str(output_file),
        "log_file": str(log_file),
    }
    if saw_usage:
        summary["usage"] = usage_totals
    return summary


def preview_command_report(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    model: str | None = None,
    provider: str | None = None,
    effort: str | None = None,
    preset: str | None = None,
    config_mode: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Build a dry-run preview of the redacted Codex command and resolved config."""
    try:
        codex_bin = find_codex_bin()
    except FileNotFoundError:
        codex_bin = "codex"

    cmd, meta = build_codex_command(
        codex_bin,
        Path("."),
        Path("output.json"),
        codex_home=codex_home,
        data_dir=data_dir,
        cli_model=model,
        cli_provider=provider,
        cli_effort=effort,
        preset=preset,
        cli_mode=mode,
        cli_config_mode=config_mode,
        check_git=False,
        quiet=True,
    )
    redacted_cmd = sanitize_command_for_meta(cmd, has_prompt=False)
    return {
        "command": redacted_cmd,
        "command_line": " ".join(
            shlex.quote(tok) if os.name != "nt" else (f'"{tok}"' if " " in tok else tok)
            for tok in redacted_cmd
        ),
        **meta,
    }


def doctor_report(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Validate local prerequisites and return structured health check report (secrets redacted)."""
    chome = get_codex_home(codex_home)
    ddir = codex_catalog.get_plugin_data_dir(data_dir)
    env = build_exec_env()
    checks: dict[str, dict[str, Any]] = {}
    all_required_ok = True

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks["python"] = {
        "ok": sys.version_info >= (3, 10),
        "required": True,
        "version": py_ver,
        "tomllib_available": codex_catalog.tomllib is not None,
    }
    if not checks["python"]["ok"]:
        all_required_ok = False

    try:
        codex_bin = find_codex_bin()
        ver_proc = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
        checks["codex_bin"] = {"ok": False, "required": True, "error": str(exc)}
    if not checks["codex_bin"]["ok"]:
        all_required_ok = False

    # Config + catalog + models discovery
    base_cfg_path = chome / "config.toml"
    worker_cfg_path = chome / "worker.config.toml"
    _, base_err = codex_catalog.load_toml_file(base_cfg_path)
    _, worker_err = codex_catalog.load_toml_file(worker_cfg_path)

    disc: dict[str, Any] = {}
    iso_meta: dict[str, Any] = {}
    cfg_err: str | None = None
    if codex_catalog.tomllib is not None and not base_err and not worker_err:
        try:
            disc = codex_catalog.discover_models_and_config(chome, ddir)
            _, iso_meta = codex_catalog.collect_isolated_overrides(chome, ddir)
        except Exception as exc:
            cfg_err = str(exc)

    config_ok = (base_err is None) and (worker_err is None) and (cfg_err is None)
    checks["config"] = {
        "ok": config_ok,
        "required": True,
        "codex_home": str(chome),
        "data_dir": str(ddir),
        "config_toml_exists": base_cfg_path.is_file(),
        "worker_config_exists": worker_cfg_path.is_file(),
        "catalog_mode": disc.get("catalog_mode"),
        "catalog_path": disc.get("catalog_path"),
        "catalog_loaded": bool(disc.get("catalog_loaded")),
        "models_cache_loaded": bool(disc.get("models_cache_loaded")),
        "all_models_count": disc.get("all_models_count", 0),
        "visible_models_count": disc.get("visible_models_count", 0),
        "hidden_models_count": disc.get("hidden_models_count", 0),
        "providers": disc.get("providers", []),
        "effective_defaults": disc.get("effective_defaults", {}),
        "redacted_overrides": iso_meta.get("redacted_overrides", {}),
        "warnings": (disc.get("warnings") or []) + (iso_meta.get("warnings") or []),
        "error": base_err or worker_err or cfg_err,
    }
    if not config_ok:
        all_required_ok = False

    # Auth: non-fatal if active custom provider has token/env_key
    auth_file = chome / "auth.json"
    auth_exists = auth_file.is_file() and auth_file.stat().st_size > 0
    active_prov_id = (disc.get("effective_defaults") or {}).get("provider")
    active_prov = next(
        (p for p in (disc.get("providers") or []) if p.get("id") == active_prov_id), None
    )
    has_provider_auth = bool(active_prov and active_prov.get("has_auth_token"))
    auth_required = not has_provider_auth
    auth_ok = auth_exists or has_provider_auth
    checks["auth"] = {
        "ok": auth_ok,
        "required": auth_required,
        "auth_json_exists": auth_exists,
        "provider_auth_configured": has_provider_auth,
        "path": str(auth_file),
        "hint": None
        if auth_ok
        else "Run 'codex login' or configure provider env_key/token in config.toml.",
    }
    if auth_required and not auth_ok:
        all_required_ok = False

    # Schema
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

    # Git (optional, required for --worktree)
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
    checks["git"] = {"ok": git_ok, "required": False, "path": git_bin, "version": git_ver}

    # Local SQLite telemetry status (optional)
    db_path = codex_telemetry.find_latest_state_db(chome)
    checks["telemetry_db"] = {
        "ok": db_path is not None,
        "required": False,
        "path": str(db_path) if db_path else None,
        "hint": None if db_path else "No local state_*.sqlite found yet (optional).",
    }

    return {
        "ok": all_required_ok,
        "recommended_config_mode": "isolated" if codex_catalog.tomllib is not None else "profile",
        "checks": checks,
    }


def run_doctor() -> int:
    report = doctor_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def add_shared_options(parser: argparse.ArgumentParser, default_mode: str | None = None) -> None:
    parser.add_argument("-C", "--cwd", default=".", help="Working directory for Codex.")
    parser.add_argument(
        "--mode",
        choices=["write", "read-only"],
        default=default_mode,
        help="Sandbox mode (`write` or `read-only`).",
    )
    parser.add_argument(
        "--config-mode",
        choices=["isolated", "profile", "inherit"],
        default=None,
        help="Config isolation mode: isolated, profile, or inherit.",
    )
    parser.add_argument("-m", "--model", default=None, help="Override Codex model.")
    parser.add_argument("--provider", default=None, help="Override Codex model_provider.")
    parser.add_argument("--effort", default=None, help="Override model_reasoning_effort.")
    parser.add_argument("--preset", default=None, help="Role preset (explore/coding/frontend/review/debug/fast).")
    parser.add_argument("--persist", action="store_true", help="Keep session on disk (disable --ephemeral).")
    parser.add_argument("--add-dir", action="append", default=[], help="Extra writable directory.")
    parser.add_argument("--raw", action="store_true", help="Disable JSON output schema.")
    parser.add_argument("--search", action="store_true", help="Enable live web search.")
    parser.add_argument("--no-worker-profile", action="store_true", help="Alias for `--config-mode inherit`.")
    parser.add_argument("--quiet", action="store_true", help="Suppress live stderr progress milestones.")
    parser.add_argument("--no-panel-sync", action="store_true", help="Disable live sync to panel.")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600).")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_ROOT), help="Directory for run logs.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrate OpenAI Codex CLI cleanly (Agy Codex).")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_exec = subparsers.add_parser("exec", help="Run a new non-interactive Codex task.")
    add_shared_options(p_exec, default_mode=None)
    p_exec.add_argument(
        "--worktree",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help="Run inside an isolated Git worktree.",
    )
    p_exec.add_argument("--prompt", default=None, help="Task prompt.")
    p_exec.add_argument("--prompt-file", default=None, help="File containing the task prompt.")

    p_resume = subparsers.add_parser("resume", help="Resume an existing Codex session.")
    add_shared_options(p_resume, default_mode=None)
    p_resume.add_argument("session_id", nargs="?", default=None, help="Session ID to resume.")
    p_resume.add_argument("--last", action="store_true", help="Resume the most recent session.")
    p_resume.add_argument("--prompt", default=None, help="Follow-up instructions.")
    p_resume.add_argument("--prompt-file", default=None, help="File containing follow-up instructions.")

    p_review = subparsers.add_parser("review", help="Run a read-only code review.")
    add_shared_options(p_review, default_mode="read-only")
    p_review.add_argument("--base", default=None, help="Review changes against a base branch.")
    p_review.add_argument("--commit", default=None, help="Review a specific commit SHA.")
    p_review.add_argument("--uncommitted", action="store_true", help="Review uncommitted changes.")
    p_review.add_argument("--prompt", default=None, help="Custom review instructions.")
    p_review.add_argument("--prompt-file", default=None, help="File containing custom review instructions.")

    subparsers.add_parser("doctor", help="Check local Codex CLI, auth, catalog, config, and SQLite health.")

    p_models = subparsers.add_parser("models", help="List visible Codex models, providers, and presets.")
    p_models.add_argument("--query", default="", help="Filter models by slug/display name.")
    p_models.add_argument("--provider", default="", help="Filter models by provider.")
    p_models.add_argument("--exclude-filtered", action="store_true", help="Hide excluded_models entries.")

    p_preview = subparsers.add_parser("preview", help="Dry-run preview of resolved Codex flags (redacted).")
    p_preview.add_argument("-m", "--model", default=None)
    p_preview.add_argument("--provider", default=None)
    p_preview.add_argument("--effort", default=None)
    p_preview.add_argument("--preset", default=None)
    p_preview.add_argument("--config-mode", choices=["isolated", "profile", "inherit"], default=None)
    p_preview.add_argument("--mode", choices=["write", "read-only"], default=None)

    args = parser.parse_args()
    if args.subcommand == "doctor":
        return run_doctor()
    if args.subcommand == "models":
        res = dict(
            codex_catalog.discover_models_and_config(
                query=args.query,
                provider=args.provider,
                include_hidden=False,
                include_excluded=not args.exclude_filtered,
            )
        )
        res.pop("hidden_models_count", None)
        res["all_models_count"] = res["visible_models_count"]
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.subcommand == "preview":
        res = preview_command_report(
            model=args.model,
            provider=args.provider,
            effort=args.effort,
            preset=args.preset,
            config_mode=args.config_mode,
            mode=args.mode,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    codex_bin = find_codex_bin()
    codex_home = get_codex_home()
    data_dir = codex_catalog.get_plugin_data_dir()
    initial_cwd = Path(args.cwd).resolve()
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(args.log_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "codex.log"
    output_file = run_dir / (
        "output.txt" if args.raw or args.subcommand == "review" else "output.json"
    )

    worktree_info: dict[str, str] | None = None
    cwd = initial_cwd
    if args.subcommand == "exec" and args.worktree is not None:
        worktree_info = create_isolated_worktree(
            initial_cwd, args.worktree if args.worktree else None, run_id, data_dir=data_dir
        )
        cwd = Path(worktree_info["path"])
        emit_progress(
            args.quiet,
            f"worktree={worktree_info['path']} (branch={worktree_info['branch']})",
        )

    has_prompt = False
    prompt_text = ""

    if args.subcommand == "exec":
        prompt_text = read_prompt(args.prompt, args.prompt_file)
        cmd, meta = build_codex_command(
            codex_bin,
            cwd,
            output_file,
            subcommand="exec",
            codex_home=codex_home,
            data_dir=data_dir,
            cli_model=args.model,
            cli_provider=args.provider,
            cli_effort=args.effort,
            preset=args.preset,
            cli_mode=args.mode,
            cli_config_mode=args.config_mode,
            no_worker_profile=args.no_worker_profile,
            persist=args.persist,
            raw=args.raw,
            search=args.search,
            add_dir=args.add_dir,
            allow_ephemeral=True,
            allow_schema=True,
            allow_extra_dirs=True,
            allow_search=True,
            allow_json=True,
            check_git=True,
            quiet=args.quiet,
        )
        cmd.append(prompt_text)
        has_prompt = True
    elif args.subcommand == "resume":
        prompt_text = read_prompt(args.prompt, args.prompt_file)
        cmd, meta = build_codex_command(
            codex_bin,
            cwd,
            output_file,
            subcommand="resume",
            codex_home=codex_home,
            data_dir=data_dir,
            cli_model=args.model,
            cli_provider=args.provider,
            cli_effort=args.effort,
            preset=args.preset,
            cli_mode=args.mode,
            cli_config_mode=args.config_mode,
            no_worker_profile=args.no_worker_profile,
            persist=args.persist,
            raw=args.raw,
            allow_ephemeral=False,
            allow_schema=True,
            allow_extra_dirs=False,
            allow_search=False,
            allow_json=True,
            check_git=True,
            quiet=args.quiet,
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
        cmd, meta = build_codex_command(
            codex_bin,
            cwd,
            output_file,
            subcommand="review",
            codex_home=codex_home,
            data_dir=data_dir,
            cli_model=args.model,
            cli_provider=args.provider,
            cli_effort=args.effort,
            preset=args.preset,
            cli_mode="read-only",
            cli_config_mode=args.config_mode,
            no_worker_profile=args.no_worker_profile,
            persist=args.persist,
            raw=args.raw,
            allow_ephemeral=True,
            allow_schema=False,
            allow_extra_dirs=False,
            allow_search=False,
            allow_json=False,
            check_git=False,
            quiet=args.quiet,
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

    eff = meta["effective"]
    fallback_model = eff.get("model")
    panel_meta: dict[str, Any] | None = None
    if args.subcommand in ("exec", "resume") and not args.no_panel_sync:
        panel_meta = {
            "run_id": run_id,
            "cwd": str(cwd),
            "mode": eff.get("mode"),
            "config_mode": eff.get("config_mode"),
            "model": fallback_model,
            "provider": eff.get("provider"),
            "effort": eff.get("effort"),
            "preset": eff.get("preset"),
            "persist": bool(args.persist),
            "requested": meta.get("requested"),
            "effective": eff,
            "warnings": meta.get("warnings") or [],
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
        data_dir=data_dir,
    )
    summary["config_mode"] = eff.get("config_mode")
    summary["provider"] = eff.get("provider")
    summary["effort"] = eff.get("effort")
    summary["preset"] = eff.get("preset")
    summary["requested"] = meta.get("requested")
    summary["effective"] = eff
    summary["warnings"] = meta.get("warnings") or []
    if worktree_info is not None:
        summary["worktree"] = worktree_info
    summary["command"] = sanitize_command_for_meta(cmd, has_prompt)

    codex_catalog.atomic_write_text(
        run_dir / "meta.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
