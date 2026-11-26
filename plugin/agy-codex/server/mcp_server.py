#!/usr/bin/env python3
"""Stdio MCP server & MCP App host for Agy Codex (`agy-codex`).

Exposes:
- Model-visible tools:
  `list_codex_models`, `start_codex_task`, `wait_codex_task`,
  `get_codex_task_status`, `stop_codex_task`, `open_codex_panel`
- App-only tools (zero model token overhead):
  `get_run_events`, `list_runs`, `list_local_sessions`, `get_local_session_detail`,
  `get_stats`, `get_workbench_config`, `save_workbench_config`,
  `preview_resolved_command`, `run_doctor`, `cancel_run`
- UI resource: `ui://agy-codex/app.html` (`text/html;profile=mcp-app`)
"""

from __future__ import annotations

import argparse
import json
import os
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
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_catalog  # noqa: E402
import codex_run  # noqa: E402
import codex_telemetry  # noqa: E402

APP_URI = "ui://agy-codex/app.html"


def _is_pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return str(pid) in (res.stdout or "")
        except Exception:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def load_app_html() -> str:
    env_override = os.environ.get("AGY_CODEX_APP_HTML") or os.environ.get("CODEX_APP_HTML")
    candidates = [
        Path(env_override).expanduser() if env_override else None,
        PLUGIN_ROOT / "assets" / "app.html",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return (
        "<!doctype html><html><body><p>Agy Codex UI asset missing "
        "(expected assets/app.html).</p></body></html>"
    )


def safe_parse_handoff(raw_text: str) -> Any:
    stripped = raw_text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


@dataclass
class RunState:
    run_id: str
    cwd: str
    mode: str
    config_mode: str
    model: str | None
    provider: str | None
    effort: str | None
    preset: str | None
    prompt: str
    persist: bool = False
    resume_session_id: str | None = None
    worktree: dict[str, str] | None = None
    requested: dict[str, Any] = field(default_factory=dict)
    effective: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=lambda: {"model": None, "effort": None})
    warnings: list[str] = field(default_factory=list)
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
            "source": "mcp",
            "ok": self.status == "done" and (self.exit_code == 0 or self.exit_code is None),
            "status": self.status,
            "cwd": self.cwd,
            "mode": self.mode,
            "config_mode": self.config_mode,
            "model": self.model,
            "provider": self.provider,
            "effort": self.effort,
            "preset": self.preset,
            "persist": self.persist,
            "requested": self.requested,
            "effective": self.effective,
            "observed": self.observed,
            "warnings": self.warnings,
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
    """Thread-safe manager for live and persisted Agy Codex runs."""

    def __init__(self, data_dir: Path, codex_home: Path | None = None) -> None:
        self.data_dir = data_dir
        self.codex_home = codex_home
        self.runs_dir = data_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._any_done_cond = threading.Condition(self._lock)

    def _state_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "state.json"

    def _persist_state(self, state: RunState) -> None:
        payload = state.to_summary_dict()
        payload["events"] = state.events
        payload["seq"] = state.seq
        target = self._state_path(state.run_id)
        codex_catalog.atomic_write_text(
            target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    def _load_persisted_state(self, run_id: str) -> dict[str, Any] | None:
        path = self._state_path(run_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._runs.values() if s.status == "running")

    def start_run(self, params: dict[str, Any]) -> RunState:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("'prompt' is required.")

        raw_cwd = str(params.get("cwd") or ".").strip()
        initial_cwd = Path(raw_cwd).expanduser().resolve()
        if not initial_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {initial_cwd}")

        mode_arg = params.get("mode")
        mode_str = str(mode_arg).strip() if isinstance(mode_arg, str) and mode_arg.strip() else None
        if mode_str and mode_str not in ("write", "read-only"):
            raise ValueError("'mode' must be 'write' or 'read-only'.")

        cfg_mode_arg = params.get("config_mode")
        cfg_mode_str = (
            str(cfg_mode_arg).strip()
            if isinstance(cfg_mode_arg, str) and cfg_mode_arg.strip()
            else None
        )
        if cfg_mode_str and cfg_mode_str not in ("isolated", "profile", "inherit"):
            raise ValueError("'config_mode' must be 'isolated', 'profile', or 'inherit'.")

        model = params.get("model")
        model_str = str(model).strip() if isinstance(model, str) and model.strip() else None
        provider = params.get("provider")
        provider_str = str(provider).strip() if isinstance(provider, str) and provider.strip() else None
        effort = params.get("effort")
        effort_str = str(effort).strip() if isinstance(effort, str) and effort.strip() else None
        preset = params.get("preset")
        preset_str = str(preset).strip() if isinstance(preset, str) and preset.strip() else None
        persist = bool(params.get("persist", False))
        resume_sid = params.get("session_id")
        resume_sid_str = (
            str(resume_sid).strip() if isinstance(resume_sid, str) and resume_sid.strip() else None
        )
        worktree_arg = params.get("worktree")
        search = bool(params.get("search", False))
        raw = bool(params.get("raw", False))
        raw_add_dir = params.get("add_dir") or []
        add_dir = [str(d) for d in raw_add_dir if isinstance(d, str) and d.strip()]
        timeout = int(params.get("timeout") or 600)

        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file = run_dir / "codex.log"
        output_file = run_dir / ("output.txt" if raw else "output.json")

        codex_bin = codex_run.find_codex_bin()
        codex_home = codex_catalog.get_codex_home(self.codex_home)

        worktree_info: dict[str, str] | None = None
        cwd = initial_cwd
        if worktree_arg is not None and not resume_sid_str:
            wt_name = (
                str(worktree_arg).strip()
                if isinstance(worktree_arg, str) and str(worktree_arg).strip()
                else None
            )
            worktree_info = codex_run.create_isolated_worktree(
                initial_cwd, wt_name, run_id, data_dir=self.data_dir
            )
            cwd = Path(worktree_info["path"])

        cmd, meta = codex_run.build_codex_command(
            codex_bin,
            cwd,
            output_file,
            subcommand="resume" if resume_sid_str else "exec",
            codex_home=codex_home,
            data_dir=self.data_dir,
            cli_model=model_str,
            cli_provider=provider_str,
            cli_effort=effort_str,
            preset=preset_str,
            cli_mode=mode_str,
            cli_config_mode=cfg_mode_str,
            persist=persist,
            raw=raw,
            search=search if not resume_sid_str else False,
            add_dir=add_dir if not resume_sid_str else [],
            allow_ephemeral=(resume_sid_str is None),
            allow_schema=not raw,
            allow_extra_dirs=(resume_sid_str is None),
            allow_search=(resume_sid_str is None),
            allow_json=True,
            check_git=True,
            quiet=True,
        )
        if resume_sid_str:
            cmd.extend(["resume", resume_sid_str, prompt])
        else:
            cmd.append(prompt)

        eff = meta["effective"]
        warnings = list(meta.get("warnings") or [])
        wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
        max_conc = int((wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5))
        cur_active = self.active_count()
        if cur_active >= max_conc:
            warnings.append(
                f"Active runs ({cur_active}) reached max_concurrent_runs ({max_conc}); consider rolling wait/cleanup."
            )

        state = RunState(
            run_id=run_id,
            cwd=str(cwd),
            mode=str(eff.get("mode") or "write"),
            config_mode=str(eff.get("config_mode") or "isolated"),
            model=eff.get("model"),
            provider=eff.get("provider"),
            effort=eff.get("effort"),
            preset=eff.get("preset"),
            prompt=prompt,
            persist=persist,
            resume_session_id=resume_sid_str,
            worktree=worktree_info,
            requested=meta.get("requested") or {},
            effective=eff,
            warnings=warnings,
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

    def _notify_done(self, state: RunState) -> None:
        state.done_event.set()
        with self._any_done_cond:
            self._any_done_cond.notify_all()

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

        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
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

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            with state.lock:
                state.status = "error"
                state.error = f"Failed to launch codex CLI: {exc}"
                state.finished_at = time.time()
                state.duration_sec = round(state.finished_at - state.started_at, 2)
                state.exit_code = 1
                self._append_event(state, {"kind": "error", "message": state.error})
                self._persist_state(state)
            self._notify_done(state)
            return

        with state.lock:
            state.process = proc

        def on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            codex_run.terminate_process_tree(proc)

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
                    codex_run.terminate_process_tree(proc)
                    proc.wait(timeout=5)
            else:
                proc.wait()
        except Exception as exc:
            with state.lock:
                state.error = str(exc)
                self._append_event(state, {"kind": "error", "message": str(exc)})
        finally:
            if timer is not None:
                timer.cancel()
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

        combined_log = "".join(log_lines)
        log_file.write_text(combined_log, encoding="utf-8", newline="\n")

        raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
        if not raw_output and state.last_agent_message:
            raw_output = state.last_agent_message.strip()
        parsed_result = safe_parse_handoff(raw_output) if raw_output else None

        fallback = codex_run.parse_telemetry(combined_log)

        with state.lock:
            state.process = None
            state.finished_at = time.time()
            state.duration_sec = round(state.finished_at - state.started_at, 2)
            state.exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
            state.result = parsed_result
            if fallback.get("model") and not state.observed.get("model"):
                state.observed["model"] = fallback["model"]
            if fallback.get("effort") and not state.observed.get("effort"):
                state.observed["effort"] = fallback["effort"]
            if state.observed.get("model"):
                state.model = state.observed["model"]
            if not state.session_id and fallback.get("session_id"):
                state.session_id = fallback["session_id"]
            if state.saw_usage:
                state.tokens_used = state.usage["input_tokens"] + state.usage["output_tokens"]
            elif fallback.get("tokens_used"):
                state.tokens_used = fallback["tokens_used"]

            handoff_status = (
                parsed_result.get("status") if isinstance(parsed_result, dict) else None
            )
            handoff_summary = (
                codex_catalog._clean_optional_str(parsed_result.get("summary"))
                if isinstance(parsed_result, dict)
                else None
            )
            if state.status != "cancelled":
                if timed_out:
                    state.status = "error"
                    state.error = f"Timed out after {timeout}s"
                    self._append_event(state, {"kind": "error", "message": state.error})
                elif state.exit_code == 0:
                    if handoff_status == "blocked":
                        state.status = "blocked"
                        if not state.error:
                            state.error = handoff_summary or "Worker reported status=blocked."
                    elif handoff_status == "failed":
                        state.status = "error"
                        if not state.error:
                            state.error = handoff_summary or "Worker reported status=failed."
                    else:
                        state.status = "done"
                else:
                    state.status = "error"
                    if not state.error:
                        state.error = f"codex exited with code {state.exit_code}"
            self._persist_state(state)
        self._notify_done(state)

    def _handle_json_event(self, state: RunState, evt: dict[str, Any]) -> None:
        etype = evt.get("type")
        with state.lock:
            if isinstance(evt.get("model"), str) and evt["model"].strip():
                state.observed["model"] = evt["model"].strip()
            if isinstance(evt.get("reasoning_effort"), str) and evt["reasoning_effort"].strip():
                state.observed["effort"] = evt["reasoning_effort"].strip()
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
                            "display_command": codex_run.strip_shell_wrapper(raw_cmd),
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
                    if text.strip() and not codex_run.is_structured_handoff_text(text):
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
                    state.tokens_used = state.usage["input_tokens"] + state.usage["output_tokens"]
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

    def _normalize_persisted_state(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("status") == "running":
            raw_pid = data.get("pid")
            pid = raw_pid if isinstance(raw_pid, int) else None
            if _is_pid_alive(pid):
                started_at = data.get("started_at")
                if isinstance(started_at, (int, float)):
                    data["elapsed_sec"] = round(max(0.0, time.time() - float(started_at)), 2)
            else:
                data["status"] = "error"
                data["ok"] = False
                data["error"] = "External CLI process exited unexpectedly."
        return data

    def _get_summary(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._runs.get(run_id)
        if state is not None:
            with state.lock:
                return state.to_summary_dict()
        persisted = self._load_persisted_state(run_id)
        if persisted is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        persisted.pop("events", None)
        persisted.pop("seq", None)
        return self._normalize_persisted_state(persisted)

    def wait_runs(
        self,
        run_ids: list[str],
        *,
        wait_mode: str = "all",
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Wait for one or multiple runs (`all` or `any`), treating wait timeout as non-fatal."""
        if not run_ids:
            raise ValueError("Provide 'run_id' or non-empty 'run_ids'.")
        if wait_mode not in ("all", "any"):
            raise ValueError("'wait_mode' must be 'all' or 'any'.")

        deadline = None if timeout <= 0 else (time.monotonic() + timeout)
        with self._any_done_cond:
            while True:
                summaries = [self._get_summary_locked_safe(rid) for rid in run_ids]
                finished_ids = [s["run_id"] for s in summaries if s.get("status") != "running"]
                still_running_ids = [s["run_id"] for s in summaries if s.get("status") == "running"]

                if not still_running_ids:
                    timed_out = False
                    break
                if wait_mode == "any" and finished_ids:
                    timed_out = False
                    break
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        timed_out = True
                        break
                    self._any_done_cond.wait(timeout=min(rem, 0.5))
                else:
                    self._any_done_cond.wait(timeout=0.5)

        summaries = [self._get_summary(rid) for rid in run_ids]
        still_running_ids = [s["run_id"] for s in summaries if s.get("status") == "running"]
        finished_ids = [s["run_id"] for s in summaries if s.get("status") != "running"]

        if len(run_ids) == 1:
            single = dict(summaries[0])
            if timed_out:
                single["wait_timed_out"] = True
                single["still_running_ids"] = still_running_ids
                single["note"] = (
                    "Wait timed out while task is still running. Timeout is NOT a task failure; "
                    "keep tracking with get_codex_task_status or wait_codex_task instead of re-spawning."
                )
            return single

        return {
            "wait_mode": wait_mode,
            "wait_timed_out": timed_out,
            "finished_ids": finished_ids,
            "still_running_ids": still_running_ids,
            "runs": summaries,
            **(
                {
                    "note": (
                        "Wait timed out while task(s) are still running. Timeout is NOT a task failure; "
                        "continue tracking rather than duplicate-spawning."
                    )
                }
                if timed_out
                else {}
            ),
        }

    def _get_summary_locked_safe(self, run_id: str) -> dict[str, Any]:
        state = self._runs.get(run_id)
        if state is not None:
            with state.lock:
                return state.to_summary_dict()
        persisted = self._load_persisted_state(run_id)
        if persisted is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        persisted.pop("events", None)
        persisted.pop("seq", None)
        return self._normalize_persisted_state(persisted)

    def get_status(self, run_ids: list[str]) -> dict[str, Any]:
        """Non-blocking status check for one or more runs."""
        items: list[dict[str, Any]] = []
        for rid in run_ids:
            ev_data = self.get_run_events(rid, since_seq=0)
            events = ev_data.get("events") or []
            latest_event = events[-1] if events else None
            ev_data.pop("events", None)
            ev_data["latest_event"] = latest_event
            ev_data["event_count"] = len(events)
            items.append(ev_data)

        wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
        max_conc = int((wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5))
        return {
            "active_runs": self.active_count(),
            "max_concurrent_runs": max_conc,
            "runs": items,
        }

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
        persisted = self._normalize_persisted_state(persisted)
        all_events = persisted.pop("events", [])
        latest_seq = int(persisted.pop("seq", len(all_events)))
        persisted["events"] = [ev for ev in all_events if int(ev.get("seq", 0)) > since_seq]
        persisted["latest_seq"] = latest_seq
        return persisted

    def list_runs(self, *, status: str = "all", limit: int = 40) -> list[dict[str, Any]]:
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
                    persisted = self._normalize_persisted_state(persisted)
                    persisted.pop("events", None)
                    persisted.pop("seq", None)
                    seen[child.name] = persisted
                if len(seen) >= max(limit * 3, 120):
                    break

        items = sorted(seen.values(), key=lambda r: float(r.get("started_at") or 0), reverse=True)
        if status and status != "all":
            items = [r for r in items if r.get("status") == status]
        return items[:limit]

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._runs.get(run_id)
        if state is None:
            persisted = self._load_persisted_state(run_id)
            if persisted is None:
                raise ValueError(f"Unknown run_id: {run_id}")
            if persisted.get("status") == "running":
                pid = persisted.get("pid")
                if isinstance(pid, int) and _is_pid_alive(pid):
                    codex_run.terminate_process_tree(pid)
                persisted["status"] = "cancelled"
                persisted["ok"] = False
                persisted["error"] = "Cancelled by user."
                events = persisted.get("events")
                if isinstance(events, list):
                    next_seq = int(persisted.get("seq") or len(events)) + 1
                    events.append({"kind": "error", "message": "Cancelled by user.", "seq": next_seq})
                    persisted["seq"] = next_seq
                codex_catalog.atomic_write_text(
                    self._state_path(run_id),
                    json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
                )
            persisted.pop("events", None)
            persisted.pop("seq", None)
            return persisted

        with state.lock:
            proc = state.process
            if state.status != "running":
                return state.to_summary_dict()
            state.status = "cancelled"
            state.error = "Cancelled by user."
            self._append_event(state, {"kind": "error", "message": "Cancelled by user."})
            self._persist_state(state)

        codex_run.terminate_process_tree(proc)
        self._notify_done(state)
        return state.to_summary_dict()


TOOLS_DEF: list[dict[str, Any]] = [
    {
        "name": "list_codex_models",
        "title": "List Codex Models & Presets",
        "description": (
            "Discover visible (visibility != 'hide') Codex models (from custom_models, "
            "model_catalog_json, models_cache.json, and config.toml), redacted providers, "
            "effective defaults, role presets, and excluded_models. Always call before guessing "
            "unfamiliar model slugs or efforts."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Optional substring filter on slug or display_name."},
                "provider": {"type": "string", "description": "Optional provider id filter."},
                "include_excluded": {"type": "boolean", "default": True, "description": "Include excluded_models entries."},
            },
        },
    },
    {
        "name": "start_codex_task",
        "title": "Start Codex Worker",
        "description": (
            "Start a bounded OpenAI Codex CLI worker asynchronously with whitelist config isolation "
            "and render a live collapsible Codex timeline inline. Supports model, provider, extended effort, "
            "role preset, and git worktree isolation. Returns run_id immediately."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "cwd"],
            "properties": {
                "prompt": {"type": "string", "description": "Self-contained bounded task contract for Codex."},
                "cwd": {"type": "string", "description": "Absolute working directory path."},
                "mode": {"type": "string", "enum": ["write", "read-only"], "description": "Sandbox policy."},
                "config_mode": {"type": "string", "enum": ["isolated", "profile", "inherit"], "description": "Config isolation mode."},
                "model": {"type": "string", "description": "Optional Codex model slug override."},
                "provider": {"type": "string", "description": "Optional model_provider override."},
                "effort": {"type": "string", "description": "Reasoning effort (none/minimal/low/medium/high/xhigh/max/ultra or catalog level)."},
                "preset": {"type": "string", "description": "Optional role preset (explore/coding/frontend/review/debug/fast or custom)."},
                "worktree": {"type": "string", "description": "Optional Git worktree name for isolated branch execution."},
                "persist": {"type": "boolean", "default": False, "description": "Persist session on disk for later resume."},
                "session_id": {"type": "string", "description": "Existing session_id to resume."},
                "search": {"type": "boolean", "default": False, "description": "Enable live web search."},
                "add_dir": {"type": "array", "items": {"type": "string"}, "description": "Additional writable directories."},
                "raw": {"type": "boolean", "default": False, "description": "Disable JSON output schema."},
                "timeout": {"type": "integer", "default": 600, "description": "Execution timeout in seconds."},
            },
        },
        "_meta": {"ui": {"resourceUri": APP_URI}},
    },
    {
        "name": "wait_codex_task",
        "title": "Wait for Codex Worker(s)",
        "description": (
            "Wait for one run_id or multiple run_ids to finish (wait_mode='all' or 'any' for rolling verification). "
            "Returns wait_timed_out=true while still running instead of treating wait timeout as failure."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "description": "Single run_id to wait on."},
                "run_ids": {"type": "array", "items": {"type": "string"}, "description": "Multiple run_ids for parallel rolling wait."},
                "wait_mode": {"type": "string", "enum": ["all", "any"], "default": "all"},
                "timeout": {"type": "integer", "default": 600, "description": "Max wait seconds."},
            },
        },
    },
    {
        "name": "get_codex_task_status",
        "title": "Get Codex Task Status",
        "description": "Non-blocking status check for one or more Codex runs (returns status, requested/effective/observed model, latest step, tokens, active slot count).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string"},
                "run_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "stop_codex_task",
        "title": "Stop Codex Task",
        "description": "Terminate a running Codex worker and its full child process tree.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}},
        },
    },
    {
        "name": "open_codex_panel",
        "title": "Agy Codex",
        "description": "Open the Agy Codex 3-tab workbench (Subagents Monitor, Usage Stats, Models & Config) in the side pane or sidebar.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tab": {"type": "string", "enum": ["subagents", "stats", "models"], "default": "subagents"},
                "run_id": {"type": "string", "description": "Optional run_id to focus initially."},
            },
        },
        "_meta": {
            "ui": {"resourceUri": APP_URI},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "Agy Codex"},
                    {"type": "global", "title": "Agy Codex"},
                ]
            },
        },
    },
    # --- App-only tools (zero model token overhead) ---
    {
        "name": "get_run_events",
        "title": "Get Run Events",
        "description": "App-only: fetch incremental timeline events for a Codex run.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}, "since_seq": {"type": "integer", "default": 0}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "list_runs",
        "title": "List Codex Runs",
        "description": "App-only: list recent and active Antigravity Codex runs.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "default": "all"},
                "limit": {"type": "integer", "default": 40},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "list_local_sessions",
        "title": "List Local Codex Sessions",
        "description": "App-only: list read-only local Codex CLI/Desktop sessions from state_*.sqlite.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                "include_subagents": {"type": "boolean", "default": True},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_local_session_detail",
        "title": "Get Local Codex Session Detail",
        "description": "App-only: fetch local Codex session metadata, context bar, spawn tree, and recent snippets.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thread_id"],
            "properties": {"thread_id": {"type": "string"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_stats",
        "title": "Get Unified Usage Stats",
        "description": "App-only: aggregate token, cache hit, duration, and breakdown stats across plugin runs & local Codex.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "scope": {"type": "string", "enum": ["all", "antigravity", "codex_local"], "default": "all"},
                "range": {"type": "string", "enum": ["today", "7d", "30d", "all"], "default": "7d"},
                "model": {"type": "string", "default": ""},
                "provider": {"type": "string", "default": ""},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_workbench_config",
        "title": "Get Workbench Config",
        "description": "App-only: fetch model discovery, workbench defaults, mapping policies, presets, and doctor summary.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"include_hidden": {"type": "boolean", "default": False}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "save_workbench_config",
        "title": "Save Workbench Config",
        "description": "App-only: persist defaults, mapping policies, custom_models, excluded_models, and presets.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "defaults": {"type": "object"},
                "mapping": {"type": "object"},
                "custom_models": {"type": "array"},
                "excluded_models": {"type": "array"},
                "presets": {"type": "object"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "preview_resolved_command",
        "title": "Preview Resolved Command",
        "description": "App-only: dry-run preview of redacted Codex CLI command and warnings.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "model": {"type": "string"},
                "provider": {"type": "string"},
                "effort": {"type": "string"},
                "preset": {"type": "string"},
                "config_mode": {"type": "string"},
                "mode": {"type": "string"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "run_doctor",
        "title": "Run Environment Doctor",
        "description": "App-only: run local Codex/SQLite/catalog/git health checks.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "cancel_run",
        "title": "Cancel Codex Run",
        "description": "App-only: terminate an active Codex run and its process tree.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
]


class McpServer:
    """Minimal JSON-RPC 2.0 stdio MCP server for Agy Codex."""

    def __init__(self, manager: RunManager) -> None:
        self.manager = manager

    def _collect_run_ids(self, arguments: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        raw_one = arguments.get("run_id")
        if isinstance(raw_one, str) and raw_one.strip():
            ids.append(raw_one.strip())
        raw_many = arguments.get("run_ids")
        if isinstance(raw_many, list):
            for item in raw_many:
                if isinstance(item, str) and item.strip() and item.strip() not in ids:
                    ids.append(item.strip())
        return ids

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if req_id is None:
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
                    "serverInfo": {"name": "agy-codex", "version": "1.0.0"},
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
                            "name": "Agy Codex Workbench",
                            "description": "Live subagent monitor, unified telemetry, and model config UI",
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
            return self._error(req_id, -32000, str(exc))

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        chome = codex_catalog.get_codex_home(self.manager.codex_home)
        ddir = self.manager.data_dir

        if name == "list_codex_models":
            payload = dict(
                codex_catalog.discover_models_and_config(
                    chome,
                    ddir,
                    query=str(arguments.get("query") or ""),
                    provider=str(arguments.get("provider") or ""),
                    include_hidden=False,
                    include_excluded=bool(arguments.get("include_excluded", True)),
                )
            )
            payload.pop("hidden_models_count", None)
            payload["all_models_count"] = payload["visible_models_count"]
            return self._tool_ok(payload)

        if name == "start_codex_task":
            state = self.manager.start_run(arguments)
            with state.lock:
                summary = state.to_summary_dict()
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            summary["active_runs"] = self.manager.active_count()
            summary["max_concurrent_runs"] = int(
                (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
            )
            summary["note"] = (
                "Codex worker started with live inline progress viewer. "
                "Call wait_codex_task or get_codex_task_status to collect results."
            )
            return self._tool_ok(summary)

        if name == "wait_codex_task":
            run_ids = self._collect_run_ids(arguments)
            wait_mode = str(arguments.get("wait_mode") or "all")
            timeout = int(arguments.get("timeout") or 600)
            payload = self.manager.wait_runs(run_ids, wait_mode=wait_mode, timeout=timeout)
            return self._tool_ok(payload)

        if name == "get_codex_task_status":
            run_ids = self._collect_run_ids(arguments)
            if not run_ids:
                all_runs = self.manager.list_runs(limit=10)
                run_ids = [r["run_id"] for r in all_runs if r.get("status") == "running"]
            payload = self.manager.get_status(run_ids)
            return self._tool_ok(payload)

        if name == "stop_codex_task":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            return self._tool_ok(self.manager.cancel_run(run_id))

        if name == "open_codex_panel":
            tab = str(arguments.get("tab") or "subagents").strip()
            if tab not in ("subagents", "stats", "models"):
                tab = "subagents"
            runs = self.manager.list_runs(limit=40)
            selected = arguments.get("run_id")
            if not selected and runs:
                selected = runs[0].get("run_id")
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            payload = {
                "view": "panel",
                "tab": tab,
                "runs": runs,
                "selected_run_id": selected,
                "active_runs": sum(1 for r in runs if r.get("status") == "running"),
                "max_concurrent_runs": int(
                    (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
                ),
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Opened Agy Codex workbench (tab={tab}, runs={len(runs)}).",
                    }
                ],
                "structuredContent": payload,
            }

        if name == "get_run_events":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            since_seq = int(arguments.get("since_seq") or 0)
            return self._tool_ok(self.manager.get_run_events(run_id, since_seq=since_seq))

        if name == "list_runs":
            status = str(arguments.get("status") or "all")
            limit = int(arguments.get("limit") or 40)
            runs = self.manager.list_runs(status=status, limit=limit)
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            return self._tool_ok(
                {
                    "runs": runs,
                    "active_runs": sum(1 for r in runs if r.get("status") == "running"),
                    "max_concurrent_runs": int(
                        (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
                    ),
                }
            )

        if name == "list_local_sessions":
            query = str(arguments.get("query") or "").strip().lower()
            limit = int(arguments.get("limit") or 50)
            include_sub = bool(arguments.get("include_subagents", True))
            plugin_runs = self.manager.list_runs(limit=200)
            plugin_sids = {
                str(r.get("session_id")).strip()
                for r in plugin_runs
                if isinstance(r.get("session_id"), str) and str(r.get("session_id")).strip()
            }
            res = codex_telemetry.read_local_codex_sessions(chome, ddir, limit=max(limit * 3, 200))
            sessions = [s for s in res["sessions"] if s["thread_id"] not in plugin_sids]
            if not include_sub:
                sessions = [s for s in sessions if not s.get("is_subagent")]
            if query:
                sessions = [
                    s
                    for s in sessions
                    if query in str(s.get("title") or "").lower()
                    or query in str(s.get("model") or "").lower()
                    or query in str(s.get("cwd") or "").lower()
                    or query in str(s.get("thread_id") or "").lower()
                ]
            return self._tool_ok(
                {
                    "sessions": sessions[:limit],
                    "total_available": len(sessions),
                    "db_path": res.get("db_path"),
                    "warnings": res.get("warnings") or [],
                }
            )

        if name == "get_local_session_detail":
            tid = str(arguments.get("thread_id") or "").strip()
            return self._tool_ok(codex_telemetry.get_local_session_detail(chome, ddir, tid))

        if name == "get_stats":
            scope = str(arguments.get("scope") or "all")
            trange = str(arguments.get("range") or "7d")
            model = str(arguments.get("model") or "")
            provider = str(arguments.get("provider") or "")
            runs = self.manager.list_runs(limit=1000)
            stats = codex_telemetry.compute_stats(
                runs,
                chome,
                ddir,
                scope=scope,
                time_range=trange,
                model=model,
                provider=provider,
            )
            return self._tool_ok(stats)

        if name == "get_workbench_config":
            inc_hidden = bool(arguments.get("include_hidden", False))
            disc = codex_catalog.discover_models_and_config(
                chome, ddir, include_hidden=inc_hidden, include_excluded=True
            )
            doc = codex_run.doctor_report(chome, ddir)
            return self._tool_ok({**disc, "doctor": doc})

        if name == "save_workbench_config":
            saved = codex_catalog.save_workbench_config(arguments, ddir)
            disc = codex_catalog.discover_models_and_config(chome, ddir, include_hidden=False)
            return self._tool_ok({"saved": saved, **disc})

        if name == "preview_resolved_command":
            preview = codex_run.preview_command_report(
                chome,
                ddir,
                model=arguments.get("model"),
                provider=arguments.get("provider"),
                effort=arguments.get("effort"),
                preset=arguments.get("preset"),
                config_mode=arguments.get("config_mode"),
                mode=arguments.get("mode"),
            )
            return self._tool_ok(preview)

        if name == "run_doctor":
            return self._tool_ok(codex_run.doctor_report(chome, ddir))

        if name == "cancel_run":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            return self._tool_ok(self.manager.cancel_run(run_id))

        raise ValueError(f"Unknown tool: {name}")

    def _tool_ok(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "structuredContent": payload,
        }

    def _error(self, req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Agy Codex stdio MCP App server.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Persistent directory for runs, workbench config, and telemetry cache.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = codex_catalog.get_plugin_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    manager = RunManager(data_dir=data_dir)
    server = McpServer(manager=manager)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = server._error(None, -32700, f"Parse error: {exc}")
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        if not isinstance(req, dict):
            continue
        resp = server.handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
