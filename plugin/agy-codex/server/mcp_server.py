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
  `preview_resolved_command`, `run_doctor`, `cancel_run`
- UI resource: `ui://agy-codex/app.html` (`text/html;profile=mcp-app`)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
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
            f"| slots={active_runs}/{max_conc} | worker_timeout=600s | wait_timeout<=120s"
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
    active = summary.get("active_runs", 1)
    max_conc = summary.get("max_concurrent_runs", 5)

    lines = [
        f"Started Codex task: {run_id} ({title})",
        (
            f"Config: model={eff_model} | provider={eff_prov} | effort={eff_effort} "
            f"| mode={mode} | timeout={timeout_sec}s | slots={active}/{max_conc}"
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
    timeout_sec: int = 600
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
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_summary_dict(self) -> dict[str, Any]:
        elapsed = (
            self.duration_sec
            if self.duration_sec is not None
            else round(max(0.0, time.time() - self.started_at), 2)
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
        completed_cmds = sum(
            1
            for ev in self.events
            if ev.get("kind") == "command" and ev.get("status") == "completed"
        )

        return {
            "run_id": self.run_id,
            "source": "mcp",
            "pid": self.pid,
            "ok": self.status == "done" and (self.exit_code == 0 or self.exit_code is None),
            "status": self.status,
            "title": self.title or codex_run.derive_short_title(preview or self.prompt, 16),
            "role": self.role or ("只读探索" if self.mode == "read-only" else "代码实现"),
            "work_digest": work_digest,
            "completed_commands": completed_cmds,
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
            "requested": self.requested,
            "effective": self.effective,
            "observed": self.observed,
            "warnings": self.warnings,
            "prompt_preview": preview,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "elapsed_sec": elapsed,
            "duration_sec": self.duration_sec,
            "timeout_sec": self.timeout_sec,
            "remaining_timeout_sec": remaining_timeout,
            "exit_code": self.exit_code,
            "session_id": self.session_id,
            "tokens_used": self.tokens_used,
            "usage": self.usage if self.saw_usage else self._snapshot_usage,
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
        self._summary_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._last_prune_ts: float = 0.0
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._prune_lock = threading.Lock()
        self._any_done_cond = threading.Condition(self._lock)

    def _state_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "state.json"

    def _events_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "events.jsonl"

    def _prompt_path(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "prompt.txt"

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
        summary.pop("events", None)
        summary.setdefault("expired", False)
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

    def start_run(self, params: dict[str, Any]) -> RunState:
        self._maybe_prune_expired()
        wb_cfg = codex_catalog.load_workbench_config(self.data_dir)
        if not wb_cfg.get("enabled", True):
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
        raw_add_dir = params.get("add_dir") or []
        add_dir = [str(d) for d in raw_add_dir if isinstance(d, str) and d.strip()]
        timeout = int(params.get("timeout") or 600)

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
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file = run_dir / "codex.log"
        output_file = run_dir / ("output.txt" if raw else "output.json")
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
        if eff.get("mode") == "write" and len(prompt) < 80 and not context_files:
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
            log_file=str(log_file),
            output_file=str(output_file),
        )
        with self._lock:
            self._runs[run_id] = state
        with state.lock:
            self._persist_state(state, force=True)

        thread = threading.Thread(
            target=self._execute_worker,
            args=(state, cmd, log_file, output_file, timeout, effective_prompt),
            daemon=True,
        )
        thread.start()
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
        env = codex_run.build_exec_env()
        timed_out = False
        log_lines: list[str] = []

        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE if stdin_prompt is not None else subprocess.DEVNULL,
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
            codex_run.write_prompt_to_stdin(proc, stdin_prompt)
        except Exception as exc:
            with state.lock:
                state.status = "error"
                state.error = f"Failed to launch codex CLI: {exc}"
                state.finished_at = time.time()
                state.duration_sec = round(state.finished_at - state.started_at, 2)
                state.exit_code = 1
                self._append_event(state, {"kind": "error", "message": state.error})
                self._persist_state(state, force=True)
                summary = state.to_summary_dict()
            codex_telemetry.append_ledger_record(self.data_dir, summary)
            self._notify_done(state)
            return

        with state.lock:
            state.process = proc
            state.pid = proc.pid
            state.updated_at = time.time()
            self._persist_state(state, force=True)

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
            if proc.stdin is not None and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

        if not log_file.parent.exists():
            self._notify_done(state)
            return

        combined_log = "".join(log_lines)
        try:
            log_file.write_text(combined_log, encoding="utf-8", newline="\n")
        except FileNotFoundError:
            self._notify_done(state)
            return

        raw_output = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
        parsed_result = safe_parse_handoff(raw_output) if raw_output else None
        if parsed_result is None and state.last_agent_message:
            candidate = safe_parse_handoff(state.last_agent_message.strip())
            if not timed_out or (isinstance(candidate, dict) and "status" in candidate):
                parsed_result = candidate

        fallback = codex_run.parse_telemetry(combined_log)

        with state.lock:
            state.process = None
            state.pid = None
            state.finished_at = time.time()
            state.updated_at = state.finished_at
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
            elif state._snapshot_usage:
                state.usage = dict(state._snapshot_usage)
                state.saw_usage = True
                snap_tok = state.usage["input_tokens"] + state.usage["output_tokens"]
                if snap_tok > 0:
                    state.tokens_used = snap_tok
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
                    completed_cmds = sum(
                        1
                        for ev in state.events
                        if ev.get("kind") == "command" and ev.get("status") == "completed"
                    )
                    state.status = "error"
                    if completed_cmds > 0:
                        state.error = (
                            f"执行超时 ({timeout}s)，中止前已完成 {completed_cmds} 条命令"
                            "（完整过程日志已保存在 codex.log，可展开步骤查看）"
                        )
                    else:
                        state.error = f"Timed out after {timeout}s"
                    self._append_event(state, {"kind": "error", "message": state.error})
                elif state.exit_code == 0:
                    if handoff_status == "blocked":
                        state.status = "blocked"
                        if not state.error:
                            state.error = handoff_summary or "Worker reported status=blocked."
                    elif handoff_status == "partial":
                        state.status = "partial"
                        if not state.error:
                            state.error = (
                                handoff_summary or "Worker reported status=partial (incomplete)."
                            )
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
            self._persist_state(state, force=True)
            final_summary = state.to_summary_dict()
        codex_telemetry.append_ledger_record(self.data_dir, final_summary)
        self._notify_done(state)

    def _handle_json_event(self, state: RunState, evt: dict[str, Any]) -> None:
        etype = evt.get("type")
        force_write = False
        with state.lock:
            state.updated_at = time.time()
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
                elif itype == "mcp_tool_call":
                    mcp_ev, obs_m, obs_e = codex_run.extract_mcp_tool_call_event(item)
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
                    or (err_obj.get("message") if isinstance(err_obj, dict) else None)
                    or json.dumps(evt, ensure_ascii=False)
                )
                state.error = str(msg)
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
                for raw_line in events_path.read_text(encoding="utf-8").splitlines():
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
        default_seq = int(all_events[-1].get("seq", len(all_events))) if all_events else 0
        latest_seq = int(persisted.pop("seq", default_seq) or default_seq)
        persisted["events"] = [ev for ev in all_events if int(ev.get("seq", 0)) > since_seq]
        persisted["latest_seq"] = latest_seq
        return persisted

    def list_runs(
        self,
        *,
        status: str = "all",
        limit: int = 40,
        include_closed: bool = False,
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
                persisted = self._load_persisted_state(child.name)
                if persisted is not None:
                    persisted = self._normalize_persisted_state(persisted, child.name)
                    persisted.pop("events", None)
                    persisted.pop("seq", None)
                    persisted["expired"] = False
                    seen[child.name] = persisted

        items = list(seen.values())
        if not include_closed:
            items = [r for r in items if not r.get("closed")]
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

        with state.lock:
            proc = state.process
            if state.status != "running":
                return state.to_summary_dict()
            state.status = "cancelled"
            state.pid = None
            state.error = "Cancelled by user."
            state.finished_at = state.finished_at or time.time()
            state.duration_sec = round(max(0.0, state.finished_at - state.started_at), 2)
            self._append_event(state, {"kind": "error", "message": "Cancelled by user."})
            self._persist_state(state, force=True)
            summary = state.to_summary_dict()

        codex_telemetry.append_ledger_record(self.data_dir, summary)
        codex_run.terminate_process_tree(proc)
        self._notify_done(state)
        return summary

    def close_runs(
        self,
        run_ids: list[str],
        *,
        closed_by: str,
        close_all_finished: bool = False,
    ) -> dict[str, Any]:
        skipped_running_ids: list[str] = []
        targets: list[str] = []

        if close_all_finished:
            unclosed = self.list_runs(status="all", limit=1000, include_closed=False)
            for item in unclosed:
                rid = str(item.get("run_id") or "")
                if not rid:
                    continue
                if item.get("status") == "running":
                    skipped_running_ids.append(rid)
                else:
                    targets.append(rid)
        else:
            deduped: list[str] = []
            for rid in run_ids:
                s = str(rid or "").strip()
                if s and s not in deduped:
                    deduped.append(s)
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
        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("'run_id' is required.")

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
        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("'run_id' is required.")
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
            }
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
            items = [r for r in items if r.get("status") in ("error", "blocked", "cancelled")]

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
            if r.get("status") in ("error", "blocked", "cancelled"):
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
                    "default": 600,
                    "description": "Worker execution timeout in seconds (default 600; keep >=600 for multi-directory scans, parallel workloads, or complex coding tasks).",
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
            },
        },
    },
    {
        "name": "open_codex_panel",
        "title": "Agy Codex",
        "description": (
            "Open or update the Agy Codex 4-tab workbench (Subagents Monitor, History, Usage Stats, Models & Config) "
            "in the right side pane (aux_pane) instead of inline in chat. Call right after start_codex_task "
            "with tab='subagents' and run_id to focus live execution."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tab": {"type": "string", "enum": ["subagents", "history", "stats", "models"], "default": "subagents"},
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
                "include_closed": {"type": "boolean", "default": False},
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
                    "serverInfo": {"name": "agy-codex", "version": "1.3.0"},
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
            state = self.manager.start_run(arguments)
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
            res = self.manager.close_runs(
                run_ids, closed_by="agent", close_all_finished=close_all
            )
            return self._tool_ok(res, text=format_stop_or_close_brief(res, action="close"))

        if name == "open_codex_panel":
            tab = str(arguments.get("tab") or "subagents").strip()
            if tab not in ("subagents", "history", "stats", "models"):
                tab = "subagents"
            runs = self.manager.list_runs(limit=40)
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            selected = arguments.get("run_id")
            if not selected and runs:
                selected = runs[0].get("run_id")
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            payload = {
                "view": "panel",
                "tab": tab,
                "runs": runs,
                "selected_run_id": selected,
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
            runs = self.manager.list_runs(
                status=status, limit=limit, include_closed=include_closed
            )
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            wb_cfg = codex_catalog.load_workbench_config(ddir)
            return self._tool_ok(
                {
                    "runs": runs,
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
            res = self.manager.close_runs(
                run_ids, closed_by="user", close_all_finished=close_all
            )
            runs = self.manager.list_runs(limit=40)
            all_disk = self.manager.list_runs(limit=500, include_closed=True)
            closed_24h = sum(1 for r in all_disk if (r.get("closed") or r.get("closed_at")))
            res["runs"] = runs
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
