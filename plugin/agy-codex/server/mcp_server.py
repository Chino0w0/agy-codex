#!/usr/bin/env python3
"""Stdio MCP server & MCP App host for Agy Codex (`agy-codex`).

Exposes:
- Model-visible tools:
  `list_codex_models`, `start_codex_task`, `wait_codex_task`,
  `get_codex_task_status`, `stop_codex_task`, `close_codex_task`, `open_codex_panel`
- App-only tools (zero model token overhead):
  `get_run_events`, `list_runs`, `get_history`, `close_run`, `reopen_run`,
  `send_followup`, `toggle_codex_enabled`, `toggle_exclude_orchestrator`,
  `get_stats`, `get_workbench_config`, `save_workbench_config`,
  `preview_resolved_command`, `run_doctor`, `cancel_run`, `start_user_task`,
  `check_for_updates`, `start_plugin_update`, `get_update_status`
- UI resource: `ui://agy-codex/app.html` (`text/html;profile=mcp-app`)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
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
import codex_updater  # noqa: E402

APP_URI = "ui://agy-codex/app.html"


_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ERROR_LINE_RE = re.compile(
    r"\b(?:401|403|429)\b|unauthorized|rate limit|connection refused|timed out|"
    r"error:|fatal:|invalid|denied|not found|traceback", re.IGNORECASE,
)
_ISSUE_STATUSES = frozenset({"error", "blocked", "partial", "cancelled"})
_ERROR_STATUSES = _ISSUE_STATUSES - {"cancelled"}


def _run_health(status: str, last_activity_at: float, loop_reason: str | None,
                now: float) -> tuple[str, str | None, float | None]:
    if status != "running":
        return ("error" if status in _ERROR_STATUSES else "healthy", None, None)
    idle = max(0.0, now - last_activity_at)
    if idle >= 180:
        minutes, seconds = divmod(int(idle), 60)
        return "stalled", f"已静默 {minutes}m {seconds}s 无新输出", round(idle, 1)
    if loop_reason:
        return "loop_warning", loop_reason, round(idle, 1)
    return "healthy", None, round(idle, 1)


def _is_pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stdin=subprocess.DEVNULL,
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


def _fmt_ctx_window(ctx: Any) -> str:
    if isinstance(ctx, (int, float)) and not isinstance(ctx, bool) and ctx > 0:
        if ctx >= 1000:
            return f"{int(round(ctx / 1000))}k"
        return str(int(ctx))
    return ""


def format_models_brief(payload: dict[str, Any], *, active_runs: int = 0) -> str:
    """Format a compact ~25-line plain-text dispatch brief for Antigravity."""
    enabled = bool(payload.get("enabled", True))
    intercept = bool(payload.get("intercept_native_subagents", True))
    max_conc = int(payload.get("max_concurrent_runs", 5))
    eff = payload.get("effective_defaults") or {}
    eff_model = codex_catalog._clean_optional_str(eff.get("model"))
    eff_prov = codex_catalog._clean_optional_str(eff.get("provider")) or "auto"
    eff_effort = codex_catalog._clean_optional_str(eff.get("effort")) or "default"
    cfg_mode = codex_catalog._clean_optional_str(eff.get("config_mode")) or "isolated"

    excluded_set = {str(m).strip() for m in (payload.get("excluded_models") or []) if str(m).strip()}
    excl_orch = bool(payload.get("exclude_orchestrator_models", True))
    compliant_default: str | None = None
    if eff_model and eff_model not in excluded_set:
        if not (excl_orch and codex_catalog.is_orchestrator_model(eff_model)):
            compliant_default = eff_model

    lines = [
        "# Agy Codex Dispatch Brief",
        (
            f"Status: enabled={str(enabled).lower()} | intercept_native={str(intercept).lower()} "
            f"| slots={active_runs}/{max_conc} | worker_timeout=workbench (0=unlimited) | wait_timeout<=120s"
        ),
        (
            f"Default: {compliant_default or '(none - ask user to pick from Available Models below)'} "
            f"(provider={eff_prov}, effort={eff_effort}, config_mode={cfg_mode})"
        ),
        (
            'Quick call: start_codex_task(prompt=..., cwd=..., preset="coding|explore|frontend|review|debug|fast") '
            '-> open_codex_panel(tab="subagents", run_id=...) -> wait_codex_task(run_id=..., timeout=120) '
            "-> close_codex_task(run_id=...)"
        ),
        "",
        "Presets (omit model to inherit compliant default):",
    ]

    presets = payload.get("presets") or {}
    for pname, pobj in presets.items():
        if not isinstance(pobj, dict):
            continue
        pmode = pobj.get("mode") or "write"
        peff = pobj.get("effort") or "default"
        pmodel = codex_catalog._clean_optional_str(pobj.get("model"))
        plabel = codex_catalog._clean_optional_str(pobj.get("label")) or pname
        pwhen = codex_catalog._clean_optional_str(pobj.get("when_to_use")) or ""
        pmeta = f"{pmode}, effort={peff}" + (f", model={pmodel}" if pmodel else "")
        desc = f"{plabel} - {pwhen}" if pwhen else plabel
        lines.append(f"- {pname} [{pmeta}]: {desc}")

    all_models = [m for m in (payload.get("models") or []) if isinstance(m, dict)]
    available = [m for m in all_models if not m.get("excluded")]
    guarded = [m for m in all_models if m.get("excluded")]

    lines.append("")
    lines.append(f"Available Models ({len(available)}):")
    if not available:
        lines.append("- (none discovered - pass an explicit model slug or configure ~/.codex/config.toml)")
    else:
        for m in available:
            slug = str(m.get("slug") or "")
            efforts = m.get("supported_reasoning_levels") or []
            eff_str = ",".join(str(x) for x in efforts) if efforts else (
                codex_catalog._clean_optional_str(m.get("default_reasoning_level")) or "default"
            )
            tags = [f"efforts: {eff_str}"]
            ctx_str = _fmt_ctx_window(m.get("context_window"))
            if ctx_str:
                tags.append(f"ctx: {ctx_str}")
            if m.get("tool_mode") == "direct":
                tags.append("direct-only")
            if m.get("supports_search_tool") is False:
                tags.append("no-search")
            default_mark = " (default)" if compliant_default and slug == compliant_default else ""
            lines.append(f"- {slug} [{', '.join(tags)}]{default_mark}")

    if guarded:
        g_items = [
            f"{m.get('slug')} [{'orchestrator-guarded' if m.get('orchestrator_excluded') else 'excluded'}]"
            for m in guarded
        ]
        lines.append("")
        lines.append("Excluded / Guarded (do not select): " + ", ".join(g_items))

    warnings = [str(w).strip() for w in (payload.get("warnings") or []) if str(w).strip()]
    if warnings:
        lines.append("")
        lines.append("Warnings: " + " | ".join(warnings))

    return "\n".join(lines)


def format_start_brief(summary: dict[str, Any]) -> str:
    """Format a compact launch confirmation for start_codex_task."""
    run_id = summary.get("run_id") or ""
    title = summary.get("title") or run_id
    eff = summary.get("effective") or {}
    eff_model = eff.get("model") or summary.get("model") or "auto"
    eff_prov = eff.get("provider") or summary.get("provider") or "auto"
    eff_effort = eff.get("effort") or summary.get("effort") or "default"
    mode = summary.get("mode") or "write"
    timeout_sec = summary.get("timeout_sec")
    timeout_label = f"{timeout_sec}s" if timeout_sec else "unlimited"
    active = summary.get("active_runs", 1)
    max_conc = summary.get("max_concurrent_runs", 5)

    lines = [
        f"Started Codex task: {run_id} ({title})",
        (
            f"Config: model={eff_model} | provider={eff_prov} | effort={eff_effort} "
            f"| mode={mode} | timeout={timeout_label} | slots={active}/{max_conc}"
        ),
    ]
    wt = summary.get("worktree")
    if isinstance(wt, dict) and wt.get("branch"):
        lines.append(f"Worktree: {wt.get('branch')} ({wt.get('path') or ''})")
    warnings = [str(w).strip() for w in (summary.get("warnings") or []) if str(w).strip()]
    if warnings:
        lines.append("Warnings: " + "; ".join(warnings))
    lines.append(
        f'Next: call open_codex_panel(tab="subagents", run_id="{run_id}") once, '
        f'then wait_codex_task(run_id="{run_id}", timeout=120) or get_codex_task_status.'
    )
    return "\n".join(lines)


def _format_single_run_digest(r: dict[str, Any]) -> list[str]:
    rid = r.get("run_id") or ""
    status = r.get("status") or "unknown"
    title = r.get("title") or rid
    model = r.get("model") or "auto"
    effort = r.get("effort") or "default"
    elapsed = r.get("elapsed_sec")
    if elapsed is None:
        elapsed = r.get("duration_sec")
    elapsed_str = f"{elapsed}s" if elapsed is not None else "n/a"
    cmds = r.get("completed_commands")
    cmd_part = f" | cmds={cmds}" if isinstance(cmds, int) else ""
    tok = r.get("tokens_used")
    tok_str = str(tok) if tok is not None else "n/a"

    out = [f"- {rid} [{status}] {title} | model={model} ({effort}) | {elapsed_str}{cmd_part} | tokens={tok_str}"]

    health = r.get("health") or "healthy"
    if health != "healthy":
        reason = r.get("health_reason")
        out.append(f"  health: {health}" + (f" ({reason})" if reason else ""))
    if status == "running":
        for recent in (r.get("recent_errors") or [])[-2:]:
            out.append(f"  recent_error: {recent}")

    res = r.get("result")
    if isinstance(res, dict):
        summary_txt = codex_catalog._clean_optional_str(res.get("summary"))
        if summary_txt:
            out.append(f"  summary: {codex_run.truncate_text(summary_txt, 300)}")
        changed = [str(p) for p in (res.get("changed_files") or []) if str(p).strip()]
        if changed:
            shown = changed[:15]
            suffix = f" (+{len(changed) - 15} more)" if len(changed) > 15 else ""
            out.append("  changed_files: " + ", ".join(shown) + suffix)
        verifs = [v for v in (res.get("verification") or []) if isinstance(v, dict)]
        if verifs:
            passed_cnt = sum(1 for v in verifs if v.get("passed"))
            failed_cmds = [str(v.get("command") or "") for v in verifs if not v.get("passed") and v.get("command")]
            fail_note = f" (failed: {', '.join(failed_cmds[:3])})" if failed_cmds else ""
            out.append(f"  verification: {passed_cnt}/{len(verifs)} passed{fail_note}")
        risks = [str(rk).strip() for rk in (res.get("risks") or []) if str(rk).strip()]
        if risks:
            out.append("  risks: " + "; ".join(risks[:5]))
    elif isinstance(res, str) and res.strip():
        out.append(f"  output: {codex_run.truncate_text(res.strip(), 240)}")

    err = codex_catalog._clean_optional_str(r.get("error"))
    if err:
        out.append(f"  error: {err}")
        log_file = codex_catalog._clean_optional_str(r.get("log_file"))
        if log_file:
            out.append(f"  log_file: {log_file}")

    if status == "running":
        latest = r.get("latest_event")
        if isinstance(latest, dict):
            kind = latest.get("kind") or "event"
            detail = (
                latest.get("display_command")
                or latest.get("text")
                or latest.get("message")
                or latest.get("command")
                or ""
            )
            detail_str = codex_run.truncate_text(str(detail).strip(), 140) if detail else ""
            out.append(f"  latest_step: [{kind}] {detail_str}".rstrip())

    return out


def format_wait_or_status_brief(payload: dict[str, Any], *, is_wait: bool = False) -> str:
    """Format a compact human/LLM digest for wait_codex_task and get_codex_task_status."""
    if "runs" in payload and isinstance(payload["runs"], list):
        runs = payload["runs"]
    elif "run_id" in payload:
        runs = [payload]
    else:
        runs = []

    wait_timed_out = bool(payload.get("wait_timed_out"))
    lines: list[str] = []
    if is_wait:
        mode = payload.get("wait_mode") or "single"
        lines.append(f"Codex Wait Result (mode={mode}, wait_timed_out={str(wait_timed_out).lower()})")
        if payload.get("wait_timeout_clamped_sec"):
            lines.append("Note: wait timeout clamped to 120s to avoid MCP transport timeout.")
        if wait_timed_out:
            running_ids = [str(x) for x in (payload.get("still_running_ids") or [])]
            lines.append(
                "WAIT TIMED OUT (NOT A TASK FAILURE): still running "
                f"[{', '.join(running_ids)}]. Call wait_codex_task again (timeout<=120) instead of re-spawning."
            )
    else:
        active = payload.get("active_runs", 0)
        max_conc = payload.get("max_concurrent_runs", 5)
        lines.append(f"Codex Status (slots={active}/{max_conc}, tracked={len(runs)})")

    for r in runs:
        if isinstance(r, dict):
            lines.extend(_format_single_run_digest(r))

    return "\n".join(lines)


def format_stop_or_close_brief(payload: dict[str, Any], *, action: str) -> str:
    """Format a concise one-line confirmation for stop_codex_task / close_codex_task."""
    if action == "stop":
        rid = payload.get("run_id") or ""
        status = payload.get("status") or "cancelled"
        return f"Stopped Codex task {rid} (status={status})."
    closed_ids = [str(x) for x in (payload.get("closed_ids") or [])]
    skipped = [str(x) for x in (payload.get("skipped_running_ids") or [])]
    closed_str = ", ".join(closed_ids) if closed_ids else "none"
    msg = f"Closed Codex task(s): {closed_str}."
    if skipped:
        msg += f" Skipped still-running: {', '.join(skipped)}."
    return msg


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
    prompt_preview: str = ""
    persist: bool = False
    origin: str = "agent"
    raw: bool = False
    resume_session_id: str | None = None
    title: str = ""
    role: str = ""
    parent_session_id: str = "default"
    parent_session_title: str = ""
    closed: bool = False
    closed_at: float | None = None
    closed_by: str | None = None
    expired: bool = False
    worktree: dict[str, str] | None = None
    requested: dict[str, Any] = field(default_factory=dict)
    effective: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=lambda: {"model": None, "effort": None})
    warnings: list[str] = field(default_factory=list)
    status: str = "running"  # running | done | partial | blocked | error | cancelled
    pid: int | None = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    duration_sec: float | None = None
    timeout_sec: int = 0
    idle_timeout_sec: int = 0
    last_activity_at: float = field(init=False)
    in_flight_command: str | None = None
    recent_errors: list[str] = field(default_factory=list)
    _loop_reason: str | None = field(default=None, repr=False)
    _completed_ids: set[str] = field(default_factory=set, repr=False)
    _anonymous_commands: int = field(default=0, repr=False)
    _tool_ids: set[str] = field(default_factory=set, repr=False)
    _failure_command: str = field(default="", repr=False)
    _failure_fingerprint: str = field(default="", repr=False)
    _failure_count: int = field(default=0, repr=False)
    _fingerprint_count: int = field(default=0, repr=False)
    _tool_key: str = field(default="", repr=False)
    _tool_count: int = field(default=0, repr=False)
    worker: threading.Thread | None = field(default=None, repr=False)
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
    _snapshot_usage: dict[str, int] | None = field(default=None, repr=False)
    result: Any = None
    error: str | None = None
    log_file: str = ""
    output_file: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    last_agent_message: str = ""
    _last_state_write: float = field(default=0.0, repr=False)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.last_activity_at = self.started_at

    @property
    def completed_commands(self) -> int:
        return len(self._completed_ids) + self._anonymous_commands

    def record_command_completion(self, item_id: str) -> bool:
        if not item_id:
            self._anonymous_commands += 1
        elif item_id in self._completed_ids:
            return False
        else:
            self._completed_ids.add(item_id)
        return True

    def record_error(self, message: str) -> None:
        compact = codex_run.truncate_text(message, 300)
        if compact:
            if compact in self.recent_errors:
                self.recent_errors.remove(compact)
            self.recent_errors.append(compact)
            del self.recent_errors[:-5]

    def record_command_outcome(self, command: str, exit_code: Any, output: str) -> None:
        self._tool_key, self._tool_count = "", 0
        self._loop_reason = None
        normalized = " ".join(command.split())
        if exit_code in (0, None) or not normalized:
            self._failure_command, self._failure_fingerprint = "", ""
            self._failure_count = self._fingerprint_count = 0
            return
        fingerprint = hashlib.sha256(" ".join(output.split()).encode("utf-8")).hexdigest()
        same_command = normalized == self._failure_command
        self._failure_count = self._failure_count + 1 if same_command else 1
        self._fingerprint_count = (
            self._fingerprint_count + 1 if same_command and fingerprint == self._failure_fingerprint else 1
        )
        self._failure_command, self._failure_fingerprint = normalized, fingerprint
        label = codex_run.truncate_text(normalized, 80)
        excerpt = codex_run.truncate_text(output, 180)
        self.record_error(f"命令 {label!r} 退出码 {exit_code}" + (f": {excerpt}" if excerpt else ""))
        if self._fingerprint_count >= 4:
            self._loop_reason = f"检测到命令 {label!r} 连续 {self._fingerprint_count} 次相同报错循环"
        elif self._failure_count >= 4:
            self._loop_reason = f"检测到命令 {label!r} 连续 {self._failure_count} 次失败"

    def record_tool_call(self, item: dict[str, Any], label: str) -> None:
        item_id = str(item.get("id") or "")
        if item_id in self._tool_ids:
            return
        if item_id:
            self._tool_ids.add(item_id)
        args = item.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        key = json.dumps([label, args], sort_keys=True, ensure_ascii=False)
        self._tool_count = self._tool_count + 1 if key == self._tool_key else 1
        self._tool_key = key
        self._failure_command, self._failure_fingerprint = "", ""
        self._failure_count = self._fingerprint_count = 0
        self._loop_reason = (
            f"检测到工具 {label!r} 连续 {self._tool_count} 次使用相同参数调用"
            if self._tool_count >= 6 else None
        )

    def to_summary_dict(self) -> dict[str, Any]:
        with self.lock:
            return self._summary_locked()

    def _summary_locked(self) -> dict[str, Any]:
        now = time.time()
        health, health_reason, idle_sec = _run_health(self.status, self.last_activity_at, self._loop_reason, now)
        elapsed = (
            self.duration_sec
            if self.duration_sec is not None
            else round(max(0.0, now - self.started_at), 2)
        )
        remaining_timeout = (
            max(0, int(round(self.timeout_sec - elapsed)))
            if (self.status == "running" and self.timeout_sec > 0)
            else None
        )
        preview = self.prompt_preview or self.prompt[:180]
        digest_src = ""
        if isinstance(self.result, dict):
            digest_src = str(self.result.get("summary") or "")
        if not digest_src and self.error:
            digest_src = str(self.error)
        if not digest_src:
            digest_src = preview or self.prompt
        work_digest = codex_run.truncate_text(digest_src, 120)
        usage = self.usage if self.saw_usage else self._snapshot_usage

        return {
            "run_id": self.run_id,
            "source": "mcp",
            "pid": self.pid,
            "ok": self.status == "done" and (self.exit_code == 0 or self.exit_code is None),
            "status": self.status,
            "title": self.title or codex_run.derive_short_title(preview or self.prompt, 16),
            "role": self.role or ("只读探索" if self.mode == "read-only" else "代码实现"),
            "work_digest": work_digest,
            "completed_commands": self.completed_commands,
            "parent_session_id": self.parent_session_id or "default",
            "parent_session_title": self.parent_session_title or Path(self.cwd).name,
            "closed": self.closed,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "expired": self.expired,
            "cwd": self.cwd,
            "mode": self.mode,
            "config_mode": self.config_mode,
            "model": self.model,
            "provider": self.provider,
            "effort": self.effort,
            "preset": self.preset,
            "persist": self.persist,
            "origin": self.origin,
            "raw": self.raw,
            "requested": copy.deepcopy(self.requested),
            "effective": copy.deepcopy(self.effective),
            "observed": copy.deepcopy(self.observed),
            "warnings": copy.deepcopy(self.warnings),
            "prompt_preview": preview,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_activity_at": self.last_activity_at,
            "in_flight_command": self.in_flight_command,
            "recent_errors": list(self.recent_errors),
            "health": health,
            "health_reason": health_reason,
            "idle_sec": idle_sec,
            "finished_at": self.finished_at,
            "elapsed_sec": elapsed,
            "duration_sec": self.duration_sec,
            "timeout_sec": self.timeout_sec,
            "remaining_timeout_sec": remaining_timeout,
            "exit_code": self.exit_code,
            "session_id": self.session_id,
            "tokens_used": self.tokens_used,
            "usage": dict(usage) if usage is not None else None,
            "worktree": copy.deepcopy(self.worktree),
            "result": copy.deepcopy(self.result),
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
        self._summary_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._last_prune_ts: float = 0.0
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._prune_lock = threading.Lock()
        self._any_done_cond = threading.Condition(self._lock)
        self._shutting_down = False

    def _validate_run_id(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        try:
            root = self.runs_dir.resolve()
            candidate = (self.runs_dir / run_id).resolve()
            if candidate != root and candidate.is_relative_to(root):
                return run_id
        except (OSError, RuntimeError, ValueError):
            pass
        raise ValueError(f"Invalid run_id outside the runs directory: {run_id!r}")

    def _run_file(self, run_id: str, name: str) -> Path:
        directory = self.runs_dir / self._validate_run_id(run_id)
        target = directory / name
        try:
            if not target.is_symlink() and target.resolve().parent == directory.resolve():
                return target
        except (OSError, RuntimeError, ValueError):
            pass
        raise ValueError(f"Invalid run file for run_id: {run_id!r}")

    def _state_path(self, run_id: str) -> Path:
        return self._run_file(run_id, "state.json")

    def _events_path(self, run_id: str) -> Path:
        return self._run_file(run_id, "events.jsonl")

    def _prompt_path(self, run_id: str) -> Path:
        return self._run_file(run_id, "prompt.txt")

    def _persist_state(self, state: RunState, *, force: bool = False) -> None:
        now_mono = time.monotonic()
        if not force and (now_mono - state._last_state_write) < 0.25:
            return
        payload = state.to_summary_dict()
        payload["seq"] = state.seq
        target = self._state_path(state.run_id)
        codex_catalog.atomic_write_text(
            target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        state._last_state_write = time.monotonic()
        with self._cache_lock:
            self._summary_cache.pop(state.run_id, None)

    def _load_persisted_state(self, run_id: str) -> dict[str, Any] | None:
        path = self._state_path(run_id)
        try:
            st = path.stat()
        except OSError:
            with self._cache_lock:
                self._summary_cache.pop(run_id, None)
            return None

        with self._cache_lock:
            cached = self._summary_cache.get(run_id)
            if cached is not None and cached[0] == st.st_mtime_ns:
                return copy.deepcopy(cached[1])

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        summary = dict(data)
        summary["run_id"] = run_id
        summary.pop("events", None)
        summary.setdefault("expired", False)
        summary.setdefault("origin", "agent")
        summary.setdefault("raw", False)
        summary.setdefault("closed", False)
        summary.setdefault("closed_at", None)
        summary.setdefault("closed_by", None)
        with self._cache_lock:
            self._summary_cache[run_id] = (st.st_mtime_ns, summary)
        return copy.deepcopy(summary)

    def _maybe_prune_expired(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_prune_ts) < 900.0:
            return
        with self._prune_lock:
            now = time.time()
            if not force and (now - self._last_prune_ts) < 900.0:
                return
            self._last_prune_ts = now
            try:
                wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
                retention_hours = int((wb_cfg.get("mapping") or {}).get("retention_hours", 24))
                with self._lock:
                    active_ids = {rid for rid, st in self._runs.items() if st.status == "running"}
                res = codex_telemetry.prune_expired_runs(
                    self.runs_dir,
                    self.data_dir,
                    retention_hours=retention_hours,
                    active_run_ids=active_ids,
                )
                if res.get("pruned"):
                    with self._lock:
                        gone = [
                            rid
                            for rid, st in self._runs.items()
                            if st.status != "running" and not (self.runs_dir / rid).exists()
                        ]
                        for rid in gone:
                            self._runs.pop(rid, None)
                    if gone:
                        with self._cache_lock:
                            for rid in gone:
                                self._summary_cache.pop(rid, None)
            except Exception:
                pass

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._runs.values() if s.status == "running")

    def infer_default_cwd(self, parent_session_id: str | None = None) -> str:
        worktrees_root = (self.data_dir / "worktrees").resolve()

        def _pick_from(runs: list[dict[str, Any]]) -> str | None:
            for item in runs:
                if item.get("expired"):
                    continue
                wt = item.get("worktree")
                candidates: list[Any] = []
                if isinstance(wt, dict) and wt.get("base_repo"):
                    candidates.append(wt.get("base_repo"))
                candidates.append(item.get("cwd"))
                for raw_cwd in candidates:
                    if not isinstance(raw_cwd, str) or not raw_cwd.strip():
                        continue
                    try:
                        p = Path(raw_cwd).resolve()
                        if p.is_dir() and not p.is_relative_to(worktrees_root):
                            return str(p)
                    except (OSError, RuntimeError):
                        continue
            return None

        if parent_session_id:
            scoped = _pick_from(
                self.list_runs(limit=10, include_closed=True, parent_session_id=parent_session_id)
            )
            if scoped:
                return scoped
        any_recent = _pick_from(self.list_runs(limit=10, include_closed=True))
        if any_recent:
            return any_recent
        try:
            cwd_path = Path.cwd()
            if cwd_path.is_dir() and not cwd_path.resolve().is_relative_to(PLUGIN_ROOT):
                return str(cwd_path)
        except (OSError, RuntimeError):
            pass
        return str(Path.home())

    def start_run(self, params: dict[str, Any], *, bypass_enabled_check: bool = False) -> RunState:
        with self._lock:
            if self._shutting_down:
                raise ValueError("The run manager is shutting down.")
        self._maybe_prune_expired()
        wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
        if not bypass_enabled_check and not wb_cfg.get("enabled", True):
            raise ValueError(
                "Codex subagent dispatch is paused in Agy Codex settings (enabled=false). "
                "Delegate this subtask using Antigravity native subagents, or re-enable Codex subagents in Agy Codex."
            )

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
        origin = "user" if params.get("origin") == "user" else "agent"
        raw_add_dir = params.get("add_dir") or []
        add_dir = [str(d) for d in raw_add_dir if isinstance(d, str) and d.strip()]
        mapping = wb_cfg.get("mapping") or {}
        timeout = params.get("timeout", mapping.get("worker_timeout_sec", 0))
        if type(timeout) is not int or timeout < 0:
            raise ValueError("'timeout' must be a non-negative integer.")
        idle_timeout = mapping.get("idle_timeout_sec", 0)

        def _clean_str_list(raw_val: Any) -> list[str]:
            if not isinstance(raw_val, list):
                return []
            return [str(item).strip() for item in raw_val if isinstance(item, str) and item.strip()]

        context_files = _clean_str_list(params.get("context_files"))
        acceptance_criteria = _clean_str_list(params.get("acceptance_criteria"))
        non_goals = _clean_str_list(params.get("non_goals"))

        sections: list[str] = []
        if context_files:
            sections.append(
                "[READ-FIRST CONTEXT FILES]\n" + "\n".join(f"- {f}" for f in context_files)
            )
        if acceptance_criteria:
            sections.append(
                "[ACCEPTANCE CRITERIA]\n" + "\n".join(f"- {c}" for c in acceptance_criteria)
            )
        if non_goals:
            sections.append(
                "[NON-GOALS & BOUNDARIES]\n" + "\n".join(f"- {g}" for g in non_goals)
            )
        if sections:
            sections.append(f"[TASK]\n{prompt}")
            effective_prompt = "\n\n".join(sections)
        else:
            effective_prompt = prompt

        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run_dir = self.runs_dir / self._validate_run_id(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        log_file = self._run_file(run_id, "codex.log")
        output_file = self._run_file(run_id, "output.txt" if raw else "output.json")
        codex_catalog.atomic_write_text(self._prompt_path(run_id), effective_prompt)

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
            allow_thread_source=(resume_sid_str is None),
            allow_schema=not raw,
            allow_extra_dirs=(resume_sid_str is None),
            allow_search=(resume_sid_str is None),
            allow_json=True,
            check_git=True,
            quiet=True,
        )
        if resume_sid_str:
            cmd.extend(["resume", resume_sid_str, "-"])
        else:
            cmd.append("-")

        eff = meta["effective"]
        warnings = list(meta.get("warnings") or [])
        if origin == "agent" and eff.get("mode") == "write" and len(prompt) < 80 and not context_files:
            warnings.append(
                "Write task prompt is short (< 80 chars) without context_files; consider providing reference files and acceptance_criteria to avoid guesswork."
            )
        max_conc = int((wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5))
        cur_active = self.active_count()
        if cur_active >= max_conc:
            warnings.append(
                f"Active runs ({cur_active}) reached max_concurrent_runs ({max_conc}); consider rolling wait/cleanup."
            )

        preset_label = None
        if eff.get("preset"):
            p_obj = (wb_cfg.get("presets") or {}).get(eff["preset"])
            if isinstance(p_obj, dict):
                preset_label = codex_catalog._clean_optional_str(p_obj.get("label"))
        title_str = codex_catalog._clean_optional_str(params.get("title")) or codex_run.derive_short_title(
            prompt, 16
        )
        role_str = (
            codex_catalog._clean_optional_str(params.get("role"))
            or preset_label
            or ("只读探索" if eff.get("mode") == "read-only" else "代码实现")
        )
        parent_session_id = (
            codex_catalog._clean_optional_str(params.get("conversation_id"))
            or codex_catalog._clean_optional_str(os.environ.get("ANTIGRAVITY_CONVERSATION_ID"))
            or f"ws:{initial_cwd.name}"
        )
        parent_session_title = (
            codex_catalog._clean_optional_str(params.get("conversation_title")) or initial_cwd.name
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
            prompt=effective_prompt,
            prompt_preview=prompt[:180],
            persist=persist,
            origin=origin,
            raw=raw,
            resume_session_id=resume_sid_str,
            title=title_str,
            role=role_str,
            parent_session_id=parent_session_id,
            parent_session_title=parent_session_title,
            worktree=worktree_info,
            requested=meta.get("requested") or {},
            effective=eff,
            warnings=warnings,
            timeout_sec=timeout,
            idle_timeout_sec=idle_timeout,
            log_file=str(log_file),
            output_file=str(output_file),
        )
        state.worker = threading.Thread(
            target=self._execute_worker,
            args=(state, cmd, log_file, output_file, timeout, effective_prompt),
            daemon=True,
        )
        with self._lock:
            if self._shutting_down:
                raise ValueError("The run manager is shutting down.")
            self._persist_state(state, force=True)
            self._runs[run_id] = state
            state.worker.start()
        return state

    def _append_event(self, state: RunState, event_payload: dict[str, Any]) -> None:
        state.seq += 1
        state.updated_at = time.time()
        event_payload["seq"] = state.seq
        event_payload.setdefault("ts", round(state.updated_at - state.started_at, 2))
        state.events.append(event_payload)
        ev_path = self._events_path(state.run_id)
        try:
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            with ev_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(event_payload, ensure_ascii=False) + "\n")
        except OSError:
            pass

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
        stdin_prompt: str | None = None,
    ) -> None:
        proc: subprocess.Popen[str] | None = None
        timer: threading.Timer | None = None
        finishing = timed_out = False
        idle_timeout_error: str | None = None
        idle_timeout = state.idle_timeout_sec
        stop_readers = threading.Event()
        active_pipes = set()
        lines: queue.Queue[tuple[bool, str | None]] = queue.Queue(maxsize=256)
        stderr_tail: deque[str] = deque(maxlen=12)
        log_head: list[str] = []
        log_tail: deque[str] = deque(maxlen=128)
        parsed_result = None
        fallback: dict[str, Any] = {}

        def enqueue(stderr: bool, line: str | None) -> None:
            while not stop_readers.is_set():
                try:
                    lines.put((stderr, line), timeout=0.1)
                    return
                except queue.Full:
                    continue

        def drain(pipe: Any, stderr: bool) -> None:
            try:
                with pipe:
                    for raw_line in pipe:
                        if stop_readers.is_set():
                            break
                        with state.lock:
                            state.last_activity_at = time.time()
                        enqueue(stderr, raw_line)
            except (OSError, ValueError):
                pass
            finally:
                enqueue(stderr, None)

        def on_timeout(*, idle: bool = False) -> None:
            nonlocal timed_out, idle_timeout_error
            with state.lock:
                if (proc is None or finishing or timed_out or state.status != "running"
                        or proc.poll() is not None):
                    return
                if idle and time.time() - state.last_activity_at < idle_timeout:
                    return
                timed_out = True
                if idle:
                    idle_timeout_error = f"连续无输出卡死超时 ({idle_timeout}s)"
                    state.error = idle_timeout_error
            codex_run.terminate_process_tree(proc)

        try:
            with state.lock:
                if state.status == "cancelled":
                    return
            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE if stdin_prompt is not None else subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
                "env": codex_run.build_exec_env(),
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **popen_kwargs)
            with state.lock:
                if state.status == "cancelled":
                    return  # finally 负责回收 Popen 交接期间被取消的进程。
                state.process = proc
                state.pid = proc.pid
                state.updated_at = time.time()
                self._persist_state(state, force=True)

            if timeout > 0:
                timer = threading.Timer(timeout, on_timeout)
                timer.daemon = True
                timer.start()
            for stderr, pipe in ((False, proc.stdout), (True, proc.stderr)):
                threading.Thread(target=drain, args=(pipe, stderr), daemon=True).start()
                active_pipes.add(pipe)
            if stdin_prompt is not None:
                threading.Thread(target=codex_run.write_prompt_to_stdin,
                                 args=(proc, stdin_prompt), daemon=True).start()
                active_pipes.add(proc.stdin)

            open_pipes = 2
            abort_at: float | None = None
            piped_exit_at: float | None = None
            log_file = self._run_file(state.run_id, log_file.name)
            with log_file.open("w", encoding="utf-8", newline='\n', buffering=1) as log:
                while open_pipes or proc.poll() is None:
                    now = time.monotonic()
                    with state.lock:
                        aborting = timed_out or state.status == "cancelled"
                    if aborting:
                        abort_at = abort_at or now
                        if now - abort_at >= 1:
                            break
                    try:
                        stderr, raw_line = lines.get(timeout=0.1)
                    except queue.Empty:
                        if proc.poll() is not None:
                            piped_exit_at = piped_exit_at or now
                            if now - piped_exit_at >= 1:
                                break  # 子进程已退出，不无限等待继承管道的后代。
                        elif idle_timeout:
                            on_timeout(idle=True)
                        continue
                    piped_exit_at = None
                    if raw_line is None:
                        open_pipes -= 1
                        continue
                    log.write(raw_line)
                    if len(log_head) < 128:
                        log_head.append(raw_line[:8192])
                    else:
                        log_tail.append(raw_line[:8192])
                    self._handle_worker_line(state, raw_line, stderr, stderr_tail)
                with state.lock:
                    finishing = True

            output_file = self._run_file(state.run_id, output_file.name)
            raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
            if state.origin == "user" and state.raw:
                parsed_result = raw_output or (state.last_agent_message.strip() if not timed_out else "") or None
            else:
                parsed_result = safe_parse_handoff(raw_output) if raw_output else None
                if parsed_result is None and state.last_agent_message:
                    candidate = safe_parse_handoff(state.last_agent_message.strip())
                    if not timed_out or (isinstance(candidate, dict) and "status" in candidate):
                        parsed_result = candidate
            fallback = codex_run.parse_telemetry("".join([*log_head, *log_tail]))
        except Exception as exc:
            with state.lock:
                if state.status == "running" and not timed_out:
                    message = f"Failed to launch codex CLI: {exc}" if proc is None else str(exc)
                    state.status = "error"
                    state.error = message
                    state.record_error(message)
        finally:
            try:
                with state.lock:
                    finishing = True
                if timer is not None:
                    timer.cancel()
                stop_readers.set()
                if proc is not None:
                    if proc.poll() is None or (os.name != "nt" and any(not p.closed for p in active_pipes)):
                        codex_run.terminate_process_tree(proc, force=True)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe is not None and pipe not in active_pipes:
                            pipe.close()
            finally:
                try:
                    self._finish_worker(
                        state, proc, timeout, timed_out, idle_timeout_error, parsed_result, fallback, stderr_tail
                    )
                finally:
                    self._notify_done(state)

    def _handle_worker_line(
        self, state: RunState, raw_line: str, stderr: bool, stderr_tail: deque[str]
    ) -> None:
        stripped = raw_line.strip()
        if stripped.startswith("{"):
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                event = None
            if isinstance(event, dict):
                self._handle_json_event(state, event)
                return
        with state.lock:
            if state.status != "running":
                return
            if stderr and stripped:
                stderr_tail.append(codex_run.truncate_text(stripped, 300))
                if _ERROR_LINE_RE.search(stripped):
                    state.record_error(stripped)
                    self._append_event(state, {"kind": "log", "text": stripped})
            elif "ERROR" in stripped or "fatal" in stripped.lower() or "warning:" in stripped.lower():
                self._append_event(state, {"kind": "log", "text": stripped})
            self._persist_state(state)

    def _finish_worker(
        self, state: RunState, proc: subprocess.Popen[str] | None, timeout: int, timed_out: bool,
        idle_timeout_error: str | None, parsed_result: Any, fallback: dict[str, Any],
        stderr_tail: deque[str],
    ) -> None:
        with state.lock:
            state.process = None
            state.pid = None
            state.in_flight_command = None
            state.finished_at = state.finished_at or time.time()
            state.updated_at = state.finished_at
            state.duration_sec = round(max(0.0, state.finished_at - state.started_at), 2)
            state.exit_code = 124 if timed_out else (proc.returncode if proc and proc.returncode is not None else 1)
            state.result = parsed_result
            for key in ("model", "effort"):
                if fallback.get(key) and not state.observed.get(key):
                    state.observed[key] = fallback[key]
            if state.observed.get("model"):
                state.model = state.observed["model"]
            if not state.session_id and fallback.get("session_id"):
                state.session_id = fallback["session_id"]
            if not state.saw_usage and state._snapshot_usage:
                state.usage = dict(state._snapshot_usage)
                state.saw_usage = True
            if state.saw_usage:
                state.tokens_used = state.usage["input_tokens"] + state.usage["output_tokens"]
            elif fallback.get("tokens_used"):
                state.tokens_used = fallback["tokens_used"]

            if state.status == "running":
                handoff = parsed_result if isinstance(parsed_result, dict) else {}
                status = handoff.get("status")
                summary = codex_catalog._clean_optional_str(handoff.get("summary"))
                if timed_out:
                    state.status = "error"
                    if idle_timeout_error:
                        state.error = idle_timeout_error
                    else:
                        count = state.completed_commands
                        state.error = (
                            f"执行超时 ({timeout}s)，中止前已完成 {count} 条命令"
                            "（完整过程日志已保存在 codex.log，可展开步骤查看）"
                            if count else f"Timed out after {timeout}s"
                        )
                elif state.exit_code != 0:
                    state.status = "error"
                    state.error = state.error or (state.recent_errors[-1] if state.recent_errors else None) or (
                        stderr_tail[-1] if stderr_tail else f"codex exited with code {state.exit_code}"
                    )
                elif status in ("blocked", "partial", "failed"):
                    state.status = "error" if status == "failed" else status
                    state.error = state.error or summary or f"Worker reported status={status}."
                else:
                    state.status = "done"
            try:
                if state.status == "error" and state.error:
                    self._append_event(state, {"kind": "error", "message": state.error})
            finally:
                try:
                    self._persist_state(state, force=True)
                except (OSError, ValueError):
                    pass
            summary = state.to_summary_dict()
        try:
            codex_telemetry.append_ledger_record(self.data_dir, summary)
        except (OSError, UnicodeError):
            pass

    def _handle_json_event(self, state: RunState, evt: dict[str, Any]) -> None:
        etype = evt.get("type")
        force_write = False
        with state.lock:
            if state.status != "running":
                return
            state.last_activity_at = state.updated_at = time.time()
            if isinstance(evt.get("model"), str) and evt["model"].strip():
                state.observed["model"] = evt["model"].strip()
            if isinstance(evt.get("reasoning_effort"), str) and evt["reasoning_effort"].strip():
                state.observed["effort"] = evt["reasoning_effort"].strip()
            if etype == "thread.started":
                tid = evt.get("thread_id")
                if isinstance(tid, str) and tid:
                    state.session_id = tid
                    self._append_event(state, {"kind": "session", "session_id": tid})
                    force_write = True
            elif etype in ("item.started", "item.completed"):
                item = evt.get("item")
                if not isinstance(item, dict):
                    return
                item_id = str(item.get("id") or "")
                itype = str(item.get("type") or "")
                if itype == "command_execution":
                    raw_cmd = str(item.get("command") or "")
                    display_command = codex_run.strip_shell_wrapper(raw_cmd)
                    output = str(item.get("aggregated_output") or "")[:12000]
                    if etype == "item.started":
                        state.in_flight_command = display_command
                    else:
                        state.in_flight_command = None
                        if state.record_command_completion(item_id):
                            state.record_command_outcome(display_command, item.get("exit_code"), output)
                    self._append_event(
                        state,
                        {
                            "kind": "command",
                            "item_id": item_id,
                            "command": raw_cmd,
                            "display_command": display_command,
                            "status": str(item.get("status") or ""),
                            "exit_code": item.get("exit_code"),
                            "output": output,
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
                elif itype == "mcp_tool_call":
                    mcp_ev, obs_m, obs_e = codex_run.extract_mcp_tool_call_event(item)
                    if "arguments" in item or etype == "item.completed":
                        state.record_tool_call(item, mcp_ev["display_command"])
                    if etype == "item.completed" and state.record_command_completion(item_id):
                        if mcp_ev["exit_code"] not in (None, 0):
                            state.record_error(f"{mcp_ev['display_command']}: {mcp_ev['output'] or '工具调用失败'}")
                    if obs_m:
                        state.observed["model"] = obs_m
                    if obs_e:
                        state.observed["effort"] = obs_e
                    self._append_event(state, mcp_ev)
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
                    or (err_obj.get("message") if isinstance(err_obj, dict) else err_obj)
                    or json.dumps(evt, ensure_ascii=False)
                )
                state.error = str(msg)
                state.record_error(state.error)
                self._append_event(state, {"kind": "error", "message": state.error})
                force_write = True

            if etype != "turn.completed" and not state.saw_usage:
                snap_u = evt.get("usage")
                if not isinstance(snap_u, dict):
                    payload_obj = evt.get("payload")
                    info_obj = payload_obj.get("info") if isinstance(payload_obj, dict) else None
                    if isinstance(info_obj, dict):
                        snap_u = info_obj.get("total_token_usage") or info_obj.get(
                            "last_token_usage"
                        )
                if isinstance(snap_u, dict):
                    in_t = int(snap_u.get("input_tokens") or 0)
                    c_t = int(snap_u.get("cached_input_tokens") or 0)
                    out_t = int(snap_u.get("output_tokens") or 0)
                    r_t = int(snap_u.get("reasoning_output_tokens") or 0)
                    tot_t = int(snap_u.get("total_tokens") or (in_t + out_t))
                    if in_t or c_t or out_t or r_t or tot_t:
                        state._snapshot_usage = {
                            "input_tokens": in_t,
                            "cached_input_tokens": c_t,
                            "output_tokens": out_t,
                            "reasoning_output_tokens": r_t,
                        }
                        state.tokens_used = (in_t + out_t) if (in_t or out_t) else tot_t

            self._persist_state(state, force=force_write)

    def _normalize_persisted_state(
        self, data: dict[str, Any], run_id: str | None = None
    ) -> dict[str, Any]:
        if data.get("status") == "running":
            raw_pid = data.get("pid")
            pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
            rid = str(run_id or data.get("run_id") or "").strip()
            now = time.time()
            recently_active = False
            if rid:
                cutoff = now - 45.0
                for p in (self._events_path(rid), self._state_path(rid)):
                    try:
                        if p.is_file() and p.stat().st_mtime >= cutoff:
                            recently_active = True
                            break
                    except OSError:
                        pass
            if _is_pid_alive(pid) or recently_active:
                started_at = data.get("started_at")
                elapsed = data.get("elapsed_sec")
                if isinstance(started_at, (int, float)):
                    elapsed = round(max(0.0, now - float(started_at)), 2)
                    data["elapsed_sec"] = elapsed
                raw_timeout = data.get("timeout_sec")
                if (
                    isinstance(raw_timeout, (int, float))
                    and not isinstance(raw_timeout, bool)
                    and raw_timeout > 0
                    and isinstance(elapsed, (int, float))
                ):
                    data["remaining_timeout_sec"] = max(
                        0, int(round(float(raw_timeout) - float(elapsed)))
                    )
                else:
                    data["remaining_timeout_sec"] = None
            else:
                data["status"] = "error"
                data["ok"] = False
                data["pid"] = None
                data["remaining_timeout_sec"] = None
                data["error"] = "External CLI process exited unexpectedly."
        now = time.time()
        last_activity = data.get("last_activity_at") or data.get("updated_at") or data.get("started_at")
        if not isinstance(last_activity, (int, float)):
            last_activity = now
        loop_reason = data.get("health_reason") if data.get("health") == "loop_warning" else None
        health, reason, idle = _run_health(data.get("status", ""), last_activity, loop_reason, now)
        data.update(last_activity_at=last_activity, health=health, health_reason=reason, idle_sec=idle)
        data.setdefault("in_flight_command", None)
        data.setdefault("recent_errors", [])
        if data.get("status") != "running":
            data["in_flight_command"] = None
        return data

    def _get_summary(self, run_id: str) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
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
        return self._normalize_persisted_state(persisted, run_id)

    def wait_runs(
        self,
        run_ids: list[str],
        *,
        wait_mode: str = "all",
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Wait for one or multiple runs (`all` or `any`), treating wait timeout as non-fatal."""
        if not run_ids:
            raise ValueError("Provide 'run_id' or non-empty 'run_ids'.")
        if wait_mode not in ("all", "any"):
            raise ValueError("'wait_mode' must be 'all' or 'any'.")

        deadline = None if timeout <= 0 else (time.monotonic() + timeout)
        while True:
            summaries = [self._get_summary(rid) for rid in run_ids]
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
                wait_slice = min(rem, 0.5)
            else:
                wait_slice = 0.5

            with self._any_done_cond:
                in_mem_done = False
                for rid in still_running_ids:
                    st = self._runs.get(rid)
                    if st is not None and st.done_event.is_set():
                        in_mem_done = True
                        break
                if not in_mem_done:
                    self._any_done_cond.wait(timeout=wait_slice)

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

    def _read_persisted_events(self, run_id: str) -> list[dict[str, Any]]:
        events_path = self._events_path(run_id)
        all_events: list[dict[str, Any]] = []
        if events_path.is_file():
            try:
                for raw_line in events_path.read_text(encoding="utf-8").split('\n'):
                    s = raw_line.strip()
                    if not s:
                        continue
                    try:
                        ev = json.loads(s)
                        if isinstance(ev, dict):
                            all_events.append(ev)
                    except json.JSONDecodeError:
                        continue
                return all_events
            except OSError:
                pass
        # Legacy v1.0 fallback: embedded "events" array in state.json
        state_path = self._state_path(run_id)
        if state_path.is_file():
            try:
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(raw_state, dict) and isinstance(raw_state.get("events"), list):
                    return [ev for ev in raw_state["events"] if isinstance(ev, dict)]
            except Exception:
                pass
        return []

    def get_run_events(self, run_id: str, since_seq: int = 0) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
        with self._lock:
            state = self._runs.get(run_id)
        if state is not None:
            with state.lock:
                summary = state.to_summary_dict()
                summary["prompt"] = state.prompt
                events = [ev for ev in state.events if int(ev.get("seq", 0)) > since_seq]
                latest_seq = state.seq
            summary["events"] = events
            summary["latest_seq"] = latest_seq
            return summary

        persisted = self._load_persisted_state(run_id)
        if persisted is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        persisted = self._normalize_persisted_state(persisted, run_id)
        prompt_path = self._prompt_path(run_id)
        full_prompt: str | None = None
        if prompt_path.is_file():
            try:
                full_prompt = prompt_path.read_text(encoding="utf-8")
            except OSError:
                full_prompt = None
        persisted["prompt"] = (
            full_prompt if full_prompt is not None else persisted.get("prompt_preview")
        )
        all_events = self._read_persisted_events(run_id)
        max_event_seq = max((int(ev.get("seq", i)) for i, ev in enumerate(all_events, 1)), default=0)
        latest_seq = max(int(persisted.pop("seq", 0) or 0), max_event_seq)
        persisted["events"] = [ev for ev in all_events if int(ev.get("seq", 0)) > since_seq]
        persisted["latest_seq"] = latest_seq
        return persisted

    def list_runs(
        self,
        *,
        status: str = "all",
        limit: int = 40,
        include_closed: bool = False,
        parent_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._maybe_prune_expired()
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
                try:
                    persisted = self._load_persisted_state(child.name)
                except ValueError:
                    continue
                if persisted is not None:
                    persisted = self._normalize_persisted_state(persisted, child.name)
                    persisted.pop("events", None)
                    persisted.pop("seq", None)
                    persisted["expired"] = False
                    seen[child.name] = persisted

        items = list(seen.values())
        if not include_closed:
            items = [r for r in items if not r.get("closed")]
        scope_sid = codex_catalog._clean_optional_str(parent_session_id)
        if scope_sid:
            items = [r for r in items if str(r.get("parent_session_id") or "") == scope_sid]
        if status and status != "all":
            items = [r for r in items if r.get("status") == status]
        if include_closed:
            items.sort(key=lambda r: -float(r.get("started_at") or 0))
        else:
            items.sort(
                key=lambda r: (
                    0 if r.get("status") == "running" else 1,
                    -float(r.get("started_at") or 0),
                )
            )
        return items[:limit]

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run_id = self._validate_run_id(run_id)
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
                persisted["pid"] = None
                persisted["remaining_timeout_sec"] = None
                persisted["error"] = "Cancelled by user."
                persisted.update(health="healthy", health_reason=None, idle_sec=None, in_flight_command=None)
                now_ts = time.time()
                persisted["finished_at"] = persisted.get("finished_at") or now_ts
                persisted["updated_at"] = now_ts
                next_seq = int(persisted.get("seq") or 0) + 1
                err_ev = {
                    "kind": "error",
                    "message": "Cancelled by user.",
                    "seq": next_seq,
                    "ts": round(
                        max(0.0, now_ts - float(persisted.get("started_at") or now_ts)), 2
                    ),
                }
                persisted["seq"] = next_seq
                ev_path = self._events_path(run_id)
                try:
                    ev_path.parent.mkdir(parents=True, exist_ok=True)
                    with ev_path.open("a", encoding="utf-8", newline="\n") as fh:
                        fh.write(json.dumps(err_ev, ensure_ascii=False) + "\n")
                except OSError:
                    pass
                codex_catalog.atomic_write_text(
                    self._state_path(run_id),
                    json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
                )
                with self._cache_lock:
                    self._summary_cache.pop(run_id, None)
                codex_telemetry.append_ledger_record(self.data_dir, persisted)
            persisted.pop("events", None)
            persisted.pop("seq", None)
            return persisted

        return self._cancel_live_run(state)

    def _cancel_live_run(self, state: RunState) -> dict[str, Any]:
        proc = None
        should_notify = False
        try:
            with state.lock:
                if state.status != "running":
                    return state.to_summary_dict()
                proc = state.process
                should_notify = True
                state.status = "cancelled"
                state.pid = None
                state.in_flight_command = None
                state.error = "Cancelled by user."
                state.finished_at = state.finished_at or time.time()
                state.duration_sec = round(max(0.0, state.finished_at - state.started_at), 2)
                self._append_event(state, {"kind": "error", "message": state.error})
                self._persist_state(state, force=True)
                summary = state.to_summary_dict()
        finally:
            if should_notify:
                self._notify_done(state)
                codex_run.terminate_process_tree(proc)
                if proc is not None and state.worker is not None:
                    state.worker.join(timeout=4.0)
        return summary  # Worker 的 finally 负责更新账本和回收进程句柄。

    def shutdown(self) -> None:
        """Stop new launches and terminate and reap active workers within a shared deadline."""
        with self._lock:
            self._shutting_down = True
            states = list(self._runs.values())

        def stop(state: RunState) -> None:
            try:
                self._cancel_live_run(state)
            except (OSError, ValueError):
                pass

        deadline = time.monotonic() + 8.0
        workers = [s.worker for s in states if s.worker is not None and s.worker.is_alive()]
        stoppers = [threading.Thread(target=stop, args=(s,), daemon=True) for s in states if s.status == "running"]
        for thread in stoppers:
            thread.start()
        for thread in [*stoppers, *workers]:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def close_runs(
        self,
        run_ids: list[str],
        *,
        closed_by: str,
        close_all_finished: bool = False,
        parent_session_id: str | None = None,
    ) -> dict[str, Any]:
        skipped_running_ids: list[str] = []
        targets: list[str] = []
        run_ids = [self._validate_run_id(rid) for rid in run_ids]

        if close_all_finished:
            unclosed = self.list_runs(
                status="all",
                limit=1000,
                include_closed=False,
                parent_session_id=parent_session_id,
            )
            for item in unclosed:
                rid = str(item.get("run_id") or "")
                if not rid:
                    continue
                if item.get("status") == "running":
                    skipped_running_ids.append(rid)
                else:
                    targets.append(rid)
        else:
            deduped = list(dict.fromkeys(run_ids))
            if not deduped:
                raise ValueError("Provide 'run_id', 'run_ids', or 'close_all_finished=true'.")
            for rid in deduped:
                summary = self._get_summary(rid)
                if summary.get("status") == "running":
                    skipped_running_ids.append(rid)
                else:
                    targets.append(rid)
            if not targets and skipped_running_ids:
                raise ValueError("Cannot close running task(s); call stop_codex_task first.")

        now = time.time()
        closed_ids: list[str] = []
        for rid in targets:
            with self._lock:
                state = self._runs.get(rid)
            if state is not None:
                with state.lock:
                    if state.status == "running":
                        skipped_running_ids.append(rid)
                        continue
                    state.closed = True
                    state.closed_at = now
                    state.closed_by = closed_by
                    self._persist_state(state, force=True)
                    summary = state.to_summary_dict()
            else:
                persisted = self._load_persisted_state(rid)
                if persisted is None:
                    continue
                persisted = self._normalize_persisted_state(persisted, rid)
                if persisted.get("status") == "running":
                    skipped_running_ids.append(rid)
                    continue
                persisted["closed"] = True
                persisted["closed_at"] = now
                persisted["closed_by"] = closed_by
                codex_catalog.atomic_write_text(
                    self._state_path(rid),
                    json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
                )
                with self._cache_lock:
                    self._summary_cache.pop(rid, None)
                summary = persisted

            codex_telemetry.append_ledger_record(self.data_dir, summary)
            closed_ids.append(rid)

        return {
            "closed_ids": closed_ids,
            "skipped_running_ids": skipped_running_ids,
            "closed_by": closed_by,
            "closed_at": now if closed_ids else None,
        }

    def reopen_run(self, run_id: str) -> dict[str, Any]:
        rid = self._validate_run_id(run_id)

        with self._lock:
            state = self._runs.get(rid)
        if state is not None:
            with state.lock:
                state.closed = False
                state.closed_at = None
                state.closed_by = None
                self._persist_state(state, force=True)
                summary = state.to_summary_dict()
            codex_telemetry.append_ledger_record(self.data_dir, summary)
            return summary

        persisted = self._load_persisted_state(rid)
        if persisted is None:
            ledger_ids = {
                rec.get("run_id") for rec in codex_telemetry.read_ledger_records(self.data_dir)
            }
            if rid in ledger_ids:
                raise ValueError(f"Run {rid!r} has expired and cannot be reopened.")
            raise ValueError(f"Unknown run_id: {rid}")

        persisted = self._normalize_persisted_state(persisted, rid)
        persisted["closed"] = False
        persisted["closed_at"] = None
        persisted["closed_by"] = None
        persisted["expired"] = False
        codex_catalog.atomic_write_text(
            self._state_path(rid),
            json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
        )
        with self._cache_lock:
            self._summary_cache.pop(rid, None)
        codex_telemetry.append_ledger_record(self.data_dir, persisted)
        persisted.pop("events", None)
        persisted.pop("seq", None)
        return persisted

    def send_followup(self, run_id: str, prompt: str) -> RunState:
        rid = self._validate_run_id(run_id)
        followup_prompt = str(prompt or "").strip()
        if not followup_prompt:
            raise ValueError("'prompt' is required.")

        summary = self._get_summary(rid)
        if summary.get("status") == "running":
            raise ValueError(f"Run {rid!r} is still running; wait for completion before follow-up.")
        session_id = str(summary.get("session_id") or "").strip()
        if not summary.get("persist") or not session_id:
            raise ValueError(
                f"Run {rid!r} cannot be resumed (requires persist=True and a valid session_id)."
            )

        orig_title = str(summary.get("title") or "").strip()
        if orig_title:
            next_title = (
                orig_title
                if "(续)" in orig_title
                else codex_run.truncate_text(f"{orig_title} (续)", 32)
            )
        else:
            next_title = codex_run.truncate_text(followup_prompt, 24)

        origin = summary.get("origin", "agent")
        return self.start_run(
            {
                "prompt": followup_prompt,
                "cwd": summary.get("cwd") or ".",
                "model": summary.get("model"),
                "provider": summary.get("provider"),
                "effort": summary.get("effort"),
                "preset": summary.get("preset"),
                "mode": summary.get("mode"),
                "config_mode": summary.get("config_mode"),
                "role": summary.get("role"),
                "title": next_title,
                "conversation_id": summary.get("parent_session_id"),
                "conversation_title": summary.get("parent_session_title"),
                "persist": True,
                "session_id": session_id,
                "origin": origin,
                "raw": summary.get("raw", False),
            },
            bypass_enabled_check=origin == "user",
        )

    def get_history(
        self,
        *,
        query: str = "",
        filter_mode: str = "all",
        limit: int = 80,
    ) -> dict[str, Any]:
        self._maybe_prune_expired()
        fmode = str(filter_mode or "all").strip()
        if fmode not in ("all", "closed", "active", "issue"):
            raise ValueError("Invalid filter_mode; expected 'all', 'closed', 'active', or 'issue'.")

        disk_runs = self.list_runs(status="all", limit=500, include_closed=True)
        ledger_runs = codex_telemetry.read_ledger_records(self.data_dir)

        by_id: dict[str, dict[str, Any]] = {}
        for rec in ledger_runs:
            rid = str(rec.get("run_id") or "").strip()
            if rid:
                by_id[rid] = rec
        for r in disk_runs:
            rid = str(r.get("run_id") or "").strip()
            if rid:
                r_copy = dict(r)
                r_copy["expired"] = False
                by_id[rid] = r_copy

        items = list(by_id.values())
        if fmode == "closed":
            items = [r for r in items if bool(r.get("closed"))]
        elif fmode == "active":
            items = [r for r in items if not bool(r.get("closed")) and not bool(r.get("expired"))]
        elif fmode == "issue":
            items = [r for r in items if r.get("status") in _ISSUE_STATUSES]

        q = query.strip().lower()
        if q:
            items = [
                r
                for r in items
                if q in str(r.get("title") or "").lower()
                or q in str(r.get("role") or "").lower()
                or q in str(r.get("work_digest") or "").lower()
                or q in str(r.get("prompt_preview") or "").lower()
                or q in str(r.get("model") or "").lower()
                or q in str(r.get("provider") or "").lower()
                or q in str(r.get("run_id") or "").lower()
                or q in str(r.get("parent_session_id") or "").lower()
                or q in str(r.get("parent_session_title") or "").lower()
                or q in str(r.get("error") or "").lower()
                or q in str(r.get("cwd") or "").lower()
            ]

        items.sort(key=lambda r: float(r.get("started_at") or 0), reverse=True)
        sliced = items[:limit]

        grouped: dict[str, dict[str, Any]] = {}
        for r in sliced:
            pid = str(r.get("parent_session_id") or "default")
            ptitle = str(r.get("parent_session_title") or pid)
            cwd_str = str(r.get("cwd") or "")
            grp = grouped.get(pid)
            if grp is None:
                grp = {
                    "session_id": pid,
                    "title": ptitle,
                    "parent_session_id": pid,
                    "parent_session_title": ptitle,
                    "workspace": Path(cwd_str).name if cwd_str else ptitle,
                    "updated_at": float(r.get("started_at") or 0),
                    "latest_started_at": float(r.get("started_at") or 0),
                    "run_count": 0,
                    "active_count": 0,
                    "running_count": 0,
                    "closed_count": 0,
                    "issue_count": 0,
                    "total_tokens": 0,
                    "runs": [],
                }
                grouped[pid] = grp
            grp["runs"].append(r)
            grp["run_count"] += 1
            if r.get("status") == "running":
                grp["running_count"] += 1
            if not r.get("closed"):
                grp["active_count"] += 1
            if r.get("closed"):
                grp["closed_count"] += 1
            if r.get("status") in _ISSUE_STATUSES:
                grp["issue_count"] += 1
            grp["total_tokens"] += int(r.get("tokens_used") or 0)
            ts = float(r.get("started_at") or 0)
            if ts > grp["latest_started_at"]:
                grp["latest_started_at"] = ts
                grp["updated_at"] = ts
                if ptitle:
                    grp["parent_session_title"] = ptitle
                    grp["title"] = ptitle

        sessions = sorted(grouped.values(), key=lambda g: g["latest_started_at"], reverse=True)
        wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
        retention_hours = int((wb_cfg.get("mapping") or {}).get("retention_hours", 24))
        return {
            "sessions": sessions,
            "groups": sessions,
            "total_runs": len(sliced),
            "retention_hours": retention_hours,
        }


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
            "(no inline chat card; call open_codex_panel to open/update live progress in the right side pane). "
            "Pass context_files, acceptance_criteria, and non_goals for self-contained business context. "
            "Supports model, provider, extended effort, role preset, and git worktree isolation. Returns run_id immediately."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "cwd"],
            "properties": {
                "prompt": {"type": "string", "description": "Self-contained bounded task contract for Codex."},
                "cwd": {"type": "string", "description": "Absolute working directory path."},
                "context_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of file/doc paths the worker should read first before editing.",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional concrete acceptance criteria the worker must satisfy and verify.",
                },
                "non_goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit boundaries and out-of-scope items not to alter.",
                },
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
                "timeout": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Worker wall-clock timeout in seconds. 0 means unlimited; live health, stall and loop detection plus manual stop remain available. If omitted, uses the workbench setting.",
                },
                "title": {"type": "string", "description": "Optional short task title for panel/history display."},
                "role": {"type": "string", "description": "Optional subagent role label (e.g. '只读探索', '代码实现', '独立审查')."},
                "conversation_id": {"type": "string", "description": "Optional parent Antigravity conversation ID for history grouping."},
                "conversation_title": {"type": "string", "description": "Optional parent Antigravity conversation title."},
            },
        },
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
                "timeout": {
                    "type": "integer",
                    "default": 120,
                    "minimum": 1,
                    "maximum": 120,
                    "description": "Max wait seconds per poll call (default 120, capped at 120s to avoid MCP transport timeout; if wait_timed_out=true, call wait_codex_task again instead of re-spawning).",
                },
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
        "name": "close_codex_task",
        "title": "Close Codex Task(s)",
        "description": (
            "Archive finished Codex task(s) from the active workbench monitor "
            "(cannot close running tasks; call stop_codex_task first)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "description": "Single finished run_id to close."},
                "run_ids": {"type": "array", "items": {"type": "string"}, "description": "Multiple finished run_ids to close."},
                "close_all_finished": {"type": "boolean", "default": False, "description": "Close all finished unclosed runs."},
                "conversation_id": {"type": "string", "description": "Optional parent conversation ID to scope close_all_finished."},
            },
        },
    },
    {
        "name": "open_codex_panel",
        "title": "Agy Codex",
        "description": (
            "Open or update the Agy Codex 4-tab workbench (Subagents Monitor, History, Usage Stats, Models & Config) "
            "in the right side pane (aux_pane) instead of inline in chat. Call right after start_codex_task "
            "with tab='subagents' and run_id (and optional conversation_id) to focus live execution scoped to the current conversation."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tab": {"type": "string", "enum": ["subagents", "history", "stats", "models"], "default": "subagents"},
                "run_id": {"type": "string", "description": "Optional run_id to focus initially."},
                "conversation_id": {"type": "string", "description": "Optional parent Antigravity conversation ID to isolate the Active Monitor to this conversation."},
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
        "name": "start_user_task",
        "title": "Start a Direct Codex Conversation",
        "description": "App-only: send a message directly to Codex from the workbench.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "cwd"],
            "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string"},
                "model": {"type": "string"},
                "provider": {"type": "string"},
                "effort": {"type": "string"},
                "mode": {"type": "string", "enum": ["write", "read-only"]},
                "config_mode": {"type": "string", "enum": ["isolated", "profile", "inherit"]},
                "preset": {"type": "string"},
                "worktree": {"type": "string"},
                "title": {"type": "string"},
                "conversation_id": {"type": "string"},
                "conversation_title": {"type": "string"},
                "persist": {"type": "boolean", "default": True},
                "raw": {"type": "boolean", "default": True},
                "search": {"type": "boolean", "default": False},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "check_for_updates",
        "title": "Check for Plugin Updates",
        "description": "App-only: check GitHub for an update or dismiss a specific version.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "force": {"type": "boolean", "default": False},
                "channel": {"type": "string", "enum": ["auto", "release", "main"], "default": "auto"},
                "dismiss_version": {"type": "string"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "start_plugin_update",
        "title": "Install Plugin Update",
        "description": "App-only: download and install the selected plugin update in the background.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "channel": {"type": "string", "enum": ["auto", "release", "main"], "default": "auto"},
                "expected_version": {"type": "string"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_update_status",
        "title": "Get Plugin Update Status",
        "description": "App-only: poll the current plugin update and its latest check result.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        "_meta": {"ui": {"visibility": ["app"]}},
    },
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
                "include_closed": {"type": "boolean", "default": False},
                "parent_session_id": {"type": "string", "description": "Optional parent Antigravity conversation ID filter."},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_history",
        "title": "Get Codex Run History",
        "description": "App-only: fetch Antigravity Codex runs (24h active + expired ledger) grouped by parent session.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "default": ""},
                "filter": {"type": "string", "enum": ["all", "closed", "active", "issue"], "default": "all"},
                "limit": {"type": "integer", "default": 80},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "close_run",
        "title": "Close Codex Run(s)",
        "description": "App-only: archive finished run(s) from the active monitor.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string"},
                "run_ids": {"type": "array", "items": {"type": "string"}},
                "close_all_finished": {"type": "boolean", "default": False},
                "parent_session_id": {"type": "string", "description": "Optional parent conversation ID to scope batch archiving and refreshed list."},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "reopen_run",
        "title": "Reopen Closed Codex Run",
        "description": "App-only: restore a non-expired closed run back to the active monitor.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "send_followup",
        "title": "Send Follow-up to Persisted Run",
        "description": "App-only: resume a finished persist=true Codex run with a follow-up prompt.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id", "prompt"],
            "properties": {
                "run_id": {"type": "string"},
                "prompt": {"type": "string"},
            },
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "toggle_codex_enabled",
        "title": "Toggle Codex Subagent Dispatch",
        "description": "App-only: pause or enable Codex subagent dispatch in workbench settings.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["enabled"],
            "properties": {"enabled": {"type": "boolean"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "toggle_exclude_orchestrator",
        "title": "Toggle Exclude Orchestrator Models",
        "description": "App-only: toggle excluding orchestrator models in workbench settings.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["enabled"],
            "properties": {"enabled": {"type": "boolean"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "toggle_intercept_native_subagents",
        "title": "Toggle Default Native Subagent Takeover",
        "description": "App-only: toggle whether Agy Codex hooks redirect general native subagent calls by default when enabled.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["enabled"],
            "properties": {"enabled": {"type": "boolean"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "get_stats",
        "title": "Get Unified Usage Stats",
        "description": "App-only: aggregate token, cache hit, duration, and breakdown stats across plugin runs.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "scope": {"type": "string", "enum": ["all", "antigravity", "codex_local"], "default": "antigravity"},
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
        "description": "App-only: persist enabled, exclude_orchestrator_models, defaults, mapping, custom_models, excluded_models, and presets.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "intercept_native_subagents": {"type": "boolean"},
                "exclude_orchestrator_models": {"type": "boolean"},
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
        self.stdout_lock = threading.Lock()

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
                    "serverInfo": {"name": "agy-codex", "version": "1.5.0"},
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
            active_runs = self.manager.active_count()
            payload["active_runs"] = active_runs
            payload["recommended_wait_timeout_sec"] = 120
            return self._tool_ok(payload, text=format_models_brief(payload, active_runs=active_runs))

        if name == "start_codex_task":
            state = self.manager.start_run({**arguments, "origin": "agent"})
            with state.lock:
                summary = state.to_summary_dict()
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            summary["active_runs"] = self.manager.active_count()
            summary["max_concurrent_runs"] = int(
                (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
            )
            summary["note"] = (
                "Codex worker started. Call open_codex_panel(tab='subagents', run_id=...) once "
                "to open or update live progress in the right side pane, then call wait_codex_task(..., timeout=120) "
                "or get_codex_task_status."
            )
            return self._tool_ok(summary, text=format_start_brief(summary))

        if name == "wait_codex_task":
            run_ids = self._collect_run_ids(arguments)
            wait_mode = str(arguments.get("wait_mode") or "all")
            raw_timeout = arguments.get("timeout")
            req_timeout = 120 if raw_timeout is None else int(raw_timeout)
            clamped = max(1, min(120, req_timeout))
            payload = self.manager.wait_runs(run_ids, wait_mode=wait_mode, timeout=clamped)
            if req_timeout > 120:
                payload["wait_timeout_clamped_sec"] = 120
            return self._tool_ok(payload, text=format_wait_or_status_brief(payload, is_wait=True))

        if name == "get_codex_task_status":
            run_ids = self._collect_run_ids(arguments)
            if not run_ids:
                all_runs = self.manager.list_runs(limit=10)
                run_ids = [r["run_id"] for r in all_runs if r.get("status") == "running"]
            payload = self.manager.get_status(run_ids)
            return self._tool_ok(payload, text=format_wait_or_status_brief(payload, is_wait=False))

        if name == "stop_codex_task":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            res = self.manager.cancel_run(run_id)
            return self._tool_ok(res, text=format_stop_or_close_brief(res, action="stop"))

        if name == "close_codex_task":
            run_ids = self._collect_run_ids(arguments)
            close_all = bool(arguments.get("close_all_finished", False))
            scope_sid = codex_catalog._clean_optional_str(arguments.get("conversation_id"))
            res = self.manager.close_runs(
                run_ids, closed_by="agent", close_all_finished=close_all, parent_session_id=scope_sid
            )
            return self._tool_ok(res, text=format_stop_or_close_brief(res, action="close"))

        if name == "open_codex_panel":
            tab = str(arguments.get("tab") or "subagents").strip()
            if tab not in ("subagents", "history", "stats", "models"):
                tab = "subagents"
            requested_run_id = codex_catalog._clean_optional_str(arguments.get("run_id"))
            scope_sid = codex_catalog._clean_optional_str(arguments.get("conversation_id"))
            scope_title: str | None = None
            if requested_run_id:
                try:
                    focused_summary = self.manager._get_summary(requested_run_id)
                except ValueError:
                    focused_summary = None
                if focused_summary:
                    focused_pid = codex_catalog._clean_optional_str(focused_summary.get("parent_session_id"))
                    if not scope_sid and focused_pid and focused_pid != "default":
                        scope_sid = focused_pid
                    if focused_pid == scope_sid:
                        scope_title = codex_catalog._clean_optional_str(focused_summary.get("parent_session_title"))
            if not scope_sid:
                env_sid = codex_catalog._clean_optional_str(os.environ.get("ANTIGRAVITY_CONVERSATION_ID"))
                if env_sid and env_sid != "default":
                    scope_sid = env_sid

            all_unclosed = self.manager.list_runs(limit=500, include_closed=False)
            if scope_sid:
                runs = [r for r in all_unclosed if str(r.get("parent_session_id") or "") == scope_sid][:40]
                if not scope_title and runs:
                    scope_title = codex_catalog._clean_optional_str(runs[0].get("parent_session_title"))
            else:
                runs = all_unclosed[:40]
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            selected = requested_run_id
            if not selected and runs:
                selected = runs[0].get("run_id")
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            total_unclosed = len(all_unclosed)
            global_active = sum(1 for r in all_unclosed if r.get("status") == "running")
            other_sessions = max(0, total_unclosed - len(runs)) if scope_sid else 0
            payload = {
                "view": "panel",
                "tab": tab,
                "runs": runs,
                "selected_run_id": selected,
                "scope_session_id": scope_sid,
                "scope_session_title": scope_title,
                "scope_mode": "session" if scope_sid else "all",
                "total_unclosed_runs": total_unclosed,
                "other_sessions_runs": other_sessions,
                "global_active_runs": global_active,
                "default_cwd": self.manager.infer_default_cwd(parent_session_id=scope_sid),
                "enabled": bool(wb_cfg.get("enabled", True)),
                "intercept_native_subagents": bool(wb_cfg.get("intercept_native_subagents", True)),
                "exclude_orchestrator_models": bool(wb_cfg.get("exclude_orchestrator_models", True)),
                "retention_hours": int((wb_cfg.get("mapping") or {}).get("retention_hours", 24)),
                "closed_24h_count": closed_24h,
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

        if name == "start_user_task":
            if not isinstance(arguments.get("cwd"), str) or not arguments["cwd"].strip():
                raise ValueError("'cwd' is required.")
            schema = next(tool["inputSchema"] for tool in TOOLS_DEF if tool["name"] == name)
            unexpected = arguments.keys() - schema["properties"].keys()
            if unexpected:
                raise ValueError(f"Unexpected argument(s): {', '.join(sorted(unexpected))}")
            conv_id = codex_catalog._clean_optional_str(arguments.get("conversation_id"))
            conv_title = codex_catalog._clean_optional_str(arguments.get("conversation_title"))
            user_args = {"persist": True, "raw": True, **arguments}
            user_args.update(
                origin="user",
                role="用户直连",
                conversation_id=conv_id or "user-direct",
                conversation_title=conv_title or (conv_id if conv_id else "用户直连 Codex"),
            )
            state = self.manager.start_run(user_args, bypass_enabled_check=True)
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            all_unclosed = self.manager.list_runs(limit=500, include_closed=False)
            scoped_runs = (
                [r for r in all_unclosed if str(r.get("parent_session_id") or "") == conv_id][:40]
                if conv_id
                else all_unclosed[:40]
            )
            return self._tool_ok({
                **state.to_summary_dict(),
                "scope_session_id": conv_id,
                "total_unclosed_runs": len(all_unclosed),
                "other_sessions_runs": max(0, len(all_unclosed) - len(scoped_runs)) if conv_id else 0,
                "global_active_runs": sum(1 for r in all_unclosed if r.get("status") == "running"),
                "active_runs": sum(1 for r in scoped_runs if r.get("status") == "running"),
                "max_concurrent_runs": int((wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)),
                "runs": scoped_runs,
            })

        if name == "check_for_updates":
            return self._tool_ok(codex_updater.check_for_updates(
                PLUGIN_ROOT, ddir, force=bool(arguments.get("force", False)),
                channel=arguments.get("channel", "auto"), dismiss_version=arguments.get("dismiss_version"),
            ))

        if name == "start_plugin_update":
            return self._tool_ok(codex_updater.start_plugin_update(
                PLUGIN_ROOT, ddir, channel=arguments.get("channel", "auto"),
                expected_version=arguments.get("expected_version"), codex_home=chome,
            ))

        if name == "get_update_status":
            return self._tool_ok(codex_updater.get_update_status(PLUGIN_ROOT, ddir))

        if name == "get_run_events":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            since_seq = int(arguments.get("since_seq") or 0)
            return self._tool_ok(self.manager.get_run_events(run_id, since_seq=since_seq))

        if name == "list_runs":
            status = str(arguments.get("status") or "all")
            limit = int(arguments.get("limit") or 40)
            include_closed = bool(arguments.get("include_closed", False))
            scope_sid = codex_catalog._clean_optional_str(arguments.get("parent_session_id"))
            runs = self.manager.list_runs(
                status=status,
                limit=limit,
                include_closed=include_closed,
                parent_session_id=scope_sid,
            )
            all_unclosed = self.manager.list_runs(limit=500, include_closed=False)
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            total_unclosed = len(all_unclosed)
            global_active = sum(1 for r in all_unclosed if r.get("status") == "running")
            other_sessions = (
                max(
                    0,
                    total_unclosed
                    - sum(1 for r in all_unclosed if str(r.get("parent_session_id") or "") == scope_sid),
                )
                if scope_sid
                else 0
            )
            return self._tool_ok(
                {
                    "runs": runs,
                    "scope_session_id": scope_sid,
                    "total_unclosed_runs": total_unclosed,
                    "other_sessions_runs": other_sessions,
                    "global_active_runs": global_active,
                    "default_cwd": self.manager.infer_default_cwd(parent_session_id=scope_sid),
                    "enabled": bool(wb_cfg.get("enabled", True)),
                    "intercept_native_subagents": bool(
                        wb_cfg.get("intercept_native_subagents", True)
                    ),
                    "exclude_orchestrator_models": bool(
                        wb_cfg.get("exclude_orchestrator_models", True)
                    ),
                    "retention_hours": int((wb_cfg.get("mapping") or {}).get("retention_hours", 24)),
                    "closed_24h_count": closed_24h,
                    "active_runs": sum(1 for r in runs if r.get("status") == "running"),
                    "max_concurrent_runs": int(
                        (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
                    ),
                }
            )

        if name == "get_history":
            query = str(arguments.get("query") or "")
            filter_mode = str(arguments.get("filter") or arguments.get("filter_mode") or "all")
            limit = int(arguments.get("limit") or 80)
            return self._tool_ok(
                self.manager.get_history(query=query, filter_mode=filter_mode, limit=limit)
            )

        if name == "close_run":
            run_ids = self._collect_run_ids(arguments)
            close_all = bool(arguments.get("close_all_finished", False))
            scope_sid = codex_catalog._clean_optional_str(arguments.get("parent_session_id"))
            res = self.manager.close_runs(
                run_ids, closed_by="user", close_all_finished=close_all, parent_session_id=scope_sid
            )
            all_unclosed = self.manager.list_runs(limit=500, include_closed=False)
            runs = (
                [r for r in all_unclosed if str(r.get("parent_session_id") or "") == scope_sid][:40]
                if scope_sid
                else all_unclosed[:40]
            )
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            res["runs"] = runs
            res["scope_session_id"] = scope_sid
            res["total_unclosed_runs"] = len(all_unclosed)
            res["other_sessions_runs"] = max(0, len(all_unclosed) - len(runs)) if scope_sid else 0
            res["global_active_runs"] = sum(1 for r in all_unclosed if r.get("status") == "running")
            res["active_runs"] = sum(1 for r in runs if r.get("status") == "running")
            res["closed_24h_count"] = closed_24h
            return self._tool_ok(res)

        if name == "reopen_run":
            run_id = str(arguments.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("'run_id' is required.")
            return self._tool_ok(self.manager.reopen_run(run_id))

        if name == "send_followup":
            run_id = str(arguments.get("run_id") or "").strip()
            prompt = str(arguments.get("prompt") or "").strip()
            state = self.manager.send_followup(run_id, prompt)
            with state.lock:
                summary = state.to_summary_dict()
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            summary["active_runs"] = self.manager.active_count()
            summary["max_concurrent_runs"] = int(
                (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5)
            )
            return self._tool_ok(summary)

        if name == "toggle_codex_enabled":
            if "enabled" not in arguments or not isinstance(arguments.get("enabled"), bool):
                raise ValueError("'enabled' boolean is required.")
            saved = codex_catalog.save_workbench_config(
                {"enabled": bool(arguments["enabled"])}, ddir
            )
            return self._tool_ok({"enabled": bool(saved.get("enabled", True)), "saved": saved})

        if name == "toggle_exclude_orchestrator":
            if "enabled" not in arguments or not isinstance(arguments.get("enabled"), bool):
                raise ValueError("'enabled' boolean is required.")
            saved = codex_catalog.save_workbench_config(
                {"exclude_orchestrator_models": bool(arguments["enabled"])}, ddir
            )
            return self._tool_ok(
                {
                    "exclude_orchestrator_models": bool(
                        saved.get("exclude_orchestrator_models", True)
                    ),
                    "workbench_config": saved,
                }
            )

        if name == "toggle_intercept_native_subagents":
            if "enabled" not in arguments or not isinstance(arguments.get("enabled"), bool):
                raise ValueError("'enabled' boolean is required.")
            saved = codex_catalog.save_workbench_config(
                {"intercept_native_subagents": bool(arguments["enabled"])}, ddir
            )
            return self._tool_ok(
                {
                    "intercept_native_subagents": bool(
                        saved.get("intercept_native_subagents", True)
                    ),
                    "workbench_config": saved,
                }
            )

        if name == "get_stats":
            scope = str(arguments.get("scope") or "antigravity")
            trange = str(arguments.get("range") or "7d")
            model = str(arguments.get("model") or "")
            provider = str(arguments.get("provider") or "")
            runs = self.manager.list_runs(limit=1000, include_closed=True)
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
            return self._tool_ok({
                **disc, "doctor": doc,
                "default_cwd": self.manager.infer_default_cwd(),
                "plugin_version": codex_updater.read_local_install_info(PLUGIN_ROOT)["current_version"],
            })

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

    def _tool_ok(self, payload: dict[str, Any], *, text: str | None = None) -> dict[str, Any]:
        body_text = text if text is not None else json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": body_text}],
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


def serve_stdio(
    server: McpServer,
    stdin_stream: Any,
    stdout_stream: Any,
    *,
    max_workers: int = 16,
) -> None:
    """Read JSON-RPC lines from stdin_stream and dispatch valid requests concurrently."""
    stdout_lock = getattr(server, "stdout_lock", None)
    if stdout_lock is None:
        stdout_lock = threading.Lock()
        server.stdout_lock = stdout_lock

    def _write_response(payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False) + "\n"
        with stdout_lock:
            stdout_stream.write(encoded)
            stdout_stream.flush()

    def _dispatch_request(req: dict[str, Any]) -> None:
        req_id = req.get("id")
        try:
            resp = server.handle_request(req)
        except Exception as exc:
            if req_id is None:
                return
            resp = server._error(req_id, -32000, str(exc))
        if resp is not None:
            try:
                _write_response(resp)
            except Exception as exc:
                print(
                    f"[agy-codex] failed to write response for id={req_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

    worker_count = max(1, int(max_workers or 1))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
    try:
        for raw_line in stdin_stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                _write_response(server._error(None, -32700, f"Parse error: {exc}"))
                continue

            if not isinstance(req, dict):
                continue
            executor.submit(_dispatch_request, req)
    finally:
        server.manager.shutdown()
        executor.shutdown(wait=True)


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    args = parse_args()
    data_dir = codex_catalog.get_plugin_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    manager = RunManager(data_dir=data_dir)
    server = McpServer(manager=manager)
    serve_stdio(server, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
