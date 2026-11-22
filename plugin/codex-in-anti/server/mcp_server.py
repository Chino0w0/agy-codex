#!/usr/bin/env python3
"""Stdio MCP server & MCP App host for codex-in-anti.

Exposes:
- Model + UI tools: `start_codex_task`, `open_codex_panel`
- Model-only tools: `wait_codex_task`
- App-only tools (zero model token overhead): `get_run_events`, `list_runs`, `cancel_run`
- UI resource: `ui://codex-in-anti/app.html` (MIME `text/html;profile=mcp-app`)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SERVER_DIR.parent
APP_URI = "ui://codex-in-anti/app.html"
SHELL_WRAPPER_RE = re.compile(
    r"^(?:/opt/homebrew/bin/bash|/bin/bash|/bin/zsh|/usr/bin/env\s+\w+|bash|zsh)\s+-(?:l?c)\s+(.*)$",
    re.DOTALL,
)


def load_codex_run_module() -> Any:
    """Import `codex_run.py` from sibling server directory or source repo."""
    candidates = [
        SERVER_DIR / "codex_run.py",
        PLUGIN_ROOT.parent.parent / "skill" / "codex-worker" / "scripts" / "codex_run.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("codex_run", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                # Ensure DEFAULT_SCHEMA resolves even when installed standalone in plugin/server/
                schema_candidates = [
                    SERVER_DIR / "handoff_schema.json",
                    path.parent.parent / "resources" / "handoff_schema.json",
                ]
                for schema_path in schema_candidates:
                    if schema_path.is_file():
                        mod.DEFAULT_SCHEMA = schema_path
                        break
                return mod
    raise FileNotFoundError("Could not locate codex_run.py for codex-in-anti MCP server.")


codex_run = load_codex_run_module()


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


def load_app_html() -> str:
    """Load the single-file MCP App HTML bundle."""
    env_override = os.environ.get("CODEX_APP_HTML")
    candidates = [
        Path(env_override).expanduser() if env_override else None,
        PLUGIN_ROOT / "assets" / "app.html",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return (
        "<!doctype html><html><body><p>Codex Worker UI asset missing "
        "(expected assets/app.html).</p></body></html>"
    )


def safe_parse_handoff(raw_text: str) -> Any:
    """Parse JSON handoff if valid, otherwise return raw text."""
    stripped = raw_text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


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


@dataclass
class RunState:
    run_id: str
    cwd: str
    mode: str
    config_mode: str
    model: str | None
    effort: str | None
    prompt: str
    persist: bool = False
    resume_session_id: str | None = None
    worktree: dict[str, str] | None = None
    status: str = "running"  # running | done | error | cancelled
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    duration_sec: float | None = None
    exit_code: int | None = None
    session_id: str | None = None
    tokens_used: int | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
    )
    saw_usage: bool = False
    result: Any = None
    error: str | None = None
    log_file: str = ""
    output_file: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    last_agent_message: str = ""
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_summary_dict(self) -> dict[str, Any]:
        elapsed = (
            self.duration_sec
            if self.duration_sec is not None
            else round(max(0.0, time.time() - self.started_at), 2)
        )
        return {
            "run_id": self.run_id,
            "ok": self.status == "done" and (self.exit_code == 0 or self.exit_code is None),
            "status": self.status,
            "cwd": self.cwd,
            "mode": self.mode,
            "config_mode": self.config_mode,
            "model": self.model,
            "effort": self.effort,
            "prompt_preview": self.prompt[:180],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_sec": elapsed,
            "duration_sec": self.duration_sec,
            "exit_code": self.exit_code,
            "session_id": self.session_id,
            "tokens_used": self.tokens_used,
            "usage": self.usage if self.saw_usage else None,
            "worktree": self.worktree,
            "result": self.result,
            "error": self.error,
            "log_file": self.log_file,
            "output_file": self.output_file,
        }


class RunManager:
    """Thread-safe manager for live and persisted Codex runs."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runs_dir = data_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()

    def _state_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "state.json"

    def _persist_state(self, state: RunState) -> None:
        payload = state.to_summary_dict()
        payload["events"] = state.events
        payload["seq"] = state.seq
        target = self._state_path(state.run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, target)

    def _load_persisted_state(self, run_id: str) -> dict[str, Any] | None:
        path = self._state_path(run_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def start_run(self, params: dict[str, Any]) -> RunState:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("'prompt' is required.")

        raw_cwd = str(params.get("cwd") or ".").strip()
        initial_cwd = Path(raw_cwd).expanduser().resolve()
        if not initial_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {initial_cwd}")

        mode = str(params.get("mode") or "write")
        if mode not in ("write", "read-only"):
            raise ValueError("'mode' must be 'write' or 'read-only'.")

        requested_config_mode = str(params.get("config_mode") or "isolated")
        if requested_config_mode not in ("isolated", "profile", "inherit"):
            raise ValueError("'config_mode' must be 'isolated', 'profile', or 'inherit'.")

        model = params.get("model")
        model_str = str(model).strip() if isinstance(model, str) and model.strip() else None
        effort = params.get("effort")
        effort_str = str(effort).strip() if isinstance(effort, str) and effort.strip() else None
        persist = bool(params.get("persist", False))
        resume_sid = params.get("session_id")
        resume_sid_str = (
            str(resume_sid).strip() if isinstance(resume_sid, str) and resume_sid.strip() else None
        )
        worktree_arg = params.get("worktree")
        timeout = int(params.get("timeout") or 600)

        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file = run_dir / "codex.log"
        output_file = run_dir / "output.json"

        effective_config_mode = codex_run.resolve_config_mode(
            requested_config_mode, no_worker_profile=False, quiet=True
        )
        codex_bin = codex_run.find_codex_bin()
        codex_home = codex_run.get_codex_home()

        worktree_info: dict[str, str] | None = None
        cwd = initial_cwd
        if worktree_arg is not None and not resume_sid_str:
            wt_name = (
                str(worktree_arg).strip()
                if isinstance(worktree_arg, str) and str(worktree_arg).strip()
                else None
            )
            worktree_info = codex_run.create_isolated_worktree(initial_cwd, wt_name, run_id)
            cwd = Path(worktree_info["path"])

        ns = argparse.Namespace(
            model=model_str,
            effort=effort_str,
            search=False,
            add_dir=[],
            persist=persist,
            raw=False,
        )
        cmd = [codex_bin, "exec"]
        sandbox = "read-only" if mode == "read-only" else "workspace-write"
        isolated_meta = codex_run.append_codex_flags(
            cmd,
            ns,
            cwd,
            output_file,
            config_mode=effective_config_mode,
            codex_home=codex_home,
            sandbox=sandbox,
            allow_ephemeral=(resume_sid_str is None),
            allow_schema=True,
            allow_extra_dirs=True,
            allow_search=False,
            allow_json=True,
            check_git=True,
        )
        if resume_sid_str:
            cmd.extend(["resume", resume_sid_str, prompt])
        else:
            cmd.append(prompt)

        resolved_model = (
            model_str
            or (
                isolated_meta.get("model")
                if isinstance(isolated_meta.get("model"), str)
                else None
            )
        )

        state = RunState(
            run_id=run_id,
            cwd=str(cwd),
            mode=mode,
            config_mode=effective_config_mode,
            model=resolved_model,
            effort=effort_str,
            prompt=prompt,
            persist=persist,
            resume_session_id=resume_sid_str,
            worktree=worktree_info,
            log_file=str(log_file),
            output_file=str(output_file),
        )
        with self._lock:
            self._runs[run_id] = state
        with state.lock:
            self._persist_state(state)

        thread = threading.Thread(
            target=self._execute_worker,
            args=(state, cmd, log_file, output_file, timeout),
            daemon=True,
        )
        thread.start()
        return state

    def _append_event(self, state: RunState, event_payload: dict[str, Any]) -> None:
        state.seq += 1
        event_payload["seq"] = state.seq
        event_payload.setdefault("ts", round(time.time() - state.started_at, 2))
        state.events.append(event_payload)

    def _execute_worker(
        self,
        state: RunState,
        cmd: list[str],
        log_file: Path,
        output_file: Path,
        timeout: int,
    ) -> None:
        env = codex_run.build_exec_env()
        timed_out = False
        log_lines: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            with state.lock:
                state.status = "error"
                state.error = f"Failed to launch codex CLI: {exc}"
                state.finished_at = time.time()
                state.duration_sec = round(state.finished_at - state.started_at, 2)
                state.exit_code = 1
                self._append_event(state, {"kind": "error", "message": state.error})
                self._persist_state(state)
                state.done_event.set()
            return

        with state.lock:
            state.process = proc

        def on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            try:
                proc.terminate()
            except OSError:
                pass

        timer: threading.Timer | None = None
        if timeout > 0:
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
                        evt = json.loads(stripped)
                    except json.JSONDecodeError:
                        evt = None
                    if isinstance(evt, dict):
                        self._handle_json_event(state, evt)
                        continue

                if "ERROR" in stripped or "fatal" in stripped.lower() or "warning:" in stripped.lower():
                    with state.lock:
                        self._append_event(state, {"kind": "log", "text": stripped})
                        self._persist_state(state)

            if timed_out and proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            else:
                proc.wait()
        except Exception as exc:
            with state.lock:
                state.error = str(exc)
                self._append_event(state, {"kind": "error", "message": str(exc)})
        finally:
            if timer is not None:
                timer.cancel()

        log_file.write_text("".join(log_lines), encoding="utf-8")

        raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
        if not raw_output and state.last_agent_message:
            raw_output = state.last_agent_message.strip()
        parsed_result = safe_parse_handoff(raw_output) if raw_output else None

        with state.lock:
            state.process = None
            state.finished_at = time.time()
            state.duration_sec = round(state.finished_at - state.started_at, 2)
            state.exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
            state.result = parsed_result
            if state.saw_usage:
                state.tokens_used = (
                    state.usage["input_tokens"] + state.usage["output_tokens"]
                )
            if state.status != "cancelled":
                if timed_out:
                    state.status = "error"
                    state.error = f"Timed out after {timeout}s"
                    self._append_event(state, {"kind": "error", "message": state.error})
                elif state.exit_code == 0:
                    state.status = "done"
                else:
                    state.status = "error"
                    if not state.error:
                        state.error = f"codex exited with code {state.exit_code}"
            self._persist_state(state)
            state.done_event.set()

    def _handle_json_event(self, state: RunState, evt: dict[str, Any]) -> None:
        etype = evt.get("type")
        with state.lock:
            if etype == "thread.started":
                tid = evt.get("thread_id")
                if isinstance(tid, str) and tid:
                    state.session_id = tid
                    self._append_event(state, {"kind": "session", "session_id": tid})
            elif etype in ("item.started", "item.completed"):
                item = evt.get("item") or {}
                item_id = str(item.get("id") or "")
                itype = str(item.get("type") or "")
                if itype == "command_execution":
                    raw_cmd = str(item.get("command") or "")
                    self._append_event(
                        state,
                        {
                            "kind": "command",
                            "item_id": item_id,
                            "command": raw_cmd,
                            "display_command": strip_shell_wrapper(raw_cmd),
                            "status": str(item.get("status") or ""),
                            "exit_code": item.get("exit_code"),
                            "output": str(item.get("aggregated_output") or "")[:12000],
                        },
                    )
                elif itype == "file_change":
                    changes = item.get("changes") or []
                    norm_changes = [
                        {
                            "path": str(c.get("path") or ""),
                            "kind": str(c.get("kind") or "modify"),
                        }
                        for c in changes
                        if isinstance(c, dict)
                    ]
                    self._append_event(
                        state,
                        {
                            "kind": "file_change",
                            "item_id": item_id,
                            "status": str(item.get("status") or ""),
                            "changes": norm_changes,
                        },
                    )
                elif itype == "agent_message":
                    text = str(item.get("text") or "")
                    state.last_agent_message = text
                    if text.strip() and not is_structured_handoff_text(text):
                        self._append_event(
                            state,
                            {
                                "kind": "commentary",
                                "item_id": item_id,
                                "text": text.strip(),
                            },
                        )
            elif etype == "turn.completed":
                u = evt.get("usage") or {}
                if isinstance(u, dict):
                    state.saw_usage = True
                    in_t = int(u.get("input_tokens") or 0)
                    c_t = int(u.get("cached_input_tokens") or 0)
                    out_t = int(u.get("output_tokens") or 0)
                    r_t = int(u.get("reasoning_output_tokens") or 0)
                    state.usage["input_tokens"] += in_t
                    state.usage["cached_input_tokens"] += c_t
                    state.usage["output_tokens"] += out_t
                    state.usage["reasoning_output_tokens"] += r_t
                    state.tokens_used = (
                        state.usage["input_tokens"] + state.usage["output_tokens"]
                    )
                    self._append_event(
                        state,
                        {
                            "kind": "turn_done",
                            "usage": {
                                "input_tokens": in_t,
                                "cached_input_tokens": c_t,
                                "output_tokens": out_t,
                                "reasoning_output_tokens": r_t,
                            },
                        },
                    )
            elif etype in ("error", "turn.failed"):
                err_obj = evt.get("error")
                msg = (
                    evt.get("message")
                    or (err_obj.get("message") if isinstance(err_obj, dict) else None)
                    or json.dumps(evt, ensure_ascii=False)
                )
                state.error = str(msg)
                self._append_event(state, {"kind": "error", "message": state.error})
            self._persist_state(state)

    def wait_run(self, run_id: str, timeout: int = 600) -> dict[str, Any]:
        with self._lock:
            state = self._runs.get(run_id)
        if state is None:
            persisted = self._load_persisted_state(run_id)
            if persisted is not None:
                persisted.pop("events", None)
                return persisted
            raise ValueError(f"Unknown run_id: {run_id}")

        finished = state.done_event.wait(timeout=timeout if timeout > 0 else None)
        with state.lock:
            summary = state.to_summary_dict()
        if not finished:
            summary["wait_timed_out"] = True
        return summary

    def get_run_events(self, run_id: str, since_seq: int = 0) -> dict[str, Any]:
        with self._lock:
            state = self._runs.get(run_id)
        if state is not None:
            with state.lock:
                summary = state.to_summary_dict()
                events = [ev for ev in state.events if int(ev.get("seq", 0)) > since_seq]
                latest_seq = state.seq
            summary["events"] = events
            summary["latest_seq"] = latest_seq
            return summary

        persisted = self._load_persisted_state(run_id)
        if persisted is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        all_events = persisted.pop("events", [])
        latest_seq = int(persisted.pop("seq", len(all_events)))
        persisted["events"] = [ev for ev in all_events if int(ev.get("seq", 0)) > since_seq]
        persisted["latest_seq"] = latest_seq
        return persisted

    def list_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        with self._lock:
            active_states = list(self._runs.values())
        for state in active_states:
            with state.lock:
                seen[state.run_id] = state.to_summary_dict()

        if self.runs_dir.is_dir():
            for child in sorted(self.runs_dir.iterdir(), reverse=True):
                if child.name in seen or not child.is_dir():
                    continue
                persisted = self._load_persisted_state(child.name)
                if persisted is not None:
                    persisted.pop("events", None)
                    persisted.pop("seq", None)
                    seen[child.name] = persisted
                if len(seen) >= limit * 2:
                    break

        items = sorted(seen.values(), key=lambda r: float(r.get("started_at") or 0), reverse=True)
        return items[:limit]

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._runs.get(run_id)
        if state is None:
            raise ValueError(f"Run {run_id} is not active in this server process.")

        with state.lock:
            proc = state.process
            if state.status != "running" or proc is None:
                return state.to_summary_dict()
            state.status = "cancelled"
            state.error = "Cancelled by user."
            self._append_event(state, {"kind": "error", "message": "Cancelled by user."})
            self._persist_state(state)

        try:
            proc.terminate()
        except OSError:
            pass
        return state.to_summary_dict()


TOOLS_DEF: list[dict[str, Any]] = [
    {
        "name": "start_codex_task",
        "title": "Run Codex Worker",
        "description": (
            "Start a bounded OpenAI Codex CLI task in the background with whitelist config "
            "isolation and render a live Codex-style process timeline inline. Returns a run_id "
            "immediately; follow up with wait_codex_task(run_id) to get the final result."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "cwd"],
            "properties": {
                "prompt": {"type": "string", "description": "Bounded task prompt for Codex."},
                "cwd": {"type": "string", "description": "Absolute working directory path."},
                "mode": {
                    "type": "string",
                    "enum": ["write", "read-only"],
                    "default": "write",
                    "description": "Sandbox policy: workspace-write or read-only.",
                },
                "config_mode": {
                    "type": "string",
                    "enum": ["isolated", "profile", "inherit"],
                    "default": "isolated",
                    "description": "Config isolation mode (default: isolated whitelist).",
                },
                "model": {"type": "string", "description": "Optional Codex model override."},
                "effort": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Reasoning effort override.",
                },
                "worktree": {
                    "type": "string",
                    "description": "Optional Git worktree name to isolate changes on a new branch.",
                },
                "persist": {
                    "type": "boolean",
                    "default": False,
                    "description": "Persist Codex session on disk for later resume.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Existing session_id to resume instead of starting fresh.",
                },
                "timeout": {
                    "type": "integer",
                    "default": 600,
                    "description": "Execution timeout in seconds.",
                },
            },
        },
        "_meta": {"ui": {"resourceUri": APP_URI}},
    },
    {
        "name": "wait_codex_task",
        "title": "Wait for Codex Worker",
        "description": (
            "Wait for a run started by start_codex_task to complete and return its full "
            "structured handoff result, changed_files, token usage, and log path."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {
                "run_id": {"type": "string", "description": "Run ID returned by start_codex_task."},
                "timeout": {
                    "type": "integer",
                    "default": 600,
                    "description": "Maximum seconds to wait for completion.",
                },
            },
        },
    },
    {
        "name": "open_codex_panel",
        "title": "Codex Worker",
        "description": (
            "Open the Codex Worker live monitor and run history viewer in the side pane or "
            "full-page view."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Optional run_id to focus initially.",
                }
            },
        },
        "_meta": {
            "ui": {"resourceUri": APP_URI},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "Codex Worker"},
                    {"type": "global", "title": "Codex Worker"},
                ]
            },
        },
    },
    {
        "name": "get_run_events",
        "title": "Get Run Events",
        "description": "App-only tool: fetch incremental process timeline events for a Codex run.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {
                "run_id": {"type": "string"},
                "since_seq": {"type": "integer", "default": 0},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "list_runs",
        "title": "List Codex Runs",
        "description": "App-only tool: list recent and active Codex runs.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "default": 25},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "cancel_run",
        "title": "Cancel Codex Run",
        "description": "App-only tool: terminate an active Codex run.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {
                "run_id": {"type": "string"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
]


class McpServer:
    """Minimal JSON-RPC 2.0 stdio MCP server."""

    def __init__(self, manager: RunManager) -> None:
        self.manager = manager

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if req_id is None:
            # JSON-RPC notification (e.g. notifications/initialized, notifications/cancelled)
            return None

        try:
            if method == "initialize":
                proto = str(params.get("protocolVersion") or "2025-03-26")
                result = {
                    "protocolVersion": proto,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": "codex-in-anti", "version": "0.2.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS_DEF}
            elif method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                result = self._call_tool(name, arguments)
            elif method == "resources/list":
                result = {
                    "resources": [
                        {
                            "uri": APP_URI,
                            "name": "Codex Worker UI",
                            "description": "Live Codex-style process timeline viewer",
                            "mimeType": "text/html;profile=mcp-app",
                        }
                    ]
                }
            elif method == "resources/read":
                uri = str(params.get("uri") or "")
                if uri != APP_URI:
                    return self._error(req_id, -32602, f"Unknown resource URI: {uri}")
                result = {
                    "contents": [
                        {
                            "uri": APP_URI,
                            "mimeType": "text/html;profile=mcp-app",
                            "text": load_app_html(),
                            "_meta": {"ui": {"prefersBorder": False}},
                        }
                    ]
                }
            else:
                return self._error(req_id, -32601, f"Method not found: {method}")

            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            return self._error(req_id, -32603, str(exc))

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "start_codex_task":
            state = self.manager.start_run(args)
            summary = state.to_summary_dict()
            structured = {
                "view": "run",
                **summary,
            }
            text = (
                f"Started Codex task run_id={state.run_id} (mode={state.mode}, "
                f"config_mode={state.config_mode}, cwd={state.cwd}). "
                f"Call wait_codex_task(run_id={state.run_id!r}) next to await completion."
            )
            return {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
                "isError": False,
            }

        if name == "wait_codex_task":
            run_id = str(args.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            timeout = int(args.get("timeout") or 600)
            summary = self.manager.wait_run(run_id, timeout=timeout)
            return {
                "content": [
                    {"type": "text", "text": json.dumps(summary, ensure_ascii=False, indent=2)}
                ],
                "structuredContent": summary,
                "isError": not bool(summary.get("ok")),
            }

        if name == "open_codex_panel":
            runs = self.manager.list_runs(limit=25)
            requested_id = args.get("run_id")
            selected_run_id = (
                str(requested_id)
                if isinstance(requested_id, str) and requested_id
                else (runs[0]["run_id"] if runs else None)
            )
            payload = {
                "view": "panel",
                "selected_run_id": selected_run_id,
                "runs": runs,
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Codex Worker panel ready ({len(runs)} recent runs).",
                    }
                ],
                "structuredContent": payload,
                "isError": False,
            }

        if name == "get_run_events":
            run_id = str(args.get("run_id") or "").strip()
            since_seq = int(args.get("since_seq") or 0)
            data = self.manager.get_run_events(run_id, since_seq=since_seq)
            return {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": False,
            }

        if name == "list_runs":
            limit = int(args.get("limit") or 25)
            runs = self.manager.list_runs(limit=limit)
            payload = {"runs": runs}
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "structuredContent": payload,
                "isError": False,
            }

        if name == "cancel_run":
            run_id = str(args.get("run_id") or "").strip()
            summary = self.manager.cancel_run(run_id)
            return {
                "content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}],
                "structuredContent": summary,
                "isError": False,
            }

        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex Worker MCP App Server")
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / ".codex" / "codex-in-anti-data"),
        help="Persistent directory for run state and logs.",
    )
    args = parser.parse_args()
    manager = RunManager(Path(args.data_dir).expanduser())
    server = McpServer(manager)

    write_lock = threading.Lock()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            err = McpServer._error(None, -32700, f"Parse error: {exc}")
            with write_lock:
                sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            continue

        resp = server.handle_request(req)
        if resp is not None:
            with write_lock:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
