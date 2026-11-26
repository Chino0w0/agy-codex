#!/usr/bin/env python3
"""Read-only local Codex telemetry & dual-source statistics engine for Agy Codex.

Reads:
1. Antigravity plugin runs (`mcp` / `cli`) from `${PLUGIN_DATA}/runs/*/state.json`
2. Local Codex CLI/Desktop sessions from `$CODEX_HOME/state_*.sqlite` (read-only)
   and rollout `.jsonl` tails cached incrementally in `${PLUGIN_DATA}/telemetry-cache.json`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from codex_catalog import atomic_write_text, get_codex_home, get_plugin_data_dir

STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")
THREAD_CANDIDATE_COLS = (
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "created_at_ms",
    "updated_at_ms",
    "source",
    "model_provider",
    "cwd",
    "title",
    "preview",
    "first_user_message",
    "tokens_used",
    "archived",
    "git_branch",
    "model",
    "reasoning_effort",
    "thread_source",
    "originator",
    "agent_role",
    "agent_nickname",
    "attached_thread_parent_id",
)


def normalize_windows_path(path: str | None) -> str:
    """Strip Windows extended-length path prefixes (`\\?\\`, `\\?\\UNC\\`)."""
    if not path:
        return ""
    s = str(path).strip()
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    if s.startswith("\\\\?\\"):
        return s[4:]
    return s


def find_latest_state_db(codex_home: Path) -> Path | None:
    """Locate the highest-numbered `state_*.sqlite` file under `codex_home`."""
    if not codex_home.is_dir():
        return None
    numbered: list[tuple[int, Path]] = []
    for child in codex_home.iterdir():
        if not child.is_file():
            continue
        m = STATE_DB_RE.match(child.name)
        if m:
            numbered.append((int(m.group(1)), child))
    if numbered:
        numbered.sort(key=lambda pair: pair[0], reverse=True)
        return numbered[0][1]
    fallback = codex_home / "state.sqlite"
    return fallback if fallback.is_file() else None


def _normalize_epoch_sec(ms_val: Any, sec_val: Any) -> float:
    if isinstance(ms_val, (int, float)) and ms_val > 0:
        return float(ms_val) / 1000.0
    if isinstance(sec_val, (int, float)) and sec_val > 0:
        val = float(sec_val)
        return val / 1000.0 if val > 1e12 else val
    return 0.0


def _read_tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            # Discard potentially partial first line
            fh.readline()
        raw = fh.read()
    return raw.decode("utf-8", errors="replace")


def _extract_usage_dict(obj: Any) -> dict[str, int] | None:
    if not isinstance(obj, dict):
        return None
    in_t = int(obj.get("input_tokens") or 0)
    c_t = int(obj.get("cached_input_tokens") or 0)
    out_t = int(obj.get("output_tokens") or 0)
    r_t = int(obj.get("reasoning_output_tokens") or 0)
    tot_t = int(obj.get("total_tokens") or (in_t + out_t))
    return {
        "input_tokens": in_t,
        "cached_input_tokens": c_t,
        "output_tokens": out_t,
        "reasoning_output_tokens": r_t,
        "total_tokens": tot_t,
    }


def parse_rollout_tail(rollout_path: Path, cache: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Parse token_count & turn_context from a rollout `.jsonl` tail with mtime/size cache."""
    key = str(rollout_path)
    try:
        st = rollout_path.stat()
    except OSError:
        return {}, False

    cached = cache.get(key)
    if (
        isinstance(cached, dict)
        and cached.get("mtime_ns") == st.st_mtime_ns
        and cached.get("size") == st.st_size
        and isinstance(cached.get("data"), dict)
    ):
        return cached["data"], False

    data: dict[str, Any] = {}
    for tail_bytes in (96 * 1024, 512 * 1024):
        try:
            text = _read_tail_text(rollout_path, tail_bytes)
        except OSError:
            return {}, False
        lines = text.splitlines()
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                evt = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            etype = evt.get("type")
            payload = evt.get("payload")
            if not isinstance(payload, dict):
                continue

            if (
                "total_token_usage" not in data
                and etype == "event_msg"
                and payload.get("type") == "token_count"
                and isinstance(payload.get("info"), dict)
            ):
                info = payload["info"]
                tot = _extract_usage_dict(info.get("total_token_usage"))
                last = _extract_usage_dict(info.get("last_token_usage"))
                cw = info.get("model_context_window")
                if tot is not None:
                    data["total_token_usage"] = tot
                if last is not None:
                    data["last_token_usage"] = last
                if isinstance(cw, (int, float)) and int(cw) > 0:
                    data["model_context_window"] = int(cw)

            if "model" not in data and etype == "turn_context":
                if isinstance(payload.get("model"), str) and payload["model"].strip():
                    data["model"] = payload["model"].strip()
                if isinstance(payload.get("effort"), str) and payload["effort"].strip():
                    data["effort"] = payload["effort"].strip()
                if isinstance(payload.get("cwd"), str) and payload["cwd"].strip():
                    data["cwd"] = normalize_windows_path(payload["cwd"])

            if "total_token_usage" in data and "model" in data:
                break
        if "total_token_usage" in data or st.st_size <= tail_bytes:
            break

    cache[key] = {
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
        "data": data,
    }
    return data, True


def _load_telemetry_cache(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "telemetry-cache.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_telemetry_cache(data_dir: Path, cache: dict[str, Any]) -> None:
    if len(cache) > 2500:
        # Keep cache bounded to recent entries
        keys = list(cache.keys())[-2000:]
        cache = {k: cache[k] for k in keys}
    try:
        atomic_write_text(
            data_dir / "telemetry-cache.json",
            json.dumps(cache, ensure_ascii=False),
        )
    except OSError:
        pass


def _open_readonly_sqlite(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _classify_local_source(row: dict[str, Any]) -> tuple[str, str | None]:
    ts = str(row.get("thread_source") or "").strip().lower()
    raw_src = row.get("source")
    parent_id: str | None = None
    if isinstance(raw_src, str) and raw_src.strip().startswith("{"):
        try:
            src_obj = json.loads(raw_src)
            if isinstance(src_obj, dict) and isinstance(src_obj.get("subagent"), dict):
                sub = src_obj["subagent"]
                if isinstance(sub.get("thread_spawn"), dict):
                    pid = sub["thread_spawn"].get("parent_thread_id")
                    if isinstance(pid, str) and pid:
                        parent_id = pid
        except json.JSONDecodeError:
            pass
    if not parent_id and isinstance(row.get("attached_thread_parent_id"), str):
        parent_id = row.get("attached_thread_parent_id") or None

    if ts in ("subagent", "guardian_review") or parent_id:
        return (ts or "subagent"), parent_id
    return (ts or "user"), parent_id


def read_local_codex_sessions(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    limit: int = 1000,
    enrich_recent_days: int = 30,
    enrich_max: int = 200,
) -> dict[str, Any]:
    """Read local Codex sessions from `$CODEX_HOME/state_*.sqlite` and rollout tails."""
    chome = get_codex_home(codex_home)
    ddir = get_plugin_data_dir(data_dir)
    db_path = find_latest_state_db(chome)
    warnings: list[str] = []

    if db_path is None:
        return {
            "sessions": [],
            "spawn_edges": [],
            "db_path": None,
            "warnings": ["Local Codex state_*.sqlite not found; showing plugin runs only."],
        }

    sessions_raw: list[dict[str, Any]] = []
    spawn_edges: list[dict[str, Any]] = []
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_readonly_sqlite(db_path)
        cur = conn.cursor()
        tables = {
            r[0]
            for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "threads" not in tables:
            return {
                "sessions": [],
                "spawn_edges": [],
                "db_path": str(db_path),
                "warnings": [f"SQLite DB {db_path.name} has no 'threads' table."],
            }

        existing_cols = {r[1] for r in cur.execute("PRAGMA table_info(threads)").fetchall()}
        select_cols = [c for c in THREAD_CANDIDATE_COLS if c in existing_cols]
        if "id" not in select_cols:
            return {
                "sessions": [],
                "spawn_edges": [],
                "db_path": str(db_path),
                "warnings": [f"SQLite DB {db_path.name} missing 'id' column."],
            }

        order_col = (
            "updated_at_ms"
            if "updated_at_ms" in existing_cols
            else ("updated_at" if "updated_at" in existing_cols else "rowid")
        )
        sql = f'SELECT {", ".join(select_cols)} FROM threads ORDER BY {order_col} DESC LIMIT ?'
        for row in cur.execute(sql, (int(limit),)).fetchall():
            sessions_raw.append(dict(zip(select_cols, row)))

        if "thread_spawn_edges" in tables:
            edge_cols = {
                r[1] for r in cur.execute("PRAGMA table_info(thread_spawn_edges)").fetchall()
            }
            if {"parent_thread_id", "child_thread_id"}.issubset(edge_cols):
                has_status = "status" in edge_cols
                esql = (
                    "SELECT parent_thread_id, child_thread_id, status FROM thread_spawn_edges"
                    if has_status
                    else "SELECT parent_thread_id, child_thread_id, '' FROM thread_spawn_edges"
                )
                for p_id, c_id, st in cur.execute(esql).fetchall():
                    spawn_edges.append(
                        {
                            "parent_thread_id": str(p_id or ""),
                            "child_thread_id": str(c_id or ""),
                            "status": str(st or ""),
                        }
                    )
    except sqlite3.Error as exc:
        warnings.append(f"SQLite read warning ({db_path.name}): {exc}")
        return {
            "sessions": [],
            "spawn_edges": [],
            "db_path": str(db_path),
            "warnings": warnings,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    edge_children: dict[str, list[dict[str, str]]] = {}
    edge_parent: dict[str, str] = {}
    for e in spawn_edges:
        pid = e["parent_thread_id"]
        cid = e["child_thread_id"]
        if pid and cid:
            edge_children.setdefault(pid, []).append(
                {"child_thread_id": cid, "status": e["status"]}
            )
            edge_parent.setdefault(cid, pid)

    cache = _load_telemetry_cache(ddir)
    cache_dirty = False
    cutoff_enrich = time.time() - (enrich_recent_days * 86400)
    enriched_count = 0

    sessions: list[dict[str, Any]] = []
    for row in sessions_raw:
        tid = str(row.get("id") or "")
        created_ts = _normalize_epoch_sec(row.get("created_at_ms"), row.get("created_at"))
        updated_ts = _normalize_epoch_sec(row.get("updated_at_ms"), row.get("updated_at")) or created_ts
        thread_kind, src_parent = _classify_local_source(row)
        parent_id = src_parent or edge_parent.get(tid)
        is_subagent = thread_kind != "user" or bool(parent_id)

        rollout_str = normalize_windows_path(row.get("rollout_path"))
        rollout_data: dict[str, Any] = {}
        if (
            rollout_str
            and enriched_count < enrich_max
            and (updated_ts == 0 or updated_ts >= cutoff_enrich)
        ):
            rp = Path(rollout_str)
            if rp.is_file():
                rollout_data, updated_cache = parse_rollout_tail(rp, cache)
                if updated_cache:
                    cache_dirty = True
                enriched_count += 1

        tot_usage = rollout_data.get("total_token_usage")
        last_usage = rollout_data.get("last_token_usage")
        cw = rollout_data.get("model_context_window")

        sqlite_tokens = int(row.get("tokens_used") or 0)
        if isinstance(tot_usage, dict):
            usage = {
                "input_tokens": int(tot_usage.get("input_tokens") or 0),
                "cached_input_tokens": int(tot_usage.get("cached_input_tokens") or 0),
                "output_tokens": int(tot_usage.get("output_tokens") or 0),
                "reasoning_output_tokens": int(tot_usage.get("reasoning_output_tokens") or 0),
            }
            tokens_used = usage["input_tokens"] + usage["output_tokens"]
        else:
            usage = None
            tokens_used = sqlite_tokens

        title = str(row.get("title") or row.get("preview") or row.get("first_user_message") or "").strip()
        sessions.append(
            {
                "thread_id": tid,
                "source": "codex_local",
                "thread_kind": thread_kind,
                "is_subagent": is_subagent,
                "parent_thread_id": parent_id,
                "child_threads": edge_children.get(tid, []),
                "model": row.get("model") or rollout_data.get("model"),
                "provider": row.get("model_provider"),
                "effort": row.get("reasoning_effort") or rollout_data.get("effort"),
                "cwd": normalize_windows_path(row.get("cwd")) or rollout_data.get("cwd") or "",
                "git_branch": row.get("git_branch") or "",
                "title": title[:180],
                "originator": row.get("originator") or "",
                "agent_nickname": row.get("agent_nickname"),
                "agent_role": row.get("agent_role"),
                "archived": bool(row.get("archived")),
                "created_at": created_ts,
                "updated_at": updated_ts,
                "tokens_used": tokens_used,
                "usage": usage,
                "last_token_usage": last_usage,
                "model_context_window": cw,
                "rollout_path": rollout_str,
            }
        )

    if cache_dirty:
        _save_telemetry_cache(ddir, cache)

    return {
        "sessions": sessions,
        "spawn_edges": spawn_edges,
        "db_path": str(db_path),
        "warnings": warnings,
    }


def _extract_recent_rollout_snippets(rollout_path: Path, max_snippets: int = 5) -> list[dict[str, str]]:
    if not rollout_path.is_file():
        return []
    try:
        text = _read_tail_text(rollout_path, 256 * 1024)
    except OSError:
        return []
    snippets: list[dict[str, str]] = []
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            evt = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        etype = evt.get("type")
        payload = evt.get("payload")
        if not isinstance(payload, dict):
            continue
        role = ""
        msg_text = ""
        if etype == "response_item" and payload.get("type") == "message":
            role = str(payload.get("role") or "")
            content = payload.get("content")
            if isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                        parts.append(blk["text"])
                msg_text = "\n".join(parts).strip()
        elif etype == "event_msg" and payload.get("type") in ("user_message", "agent_message"):
            role = "user" if payload.get("type") == "user_message" else "assistant"
            msg_text = str(payload.get("message") or payload.get("text") or "").strip()

        if role in ("user", "assistant") and msg_text:
            clean = " ".join(msg_text.split())
            if len(clean) > 400:
                clean = clean[:397] + "..."
            snippets.append({"role": role, "text": clean})
            if len(snippets) >= max_snippets:
                break
    snippets.reverse()
    return snippets


def get_local_session_detail(
    codex_home: Path | str | None,
    data_dir: Path | str | None,
    thread_id: str,
) -> dict[str, Any]:
    """Fetch detailed metadata, spawn tree, context occupancy, and recent snippets for a local thread."""
    tid = str(thread_id or "").strip()
    if not tid:
        raise ValueError("'thread_id' is required.")

    local_data = read_local_codex_sessions(codex_home, data_dir, limit=1500)
    sessions_by_id = {s["thread_id"]: s for s in local_data["sessions"]}
    target = sessions_by_id.get(tid)
    if target is None:
        raise ValueError(f"Local Codex thread not found: {tid}")

    cache = _load_telemetry_cache(get_plugin_data_dir(data_dir))
    rollout_str = target.get("rollout_path") or ""
    snippets: list[dict[str, str]] = []
    if rollout_str:
        rp = Path(rollout_str)
        if rp.is_file():
            rdata, dirty = parse_rollout_tail(rp, cache)
            if dirty:
                _save_telemetry_cache(get_plugin_data_dir(data_dir), cache)
            if isinstance(rdata.get("total_token_usage"), dict):
                tu = rdata["total_token_usage"]
                target["usage"] = {
                    "input_tokens": int(tu.get("input_tokens") or 0),
                    "cached_input_tokens": int(tu.get("cached_input_tokens") or 0),
                    "output_tokens": int(tu.get("output_tokens") or 0),
                    "reasoning_output_tokens": int(tu.get("reasoning_output_tokens") or 0),
                }
                target["tokens_used"] = (
                    target["usage"]["input_tokens"] + target["usage"]["output_tokens"]
                )
            if rdata.get("last_token_usage"):
                target["last_token_usage"] = rdata["last_token_usage"]
            if rdata.get("model_context_window"):
                target["model_context_window"] = rdata["model_context_window"]
            snippets = _extract_recent_rollout_snippets(rp, max_snippets=5)

    last_u = target.get("last_token_usage") or {}
    cw = target.get("model_context_window")
    context_percent: float | None = None
    context_raw_ratio: float | None = None
    if isinstance(cw, int) and cw > 0 and isinstance(last_u, dict) and last_u.get("input_tokens"):
        ratio = (float(last_u["input_tokens"]) / float(cw)) * 100.0
        context_raw_ratio = round(ratio, 2)
        context_percent = round(max(0.0, min(100.0, ratio)), 1)

    child_details: list[dict[str, Any]] = []
    for edge in target.get("child_threads") or []:
        cid = edge.get("child_thread_id") or ""
        cs = sessions_by_id.get(cid)
        child_details.append(
            {
                "thread_id": cid,
                "status": edge.get("status") or "",
                "title": (cs or {}).get("title") or cid[:12],
                "model": (cs or {}).get("model"),
                "effort": (cs or {}).get("effort"),
                "thread_kind": (cs or {}).get("thread_kind") or "subagent",
                "tokens_used": (cs or {}).get("tokens_used") or 0,
            }
        )

    return {
        **target,
        "context_percent": context_percent,
        "context_raw_ratio": context_raw_ratio,
        "child_details": child_details,
        "snippets": snippets,
        "warnings": local_data.get("warnings", []),
    }


def _cutoff_timestamp(time_range: str) -> float | None:
    now = datetime.now()
    if time_range == "today":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()
    if time_range == "7d":
        return (now - timedelta(days=7)).timestamp()
    if time_range == "30d":
        return (now - timedelta(days=30)).timestamp()
    return None


def _date_label(ts: float) -> str:
    if ts <= 0:
        return "unknown"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def compute_stats(
    plugin_runs: list[dict[str, Any]],
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    scope: str = "all",
    time_range: str = "7d",
    model: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """Aggregate unified token, cache hit, duration, and breakdown statistics (zero cost fields)."""
    if scope not in ("all", "antigravity", "codex_local"):
        raise ValueError("Invalid scope; expected 'all', 'antigravity', or 'codex_local'.")
    if time_range not in ("today", "7d", "30d", "all"):
        raise ValueError("Invalid time_range; expected 'today', '7d', '30d', or 'all'.")

    cutoff = _cutoff_timestamp(time_range)
    m_filter = model.strip()
    p_filter = provider.strip()

    plugin_session_ids = {
        str(r.get("session_id")).strip()
        for r in plugin_runs
        if isinstance(r.get("session_id"), str) and str(r.get("session_id")).strip()
    }

    local_sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    if scope in ("all", "codex_local"):
        local_res = read_local_codex_sessions(codex_home, data_dir, limit=2000)
        warnings.extend(local_res.get("warnings") or [])
        # Strict partition deduplication: exclude local threads already owned by plugin runs
        local_sessions = [
            s for s in local_res["sessions"] if s["thread_id"] not in plugin_session_ids
        ]

    selected_items: list[dict[str, Any]] = []

    if scope in ("all", "antigravity"):
        for r in plugin_runs:
            ts = float(r.get("started_at") or 0)
            if cutoff is not None and ts < cutoff:
                continue
            rmodel = str(r.get("model") or "")
            rprov = str(r.get("provider") or "")
            if m_filter and rmodel != m_filter:
                continue
            if p_filter and rprov != p_filter:
                continue
            selected_items.append(
                {
                    "kind": "plugin",
                    "id": r.get("run_id") or "",
                    "ts": ts,
                    "status": str(r.get("status") or "unknown"),
                    "model": rmodel or "(default)",
                    "provider": rprov or "(default)",
                    "effort": str(r.get("effort") or "(default)"),
                    "tokens_used": int(r.get("tokens_used") or 0),
                    "usage": r.get("usage") if isinstance(r.get("usage"), dict) else None,
                    "duration_sec": r.get("duration_sec"),
                    "error": r.get("error"),
                }
            )

    if scope in ("all", "codex_local"):
        for s in local_sessions:
            ts = float(s.get("updated_at") or s.get("created_at") or 0)
            if cutoff is not None and ts < cutoff:
                continue
            smodel = str(s.get("model") or "")
            sprov = str(s.get("provider") or "")
            if m_filter and smodel != m_filter:
                continue
            if p_filter and sprov != p_filter:
                continue
            selected_items.append(
                {
                    "kind": "codex_local",
                    "id": s.get("thread_id") or "",
                    "ts": ts,
                    "is_subagent": bool(s.get("is_subagent")),
                    "model": smodel or "(unknown)",
                    "provider": sprov or "(default)",
                    "effort": str(s.get("effort") or "(default)"),
                    "tokens_used": int(s.get("tokens_used") or 0),
                    "usage": s.get("usage") if isinstance(s.get("usage"), dict) else None,
                }
            )

    plugin_count = 0
    local_user_count = 0
    local_subagent_count = 0
    running_count = 0
    done_count = 0
    error_count = 0
    cancelled_count = 0

    total_tokens = 0
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    reasoning_output_tokens = 0
    cache_in_denom = 0
    cache_coverage_count = 0

    dur_sum = 0.0
    dur_count = 0

    daily_map: dict[str, dict[str, Any]] = {}
    model_map: dict[str, dict[str, Any]] = {}
    provider_map: dict[str, dict[str, Any]] = {}
    effort_map: dict[str, dict[str, Any]] = {}
    recent_errors: list[dict[str, Any]] = []

    for item in selected_items:
        is_plugin = item["kind"] == "plugin"
        if is_plugin:
            plugin_count += 1
            st = item["status"]
            if st == "running":
                running_count += 1
            elif st == "done":
                done_count += 1
            elif st == "error":
                error_count += 1
                recent_errors.append(
                    {
                        "run_id": item["id"],
                        "model": item["model"],
                        "ts": item["ts"],
                        "error": str(item.get("error") or "Unknown error"),
                    }
                )
            elif st == "cancelled":
                cancelled_count += 1

            dur = item.get("duration_sec")
            if isinstance(dur, (int, float)) and dur >= 0:
                dur_sum += float(dur)
                dur_count += 1
        else:
            if item.get("is_subagent"):
                local_subagent_count += 1
            else:
                local_user_count += 1

        u = item.get("usage")
        if isinstance(u, dict):
            in_t = int(u.get("input_tokens") or 0)
            c_t = int(u.get("cached_input_tokens") or 0)
            out_t = int(u.get("output_tokens") or 0)
            r_t = int(u.get("reasoning_output_tokens") or 0)
            tok = in_t + out_t
        else:
            in_t = c_t = out_t = r_t = 0
            tok = int(item.get("tokens_used") or 0)

        total_tokens += tok
        input_tokens += in_t
        cached_input_tokens += c_t
        output_tokens += out_t
        reasoning_output_tokens += r_t
        if in_t > 0:
            cache_in_denom += in_t
            cache_coverage_count += 1

        # Daily bucket
        dkey = _date_label(item["ts"])
        if dkey != "unknown":
            dbucket = daily_map.setdefault(
                dkey,
                {
                    "date": dkey,
                    "count": 0,
                    "plugin_count": 0,
                    "local_count": 0,
                    "tokens": 0,
                    "cached_tokens": 0,
                    "errors": 0,
                },
            )
            dbucket["count"] += 1
            if is_plugin:
                dbucket["plugin_count"] += 1
                if item.get("status") == "error":
                    dbucket["errors"] += 1
            else:
                dbucket["local_count"] += 1
            dbucket["tokens"] += tok
            dbucket["cached_tokens"] += c_t

        # Dimension buckets
        for dim_map, dim_key in (
            (model_map, item["model"]),
            (provider_map, item["provider"]),
            (effort_map, item["effort"]),
        ):
            b = dim_map.setdefault(
                dim_key,
                {
                    "name": dim_key,
                    "count": 0,
                    "tokens": 0,
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "errors": 0,
                    "dur_sum": 0.0,
                    "dur_count": 0,
                },
            )
            b["count"] += 1
            b["tokens"] += tok
            b["input_tokens"] += in_t
            b["cached_tokens"] += c_t
            b["output_tokens"] += out_t
            if is_plugin and item.get("status") == "error":
                b["errors"] += 1
            if is_plugin and isinstance(item.get("duration_sec"), (int, float)):
                b["dur_sum"] += float(item["duration_sec"])
                b["dur_count"] += 1

    finished_plugin = done_count + error_count
    success_rate = round((done_count / finished_plugin) * 100.0, 1) if finished_plugin > 0 else None
    cache_hit_rate = (
        round((cached_input_tokens / cache_in_denom) * 100.0, 1) if cache_in_denom > 0 else None
    )
    avg_duration_sec = round(dur_sum / dur_count, 2) if dur_count > 0 else None

    def finalize_dim(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for val in d.values():
            in_sum = val["input_tokens"]
            dcnt = val["dur_count"]
            rows.append(
                {
                    "name": val["name"],
                    "count": val["count"],
                    "tokens": val["tokens"],
                    "input_tokens": in_sum,
                    "cached_tokens": val["cached_tokens"],
                    "output_tokens": val["output_tokens"],
                    "cache_hit_rate": round((val["cached_tokens"] / in_sum) * 100.0, 1)
                    if in_sum > 0
                    else None,
                    "errors": val["errors"],
                    "avg_duration_sec": round(val["dur_sum"] / dcnt, 2) if dcnt > 0 else None,
                }
            )
        rows.sort(key=lambda r: (r["tokens"], r["count"]), reverse=True)
        return rows

    daily_list = sorted(daily_map.values(), key=lambda d: d["date"])
    recent_errors.sort(key=lambda e: e["ts"], reverse=True)

    return {
        "scope": scope,
        "range": time_range,
        "overview": {
            "total_items": len(selected_items),
            "plugin_run_count": plugin_count,
            "local_user_session_count": local_user_count,
            "local_subagent_count": local_subagent_count,
            "running_count": running_count,
            "done_count": done_count,
            "error_count": error_count,
            "cancelled_count": cancelled_count,
            "success_rate": success_rate,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "cache_hit_rate": cache_hit_rate,
            "cache_coverage_count": cache_coverage_count,
            "avg_duration_sec": avg_duration_sec,
            "total_duration_sec": round(dur_sum, 2),
        },
        "daily": daily_list,
        "by_model": finalize_dim(model_map),
        "by_provider": finalize_dim(provider_map),
        "by_effort": finalize_dim(effort_map),
        "recent_errors": recent_errors[:20],
        "warnings": warnings,
    }
