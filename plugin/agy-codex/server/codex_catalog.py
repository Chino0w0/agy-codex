#!/usr/bin/env python3
"""Model catalog, provider discovery, workbench presets, and whitelist isolation for Agy Codex.

100% Python stdlib (`tomllib` used on Python 3.11+ with graceful fallback on 3.10).
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]

EFFORT_RANK: list[str] = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
EFFORT_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
EXTRA_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

SUBAGENT_ROLE_PREFIX = (
    "[SUBAGENT ROLE DECLARATION / 子代理身份声明] "
    "You are running as a non-interactive LEAF SUBAGENT (子代理/Worker) invoked by an external parent orchestrator (Antigravity), "
    "NOT the root/main agent (根主代理). Your parent orchestrator has already decomposed the task and delegated this bounded subtask to you. "
    "Any global AGENTS.md, user config, or default workflow instructions telling the root agent to split work, spawn subagents "
    "(`spawn_agent`, `multi_agent`), or delegate to child agents DO NOT APPLY to you. "
    "Execute this task directly yourself with zero nested subagent spawning. "
    "Before editing, read any specified `context_files` / reference docs and nearby existing files, strictly adhering to existing project architecture, naming conventions, and business behavior. "
    "If business rules are unclear, prerequisites are missing, or existing code conflicts with the task contract, do NOT guess or silently alter unspecified interface fields: immediately return `status: \"blocked\"` and list the exact ambiguities in `risks`. "
    "If an optional MCP tool referenced in AGENTS.md (such as `fastctx` or `runtime_identity`) is unavailable in this session, "
    "silently use built-in tools without wasting turns searching for missing tools. "
    "On Windows inside a restricted sandbox, if Git Bash or WSL bash.exe fails with signal-pipe or access errors, "
    "use native PowerShell (pwsh) or Python directly (or any forwarded file/search MCP tools available in this session)."
)

NESTED_SUBAGENT_PREFIX = (
    "[SUBAGENT ROLE DECLARATION / 子代理身份声明] "
    "You are running as a non-interactive delegated subagent (子代理/Worker) invoked by an external parent orchestrator (Antigravity), "
    "NOT the root/main agent (根主代理). Nested subagents are explicitly enabled for this run, but only use them when genuinely necessary. "
    "Before editing, read any specified `context_files` / reference docs and nearby existing files, strictly adhering to existing project architecture, naming conventions, and business behavior. "
    "If business rules are unclear, prerequisites are missing, or existing code conflicts with the task contract, do NOT guess or silently alter unspecified interface fields: immediately return `status: \"blocked\"` and list the exact ambiguities in `risks`."
)

DEFAULT_DEVELOPER_INSTRUCTIONS = (
    SUBAGENT_ROLE_PREFIX
    + " Do not ask interactive follow-up questions, and do not modify files outside the requested scope. "
    "Run concrete verification commands (tests, linters, or scripts) for every change when possible. "
    "Report concise, factual results: changed file paths, exact verification commands and outcomes, and any remaining risks or blockers."
)

NESTED_SUBAGENT_INSTRUCTIONS = (
    NESTED_SUBAGENT_PREFIX
    + " Execute the bounded task directly and thoroughly within the workspace. "
    "Do not ask interactive follow-up questions, and do not modify files outside the requested scope. "
    "Run concrete verification commands (tests, linters, or scripts) for every change when possible. "
    "Report concise, factual results: changed file paths, exact verification commands and outcomes, and any remaining risks or blockers."
)


def build_worker_developer_instructions(
    worker_cfg: dict[str, Any], *, allow_nested: bool = False
) -> str:
    """Build explicit subagent-scoped developer_instructions flattened to single line for Windows `.cmd` safety."""
    custom = _clean_optional_str(worker_cfg.get("developer_instructions"))
    if not custom:
        raw_inst = NESTED_SUBAGENT_INSTRUCTIONS if allow_nested else DEFAULT_DEVELOPER_INSTRUCTIONS
    elif "SUBAGENT ROLE DECLARATION" in custom:
        raw_inst = custom
    else:
        prefix = NESTED_SUBAGENT_PREFIX if allow_nested else SUBAGENT_ROLE_PREFIX
        raw_inst = f"{prefix} {custom}"
    return " ".join(raw_inst.split())

NON_SECRET_KEYS: set[str] = {
    "tool_output_token_limit",
    "model_auto_compact_token_limit",
    "requires_openai_auth",
    "env_key",
}

SENSITIVE_KEY_PATTERN = re.compile(
    r"(api_?key|bearer|secret|password|authorization|cookie|credential|_token$|^token$)",
    re.IGNORECASE,
)

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "explore": {
        "label": "只读探索",
        "model": None,
        "provider": None,
        "effort": "medium",
        "mode": "read-only",
        "when_to_use": "代码库只读探索、调用方排查、局部信息提取与核查",
    },
    "coding": {
        "label": "编码实现",
        "model": None,
        "provider": None,
        "effort": "high",
        "mode": "write",
        "when_to_use": "边界清晰的模块实现、功能开发与单元测试编写",
    },
    "frontend": {
        "label": "前端迭代",
        "model": None,
        "provider": None,
        "effort": "medium",
        "mode": "write",
        "when_to_use": "前端页面布局、样式调整、交互组件快速迭代",
    },
    "review": {
        "label": "独立审查",
        "model": None,
        "provider": None,
        "effort": "medium",
        "mode": "read-only",
        "when_to_use": "跨模型只读 Code Review、风险核查与边界条件复核",
    },
    "debug": {
        "label": "深度排障",
        "model": None,
        "provider": None,
        "effort": "xhigh",
        "mode": "read-only",
        "when_to_use": "复杂跨模块推理、疑难 Bug 根因定位与架构诊断",
    },
    "fast": {
        "label": "轻量批量",
        "model": None,
        "provider": None,
        "effort": "low",
        "mode": "write",
        "when_to_use": "样板脚本、文档整理、简单低风险重复改动",
    },
}

DEFAULT_WORKBENCH_CONFIG: dict[str, Any] = {
    "enabled": True,
    "exclude_orchestrator_models": True,
    "defaults": {
        "model": None,
        "provider": None,
        "effort": None,
        "config_mode": "isolated",
        "mode": "write",
    },
    "mapping": {
        "catalog_mode": "auto",  # auto | custom | off
        "catalog_path_override": None,
        "effort_policy": "clamp_with_warning",  # clamp_with_warning | passthrough
        "context_window_policy": "same_model_only",  # same_model_only | always_forward | never_forward
        "extra_whitelist_keys": [],
        "forwarded_mcp_servers": [],
        "allow_nested_subagents": False,
        "max_concurrent_runs": 5,
        "retention_hours": 24,
    },
    "excluded_models": [],
    "custom_models": [],
    "presets": copy.deepcopy(DEFAULT_PRESETS),
}


def is_orchestrator_model(slug: str | None, display_name: str | None = "") -> bool:
    """Return True if 'orchestrator' appears case-insensitively in slug or display_name."""
    return (
        "orchestrator" in (slug or "").lower()
        or "orchestrator" in (display_name or "").lower()
    )


def get_codex_home(explicit: Path | str | None = None) -> Path:
    """Resolve CODEX_HOME dynamically from argument, environment, or `~/.codex`."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser()
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def get_plugin_data_dir(explicit: Path | str | None = None) -> Path:
    """Resolve plugin persistent data directory."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser()
    env_dir = os.environ.get("AGY_CODEX_DATA_DIR") or os.environ.get("CODEX_WORKER_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir).expanduser()
    return Path.home() / ".gemini" / "antigravity" / "plugin_data" / "agy-codex"


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write UTF-8 LF text file with Windows transient-lock retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        last_exc: OSError | None = None
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                last_exc = None
                break
            except (PermissionError, OSError) as exc:
                last_exc = exc
                if attempt < 4:
                    time.sleep(0.015 * (2 ** attempt))
        if last_exc is not None:
            raise last_exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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
    """Format a TOML key segment safely for `-c dotted.path=value`."""
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key, ensure_ascii=False)


def to_toml_literal(value: Any) -> str:
    """Serialize a Python scalar or list into a valid TOML literal string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(to_toml_literal(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML literal type: {type(value).__name__}")


def is_sensitive_key(key_path: str) -> bool:
    """Return True if a config key or dotted key path may hold a secret/credential."""
    parts = [p.strip('"').strip("'") for p in key_path.split(".") if p]
    if not parts:
        return False
    leaf = parts[-1]
    if leaf in NON_SECRET_KEYS:
        return False
    if "http_headers" in parts[:-1]:
        return True
    if any(SENSITIVE_KEY_PATTERN.search(p) for p in parts):
        return True
    return False


def redact_value_for_key(key_path: str, value: Any) -> Any:
    """Redact scalar/dict values when key_path is sensitive."""
    if is_sensitive_key(key_path):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(k): redact_value_for_key(f"{key_path}.{k}", v)
            for k, v in value.items()
        }
    return value


def _clean_optional_str(val: Any) -> str | None:
    if isinstance(val, str):
        s = val.strip()
        return s if s else None
    return None


def _normalize_efforts_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        val: str | None = None
        if isinstance(item, str):
            val = item.strip()
        elif isinstance(item, dict):
            ev = item.get("effort") or item.get("level") or item.get("name")
            if isinstance(ev, str):
                val = ev.strip()
        if val and EFFORT_RE.match(val) and val not in out:
            out.append(val)
    return out


def _normalize_workbench_config(raw: Any) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_WORKBENCH_CONFIG)
    if not isinstance(raw, dict):
        return cfg

    if "enabled" in raw and raw.get("enabled") is not None:
        cfg["enabled"] = bool(raw.get("enabled"))
    if "exclude_orchestrator_models" in raw and raw.get("exclude_orchestrator_models") is not None:
        cfg["exclude_orchestrator_models"] = bool(raw.get("exclude_orchestrator_models"))

    raw_defaults = raw.get("defaults")
    if isinstance(raw_defaults, dict):
        cfg["defaults"]["model"] = _clean_optional_str(raw_defaults.get("model"))
        cfg["defaults"]["provider"] = _clean_optional_str(raw_defaults.get("provider"))
        effort = _clean_optional_str(raw_defaults.get("effort"))
        if effort and EFFORT_RE.match(effort):
            cfg["defaults"]["effort"] = effort
        cm = _clean_optional_str(raw_defaults.get("config_mode"))
        if cm in ("isolated", "profile", "inherit"):
            cfg["defaults"]["config_mode"] = cm
        md = _clean_optional_str(raw_defaults.get("mode"))
        if md in ("write", "read-only"):
            cfg["defaults"]["mode"] = md

    raw_mapping = raw.get("mapping")
    if isinstance(raw_mapping, dict):
        cat_mode = _clean_optional_str(raw_mapping.get("catalog_mode"))
        if cat_mode in ("auto", "custom", "off"):
            cfg["mapping"]["catalog_mode"] = cat_mode
        cfg["mapping"]["catalog_path_override"] = _clean_optional_str(
            raw_mapping.get("catalog_path_override")
        )
        ep = _clean_optional_str(raw_mapping.get("effort_policy"))
        if ep in ("clamp_with_warning", "passthrough"):
            cfg["mapping"]["effort_policy"] = ep
        cwp = _clean_optional_str(raw_mapping.get("context_window_policy"))
        if cwp in ("same_model_only", "always_forward", "never_forward"):
            cfg["mapping"]["context_window_policy"] = cwp
        for field_key in ("extra_whitelist_keys", "forwarded_mcp_servers"):
            raw_list = raw_mapping.get(field_key)
            if isinstance(raw_list, list):
                cleaned_list: list[str] = []
                for k in raw_list:
                    if isinstance(k, str):
                        ks = k.strip()
                        if ks and EXTRA_KEY_RE.match(ks) and ks not in cleaned_list:
                            cleaned_list.append(ks)
                cfg["mapping"][field_key] = cleaned_list
        if "allow_nested_subagents" in raw_mapping:
            cfg["mapping"]["allow_nested_subagents"] = bool(raw_mapping.get("allow_nested_subagents"))
        if "max_concurrent_runs" in raw_mapping:
            try:
                mcr = int(raw_mapping.get("max_concurrent_runs"))
                cfg["mapping"]["max_concurrent_runs"] = max(1, min(20, mcr))
            except (TypeError, ValueError):
                pass
        if "retention_hours" in raw_mapping:
            raw_rh = raw_mapping.get("retention_hours")
            if not isinstance(raw_rh, bool):
                try:
                    rh = int(raw_rh)
                    cfg["mapping"]["retention_hours"] = max(1, min(168, rh))
                except (TypeError, ValueError):
                    pass

    raw_excluded = raw.get("excluded_models")
    if isinstance(raw_excluded, list):
        excl: list[str] = []
        for item in raw_excluded:
            s = _clean_optional_str(item)
            if s and s not in excl:
                excl.append(s)
        cfg["excluded_models"] = excl

    raw_custom = raw.get("custom_models")
    if isinstance(raw_custom, list):
        customs: list[dict[str, Any]] = []
        seen_slugs: set[str] = set()
        for item in raw_custom:
            if not isinstance(item, dict):
                continue
            slug = _clean_optional_str(item.get("slug") or item.get("id"))
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            cw = item.get("context_window")
            cw_int: int | None = None
            if isinstance(cw, (int, float)) and int(cw) > 0:
                cw_int = int(cw)
            def_eff = _clean_optional_str(item.get("default_reasoning_level"))
            if def_eff and not EFFORT_RE.match(def_eff):
                def_eff = None
            customs.append(
                {
                    "slug": slug,
                    "display_name": _clean_optional_str(item.get("display_name")) or slug,
                    "provider": _clean_optional_str(item.get("provider")),
                    "default_reasoning_level": def_eff,
                    "supported_reasoning_levels": _normalize_efforts_list(
                        item.get("supported_reasoning_levels")
                    ),
                    "context_window": cw_int,
                    "visibility": "hide" if item.get("visibility") == "hide" else "list",
                }
            )
        cfg["custom_models"] = customs

    raw_presets = raw.get("presets")
    if isinstance(raw_presets, dict):
        merged_presets: dict[str, dict[str, Any]] = copy.deepcopy(DEFAULT_PRESETS)
        # If caller explicitly provided a dict, allow modifying defaults or adding/removing custom keys;
        # keep any built-in presets unless explicitly omitted only if a replacement map has keys.
        if raw_presets:
            merged_presets = {}
            for name, pval in raw_presets.items():
                pname = _clean_optional_str(name)
                if not pname or not isinstance(pval, dict) or not EFFORT_RE.match(pname):
                    continue
                base_p = copy.deepcopy(DEFAULT_PRESETS.get(pname, {}))
                effort = _clean_optional_str(pval.get("effort"))
                if effort and not EFFORT_RE.match(effort):
                    effort = base_p.get("effort")
                mode = _clean_optional_str(pval.get("mode"))
                if mode not in ("write", "read-only"):
                    mode = base_p.get("mode") or "write"
                merged_presets[pname] = {
                    "label": _clean_optional_str(pval.get("label")) or base_p.get("label") or pname,
                    "model": _clean_optional_str(pval.get("model")),
                    "provider": _clean_optional_str(pval.get("provider")),
                    "effort": effort,
                    "mode": mode,
                    "when_to_use": _clean_optional_str(pval.get("when_to_use"))
                    or base_p.get("when_to_use")
                    or "",
                }
        cfg["presets"] = merged_presets

    return cfg


def load_workbench_config(data_dir: Path | str | None = None) -> dict[str, Any]:
    """Load and normalize `${PLUGIN_DATA}/workbench-config.json`."""
    ddir = get_plugin_data_dir(data_dir)
    path = ddir / "workbench-config.json"
    if not path.is_file():
        return copy.deepcopy(DEFAULT_WORKBENCH_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_workbench_config(raw)
    except Exception:
        return copy.deepcopy(DEFAULT_WORKBENCH_CONFIG)


def save_workbench_config(payload: dict[str, Any], data_dir: Path | str | None = None) -> dict[str, Any]:
    """Validate, merge if partial, and atomically save `${PLUGIN_DATA}/workbench-config.json`."""
    if not isinstance(payload, dict):
        raise ValueError("Workbench config payload must be a JSON object.")
    current = load_workbench_config(data_dir)
    merged = copy.deepcopy(current)

    for bool_key in ("enabled", "exclude_orchestrator_models"):
        if bool_key in payload:
            if not isinstance(payload[bool_key], bool):
                raise ValueError(f"'{bool_key}' must be a boolean.")
            merged[bool_key] = payload[bool_key]

    for key in ("defaults", "mapping"):
        if key in payload:
            if not isinstance(payload[key], dict):
                raise ValueError(f"'{key}' must be an object.")
            merged[key].update(payload[key])
    if "excluded_models" in payload:
        if not isinstance(payload["excluded_models"], list):
            raise ValueError("'excluded_models' must be a list.")
        merged["excluded_models"] = payload["excluded_models"]
    if "custom_models" in payload:
        if not isinstance(payload["custom_models"], list):
            raise ValueError("'custom_models' must be a list.")
        merged["custom_models"] = payload["custom_models"]
    if "presets" in payload:
        if not isinstance(payload["presets"], dict):
            raise ValueError("'presets' must be an object.")
        merged["presets"] = payload["presets"]

    # Validate explicit enum fields before normalizing so invalid inputs fail loudly
    raw_defaults = merged.get("defaults") or {}
    if raw_defaults.get("effort") not in (None, ""):
        eff = str(raw_defaults["effort"]).strip()
        if not EFFORT_RE.match(eff):
            raise ValueError(f"Invalid default effort: {eff!r}")
    if raw_defaults.get("config_mode") not in (None, "", "isolated", "profile", "inherit"):
        raise ValueError(f"Invalid config_mode: {raw_defaults.get('config_mode')!r}")
    if raw_defaults.get("mode") not in (None, "", "write", "read-only"):
        raise ValueError(f"Invalid mode: {raw_defaults.get('mode')!r}")

    raw_mapping = merged.get("mapping") or {}
    if raw_mapping.get("catalog_mode") not in (None, "", "auto", "custom", "off"):
        raise ValueError(f"Invalid catalog_mode: {raw_mapping.get('catalog_mode')!r}")
    if raw_mapping.get("effort_policy") not in (None, "", "clamp_with_warning", "passthrough"):
        raise ValueError(f"Invalid effort_policy: {raw_mapping.get('effort_policy')!r}")
    if raw_mapping.get("context_window_policy") not in (
        None,
        "",
        "same_model_only",
        "always_forward",
        "never_forward",
    ):
        raise ValueError(
            f"Invalid context_window_policy: {raw_mapping.get('context_window_policy')!r}"
        )
    for field_key, label in (
        ("extra_whitelist_keys", "extra whitelist key"),
        ("forwarded_mcp_servers", "forwarded_mcp_servers entry"),
    ):
        for item_k in raw_mapping.get(field_key) or []:
            if not isinstance(item_k, str) or not EXTRA_KEY_RE.match(item_k.strip()):
                raise ValueError(f"Invalid {label}: {item_k!r}")

    normalized = _normalize_workbench_config(merged)
    ddir = get_plugin_data_dir(data_dir)
    atomic_write_text(
        ddir / "workbench-config.json",
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
    )
    return normalized


def parse_catalog_file(path: Path, source_label: str) -> tuple[list[dict[str, Any]], str | None]:
    """Parse a Codex model catalog or `models_cache.json` file."""
    if not path.is_file():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"Failed to parse {path}: {exc}"

    raw_models: Any
    if isinstance(raw, dict):
        raw_models = raw.get("models")
        if raw_models is None and isinstance(raw.get("data"), list):
            raw_models = raw.get("data")
    elif isinstance(raw, list):
        raw_models = raw
    else:
        return [], f"Unexpected catalog structure in {path}"

    if not isinstance(raw_models, list):
        return [], f"Missing 'models' list in {path}"

    models: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        slug = _clean_optional_str(item.get("slug") or item.get("id"))
        if not slug:
            continue
        vis = _clean_optional_str(item.get("visibility")) or "list"
        cw = item.get("context_window")
        cw_int = int(cw) if isinstance(cw, (int, float)) and int(cw) > 0 else None
        def_eff = _clean_optional_str(item.get("default_reasoning_level"))
        if def_eff and not EFFORT_RE.match(def_eff):
            def_eff = None
        models.append(
            {
                "slug": slug,
                "display_name": _clean_optional_str(item.get("display_name")) or slug,
                "visibility": vis,
                "provider": _clean_optional_str(item.get("provider")),
                "default_reasoning_level": def_eff,
                "supported_reasoning_levels": _normalize_efforts_list(
                    item.get("supported_reasoning_levels")
                ),
                "context_window": cw_int,
                "tool_mode": _clean_optional_str(item.get("tool_mode")),
                "supports_search_tool": item.get("supports_search_tool")
                if isinstance(item.get("supports_search_tool"), bool)
                else None,
                "source": source_label,
            }
        )
    return models, None


def resolve_catalog_path(
    codex_home: Path,
    base_cfg: dict[str, Any],
    worker_cfg: dict[str, Any],
    wb_cfg: dict[str, Any],
) -> tuple[Path | None, str | None, list[str]]:
    """Resolve active `model_catalog_json` path per `mapping.catalog_mode`."""
    warnings: list[str] = []
    mapping = wb_cfg.get("mapping") or {}
    cat_mode = mapping.get("catalog_mode") or "auto"

    if cat_mode == "off":
        return None, None, warnings

    raw_path: str | None = None
    if cat_mode == "custom":
        raw_path = _clean_optional_str(mapping.get("catalog_path_override"))
        if not raw_path:
            warnings.append("catalog_mode is 'custom' but catalog_path_override is empty.")
            return None, None, warnings
    else:
        raw_path = _clean_optional_str(worker_cfg.get("model_catalog_json")) or _clean_optional_str(
            base_cfg.get("model_catalog_json")
        )
        if not raw_path:
            return None, None, warnings

    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = (codex_home / p).resolve()
    else:
        p = p.resolve()

    if not p.is_file():
        warnings.append(f"Configured model_catalog_json does not exist: {p}")
        return None, raw_path, warnings

    return p, raw_path, warnings


def _collect_providers(
    base_cfg: dict[str, Any],
    worker_cfg: dict[str, Any],
    active_provider: str | None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for cfg in (base_cfg, worker_cfg):
        provs = cfg.get("model_providers")
        if not isinstance(provs, dict):
            continue
        for pid, pdata in provs.items():
            if not isinstance(pid, str) or not isinstance(pdata, dict):
                continue
            entry = merged.setdefault(pid, {})
            entry.update(pdata)

    out: list[dict[str, Any]] = []
    for pid, pdata in merged.items():
        has_token = bool(
            _clean_optional_str(pdata.get("experimental_bearer_token"))
            or _clean_optional_str(pdata.get("env_key"))
            or _clean_optional_str(pdata.get("api_key"))
        )
        out.append(
            {
                "id": pid,
                "name": _clean_optional_str(pdata.get("name")) or pid,
                "base_url": _clean_optional_str(pdata.get("base_url")),
                "wire_api": _clean_optional_str(pdata.get("wire_api")),
                "requires_openai_auth": bool(pdata.get("requires_openai_auth", False)),
                "env_key": _clean_optional_str(pdata.get("env_key")),
                "has_auth_token": has_token,
                "is_builtin": False,
            }
        )

    if not out or (active_provider == "openai" and "openai" not in merged):
        out.append(
            {
                "id": "openai",
                "name": "OpenAI (Built-in)",
                "base_url": None,
                "wire_api": "responses",
                "requires_openai_auth": True,
                "env_key": None,
                "has_auth_token": False,
                "is_builtin": True,
            }
        )
    return out


def _collect_mcp_servers(
    base_cfg: dict[str, Any],
    worker_cfg: dict[str, Any],
    forwarded_ids: list[str],
) -> list[dict[str, Any]]:
    """Collect configured `[mcp_servers.*]` entries without exposing env/headers secrets."""
    merged: dict[str, dict[str, Any]] = {}
    for cfg in (base_cfg, worker_cfg):
        mcps = cfg.get("mcp_servers")
        if not isinstance(mcps, dict):
            continue
        for mid, mdata in mcps.items():
            if not isinstance(mid, str) or not isinstance(mdata, dict):
                continue
            entry = merged.setdefault(mid, {})
            entry.update(mdata)

    forwarded_set = set(forwarded_ids or [])
    out: list[dict[str, Any]] = []
    for mid, mdata in merged.items():
        url = _clean_optional_str(mdata.get("url"))
        out.append(
            {
                "id": mid,
                "type": _clean_optional_str(mdata.get("type")) or ("http" if url else "stdio"),
                "command": _clean_optional_str(mdata.get("command")),
                "url": url,
                "enabled": bool(mdata.get("enabled", True)),
                "forwarded": mid in forwarded_set,
            }
        )
    return out


def _collect_all_models(
    codex_home: Path,
    base_cfg: dict[str, Any],
    worker_cfg: dict[str, Any],
    wb_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    warnings: list[str] = []
    catalog_path, _, cat_warnings = resolve_catalog_path(codex_home, base_cfg, worker_cfg, wb_cfg)
    warnings.extend(cat_warnings)

    catalog_models: list[dict[str, Any]] = []
    catalog_loaded = False
    if catalog_path is not None:
        catalog_models, cat_err = parse_catalog_file(catalog_path, "catalog")
        if cat_err:
            warnings.append(cat_err)
        else:
            catalog_loaded = True

    cache_path = codex_home / "models_cache.json"
    cache_models: list[dict[str, Any]] = []
    models_cache_loaded = False
    if cache_path.is_file():
        cache_models, cache_err = parse_catalog_file(cache_path, "models_cache")
        if cache_err:
            warnings.append(cache_err)
        else:
            models_cache_loaded = True

    custom_models = [
        {**m, "source": "custom"} for m in (wb_cfg.get("custom_models") or []) if isinstance(m, dict)
    ]

    by_slug: dict[str, dict[str, Any]] = {}
    # Lowest precedence inserted first, overwritten by higher precedence
    for m in cache_models:
        by_slug[m["slug"]] = m
    for m in catalog_models:
        by_slug[m["slug"]] = m
    for m in custom_models:
        by_slug[m["slug"]] = m

    for cfg_src in (base_cfg, worker_cfg):
        cfg_model = _clean_optional_str(cfg_src.get("model"))
        if cfg_model and cfg_model not in by_slug:
            by_slug[cfg_model] = {
                "slug": cfg_model,
                "display_name": cfg_model,
                "visibility": "list",
                "provider": _clean_optional_str(cfg_src.get("model_provider")),
                "default_reasoning_level": _clean_optional_str(cfg_src.get("model_reasoning_effort")),
                "supported_reasoning_levels": [],
                "context_window": (
                    int(cfg_src["model_context_window"])
                    if isinstance(cfg_src.get("model_context_window"), (int, float))
                    else None
                ),
                "source": "config",
            }

    excluded_set = set(wb_cfg.get("excluded_models") or [])
    excl_orch = bool(wb_cfg.get("exclude_orchestrator_models", True))
    all_models: list[dict[str, Any]] = []
    for entry in by_slug.values():
        enriched = dict(entry)
        if "tool_mode" in entry:
            enriched["tool_mode"] = _clean_optional_str(entry.get("tool_mode"))
        if "supports_search_tool" in entry:
            ss = entry.get("supports_search_tool")
            enriched["supports_search_tool"] = ss if isinstance(ss, bool) else None
        orch_blocked = excl_orch and is_orchestrator_model(
            entry.get("slug"), entry.get("display_name")
        )
        enriched["orchestrator_excluded"] = orch_blocked
        enriched["excluded"] = (entry["slug"] in excluded_set) or orch_blocked
        all_models.append(enriched)

    meta = {
        "catalog_path": str(catalog_path) if catalog_path else None,
        "catalog_loaded": catalog_loaded,
        "models_cache_loaded": models_cache_loaded,
        "warnings": warnings,
    }
    return all_models, meta


def discover_models_and_config(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    query: str = "",
    provider: str = "",
    include_hidden: bool = False,
    include_excluded: bool = True,
) -> dict[str, Any]:
    """Discover models, providers, effective defaults, and workbench presets cleanly."""
    chome = get_codex_home(codex_home)
    ddir = get_plugin_data_dir(data_dir)

    base_cfg, base_err = load_toml_file(chome / "config.toml")
    worker_cfg, worker_err = load_toml_file(chome / "worker.config.toml")
    wb_cfg = load_workbench_config(ddir)

    warnings: list[str] = []
    if base_err:
        warnings.append(base_err)
    if worker_err:
        warnings.append(worker_err)

    all_models, src_meta = _collect_all_models(chome, base_cfg, worker_cfg, wb_cfg)
    warnings.extend(src_meta["warnings"])

    resolved = resolve_effective_run_config(
        chome, ddir, enforce_orchestrator_guard=False
    )
    warnings.extend(
        w for w in resolved.get("warnings") or [] if w not in warnings
    )
    effective_defaults = resolved["effective"]
    providers = _collect_providers(base_cfg, worker_cfg, effective_defaults.get("provider"))
    mcp_servers = _collect_mcp_servers(
        base_cfg, worker_cfg, (wb_cfg.get("mapping") or {}).get("forwarded_mcp_servers") or []
    )

    visible_count = sum(1 for m in all_models if m.get("visibility") != "hide")
    hidden_count = len(all_models) - visible_count

    q = query.strip().lower()
    prov_filter = provider.strip()

    filtered: list[dict[str, Any]] = []
    for m in all_models:
        if not include_hidden and m.get("visibility") == "hide":
            continue
        if not include_excluded and m.get("excluded"):
            continue
        if prov_filter and m.get("provider") not in (None, prov_filter):
            continue
        if q and q not in m["slug"].lower() and q not in m["display_name"].lower():
            continue
        filtered.append(m)

    return {
        "enabled": bool(wb_cfg.get("enabled", True)),
        "exclude_orchestrator_models": bool(wb_cfg.get("exclude_orchestrator_models", True)),
        "retention_hours": int((wb_cfg.get("mapping") or {}).get("retention_hours", 24)),
        "models": filtered,
        "all_models_count": len(all_models),
        "visible_models_count": visible_count,
        "hidden_models_count": hidden_count,
        "excluded_models": wb_cfg.get("excluded_models", []),
        "providers": providers,
        "mcp_servers": mcp_servers,
        "presets": wb_cfg.get("presets", {}),
        "max_concurrent_runs": (wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5),
        "catalog_mode": (wb_cfg.get("mapping") or {}).get("catalog_mode", "auto"),
        "catalog_path": src_meta["catalog_path"],
        "catalog_loaded": src_meta["catalog_loaded"],
        "models_cache_loaded": src_meta["models_cache_loaded"],
        "effective_defaults": effective_defaults,
        "workbench_config": wb_cfg,
        "standard_efforts": list(EFFORT_RANK),
        "warnings": warnings,
    }


def _clamp_effort(
    effort: str,
    supported: list[str],
    default_level: str | None,
) -> tuple[str, str | None]:
    """Clamp effort to nearest supported level when declared."""
    if not supported or effort in supported:
        return effort, None

    if effort not in EFFORT_RANK:
        fallback = default_level if (default_level and default_level in supported) else supported[0]
        return fallback, f"effort clamped: {effort} -> {fallback} (unsupported custom effort)"

    req_idx = EFFORT_RANK.index(effort)
    ranked_supported = [(EFFORT_RANK.index(lv), lv) for lv in supported if lv in EFFORT_RANK]
    if not ranked_supported:
        fallback = default_level if (default_level and default_level in supported) else supported[0]
        return fallback, f"effort clamped: {effort} -> {fallback}"

    ranked_supported.sort(key=lambda pair: pair[0])
    lower_or_eq = [lv for idx, lv in ranked_supported if idx <= req_idx]
    if lower_or_eq:
        chosen = lower_or_eq[-1]
    else:
        chosen = ranked_supported[0][1]
    return chosen, f"effort clamped: {effort} -> {chosen} (model supports {', '.join(supported)})"


def resolve_effective_run_config(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_effort: str | None = None,
    preset: str | None = None,
    cli_mode: str | None = None,
    cli_config_mode: str | None = None,
    enforce_orchestrator_guard: bool = True,
) -> dict[str, Any]:
    """Resolve effective `(model, provider, effort, mode, config_mode)` across 5 precedence layers."""
    chome = get_codex_home(codex_home)
    ddir = get_plugin_data_dir(data_dir)
    base_cfg, base_err = load_toml_file(chome / "config.toml")
    worker_cfg, worker_err = load_toml_file(chome / "worker.config.toml")
    wb_cfg = load_workbench_config(ddir)

    warnings: list[str] = []
    if base_err:
        raise RuntimeError(base_err)
    if worker_err:
        raise RuntimeError(worker_err)

    all_models, src_meta = _collect_all_models(chome, base_cfg, worker_cfg, wb_cfg)
    warnings.extend(src_meta["warnings"])
    models_by_slug = {m["slug"]: m for m in all_models}

    presets = wb_cfg.get("presets") or {}
    preset_name = _clean_optional_str(preset)
    preset_obj: dict[str, Any] = {}
    if preset_name:
        if preset_name not in presets:
            raise ValueError(f"Unknown preset: {preset_name!r}")
        preset_obj = presets[preset_name]

    wb_defaults = wb_cfg.get("defaults") or {}

    def pick(*candidates: Any) -> str | None:
        for cand in candidates:
            s = _clean_optional_str(cand)
            if s is not None:
                return s
        return None

    req_model = _clean_optional_str(cli_model)
    req_provider = _clean_optional_str(cli_provider)
    req_effort = _clean_optional_str(cli_effort)

    if req_effort and not EFFORT_RE.match(req_effort):
        raise ValueError(f"Invalid reasoning effort: {req_effort!r}")

    eff_model = pick(
        req_model,
        preset_obj.get("model"),
        wb_defaults.get("model"),
        worker_cfg.get("model"),
        base_cfg.get("model"),
    )
    explicit_provider = pick(
        req_provider,
        preset_obj.get("provider"),
        wb_defaults.get("provider"),
    )
    eff_provider = pick(
        explicit_provider,
        worker_cfg.get("model_provider"),
        base_cfg.get("model_provider"),
    )
    eff_effort = pick(
        req_effort,
        preset_obj.get("effort"),
        wb_defaults.get("effort"),
        worker_cfg.get("model_reasoning_effort"),
        base_cfg.get("model_reasoning_effort"),
    )
    if eff_effort and not EFFORT_RE.match(eff_effort):
        raise ValueError(f"Invalid reasoning effort: {eff_effort!r}")

    eff_mode = pick(cli_mode, preset_obj.get("mode"), wb_defaults.get("mode"), "write") or "write"
    eff_config_mode = (
        pick(cli_config_mode, wb_defaults.get("config_mode"), "isolated") or "isolated"
    )

    matched_model = models_by_slug.get(eff_model) if eff_model else None
    if bool(wb_cfg.get("exclude_orchestrator_models", True)) and eff_model and is_orchestrator_model(
        eff_model, (matched_model or {}).get("display_name", "")
    ):
        if enforce_orchestrator_guard:
            raise ValueError(
                f"Model {eff_model!r} is excluded by exclude_orchestrator_models guard. "
                "Choose a non-orchestrator Codex model or disable exclude_orchestrator_models."
            )
        warnings.append(
            f"Model {eff_model!r} is excluded by exclude_orchestrator_models guard."
        )
    if matched_model:
        # Auto-select model's declared provider when neither caller nor preset/workbench pinned one
        if matched_model.get("provider") and not explicit_provider:
            eff_provider = matched_model["provider"]
        if matched_model.get("excluded"):
            warnings.append(
                f"Model {eff_model!r} is in excluded_models list; running only because explicitly resolved."
            )
    elif eff_model and eff_model in set(wb_cfg.get("excluded_models") or []):
        warnings.append(
            f"Model {eff_model!r} is in excluded_models list; running only because explicitly resolved."
        )

    original_effort = eff_effort
    mapping = wb_cfg.get("mapping") or {}
    effort_policy = mapping.get("effort_policy") or "clamp_with_warning"
    if eff_effort and matched_model and effort_policy == "clamp_with_warning":
        supported = matched_model.get("supported_reasoning_levels") or []
        if supported:
            clamped, clamp_warn = _clamp_effort(
                eff_effort, supported, matched_model.get("default_reasoning_level")
            )
            eff_effort = clamped
            if clamp_warn:
                warnings.append(clamp_warn)

    return {
        "requested": {
            "model": req_model,
            "provider": req_provider,
            "effort": req_effort,
            "preset": preset_name,
        },
        "effective": {
            "model": eff_model,
            "provider": eff_provider,
            "effort": eff_effort,
            "original_effort": original_effort,
            "mode": eff_mode,
            "config_mode": eff_config_mode,
            "preset": preset_name,
        },
        "mode": eff_mode,
        "config_mode": eff_config_mode,
        "preset": preset_name,
        "catalog_path": src_meta["catalog_path"],
        "matched_model": matched_model,
        "base_cfg": base_cfg,
        "worker_cfg": worker_cfg,
        "wb_cfg": wb_cfg,
        "warnings": warnings,
    }


def collect_isolated_overrides(
    codex_home: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_effort: str | None = None,
    preset: str | None = None,
    cli_mode: str | None = None,
    cli_config_mode: str | None = None,
) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    """Build whitelisted `(dotted_key, scalar_value)` overrides for `--ignore-user-config`."""
    resolved = resolve_effective_run_config(
        codex_home,
        data_dir,
        cli_model=cli_model,
        cli_provider=cli_provider,
        cli_effort=cli_effort,
        preset=preset,
        cli_mode=cli_mode,
        cli_config_mode=cli_config_mode,
    )
    base_cfg: dict[str, Any] = resolved["base_cfg"]
    worker_cfg: dict[str, Any] = resolved["worker_cfg"]
    wb_cfg: dict[str, Any] = resolved["wb_cfg"]
    mapping: dict[str, Any] = wb_cfg.get("mapping") or {}
    eff: dict[str, Any] = resolved["effective"]
    warnings: list[str] = list(resolved["warnings"])

    overrides: dict[str, Any] = {}

    if eff.get("model"):
        overrides["model"] = eff["model"]
    if eff.get("provider"):
        overrides["model_provider"] = eff["provider"]
    if eff.get("effort"):
        overrides["model_reasoning_effort"] = eff["effort"]

    # Scalar preferences whitelisted from config.toml -> worker.config.toml
    for key in (
        "model_reasoning_summary",
        "service_tier",
        "personality",
        "tool_output_token_limit",
        "allow_login_shell",
    ):
        for src in (base_cfg, worker_cfg):
            val = src.get(key)
            if isinstance(val, (str, bool, int, float)):
                overrides[key] = val

    # Windows & workspace-write sandbox settings needed for --ignore-user-config execution
    for section in ("windows", "sandbox_workspace_write"):
        for src in (base_cfg, worker_cfg):
            tbl = src.get(section)
            if isinstance(tbl, dict):
                for s_key, s_val in tbl.items():
                    if isinstance(s_key, str) and isinstance(s_val, (str, bool, int, float)):
                        overrides[f"{section}.{to_toml_key(s_key)}"] = s_val

    # Context window policy
    cw_policy = mapping.get("context_window_policy") or "same_model_only"
    base_default_model = _clean_optional_str(base_cfg.get("model"))
    should_forward_cw = (
        cw_policy == "always_forward"
        or (
            cw_policy == "same_model_only"
            and eff.get("model") is not None
            and eff.get("model") == base_default_model
        )
    )
    if should_forward_cw:
        for key in ("model_context_window", "model_auto_compact_token_limit"):
            for src in (base_cfg, worker_cfg):
                val = src.get(key)
                if isinstance(val, (int, float)) and int(val) > 0:
                    overrides[key] = int(val)

    # Resolved model_catalog_json (absolute path when present and not off)
    if resolved["catalog_path"]:
        overrides["model_catalog_json"] = resolved["catalog_path"]

    # Safe non-interactive worker defaults
    overrides["approval_policy"] = "never"
    overrides["suppress_unstable_features_warning"] = True

    allow_nested = bool(mapping.get("allow_nested_subagents", False))
    if not allow_nested:
        overrides["agents.enabled"] = False
        overrides["features.multi_agent"] = False

    overrides["developer_instructions"] = build_worker_developer_instructions(
        worker_cfg, allow_nested=allow_nested
    )

    # Extra user-specified top-level scalar whitelist keys
    for extra_key in mapping.get("extra_whitelist_keys") or []:
        if not EXTRA_KEY_RE.match(extra_key):
            continue
        for src in (base_cfg, worker_cfg):
            val = src.get(extra_key)
            if isinstance(val, (str, bool, int, float)):
                overrides[extra_key] = val

    # Active model provider table (scalars + 1-level string maps)
    active_provider = eff.get("provider")
    provider_keys: list[str] = []
    if isinstance(active_provider, str) and active_provider:
        merged_provider: dict[str, Any] = {}
        for src in (base_cfg, worker_cfg):
            provs = src.get("model_providers")
            if isinstance(provs, dict) and isinstance(provs.get(active_provider), dict):
                merged_provider.update(provs[active_provider])

        p_prefix = f"model_providers.{to_toml_key(active_provider)}"
        for p_key, p_val in merged_provider.items():
            if not isinstance(p_key, str):
                continue
            if isinstance(p_val, (str, bool, int, float)):
                dotted = f"{p_prefix}.{to_toml_key(p_key)}"
                overrides[dotted] = p_val
                provider_keys.append(p_key)
            elif isinstance(p_val, dict) and p_key in ("http_headers", "env_http_headers", "query_params"):
                for sub_k, sub_v in p_val.items():
                    if isinstance(sub_k, str) and isinstance(sub_v, (str, bool, int, float)):
                        dotted = f"{p_prefix}.{to_toml_key(p_key)}.{to_toml_key(sub_k)}"
                        overrides[dotted] = sub_v
                        provider_keys.append(f"{p_key}.{sub_k}")

    # Shell environment policy (inherit + proxy/env `set` scalars)
    env_policy_keys: list[str] = []
    for src in (base_cfg, worker_cfg):
        policy = src.get("shell_environment_policy")
        if not isinstance(policy, dict):
            continue
        inherit = policy.get("inherit")
        if isinstance(inherit, str):
            overrides["shell_environment_policy.inherit"] = inherit
        env_set = policy.get("set")
        if isinstance(env_set, dict):
            for e_key, e_val in env_set.items():
                if isinstance(e_key, str) and isinstance(e_val, (str, bool, int, float)):
                    overrides[f"shell_environment_policy.set.{to_toml_key(e_key)}"] = e_val
                    if e_key not in env_policy_keys:
                        env_policy_keys.append(e_key)

    # Selectively forwarded [mcp_servers.*] entries (opt-in; empty by default)
    forwarded_mcps = mapping.get("forwarded_mcp_servers") or []
    if forwarded_mcps:
        merged_mcps: dict[str, dict[str, Any]] = {}
        for src in (base_cfg, worker_cfg):
            mcps = src.get("mcp_servers")
            if isinstance(mcps, dict):
                for mid, mdata in mcps.items():
                    if isinstance(mid, str) and isinstance(mdata, dict):
                        merged_mcps.setdefault(mid, {}).update(mdata)

        active_forwarded_mcps: list[str] = []
        for mid in forwarded_mcps:
            mdata = merged_mcps.get(mid)
            if not isinstance(mdata, dict):
                warnings.append(f"forwarded_mcp_servers entry {mid!r} not found in config.toml")
                continue
            if mdata.get("enabled") is False:
                continue
            active_forwarded_mcps.append(mid)
            m_prefix = f"mcp_servers.{to_toml_key(mid)}"
            for m_key in (
                "command",
                "url",
                "cwd",
                "enabled",
                "startup_timeout_sec",
                "tool_timeout_sec",
                "bearer_token_env_var",
                "default_tools_approval_mode",
            ):
                m_val = mdata.get(m_key)
                if isinstance(m_val, (str, bool, int, float)):
                    overrides[f"{m_prefix}.{to_toml_key(m_key)}"] = m_val
            for m_list_key in ("args", "env_vars", "enabled_tools", "disabled_tools"):
                m_list = mdata.get(m_list_key)
                if isinstance(m_list, list) and all(
                    isinstance(x, (str, bool, int, float)) for x in m_list
                ):
                    overrides[f"{m_prefix}.{to_toml_key(m_list_key)}"] = list(m_list)
            for m_map_key in ("env", "http_headers", "env_http_headers"):
                m_map = mdata.get(m_map_key)
                if isinstance(m_map, dict):
                    for sub_k, sub_v in m_map.items():
                        if isinstance(sub_k, str) and isinstance(sub_v, (str, bool, int, float)):
                            overrides[
                                f"{m_prefix}.{to_toml_key(m_map_key)}.{to_toml_key(sub_k)}"
                            ] = sub_v
        if active_forwarded_mcps:
            overrides["features.code_mode.direct_only_tool_namespaces"] = [
                f"mcp__{mid}" for mid in active_forwarded_mcps
            ]

    redacted_overrides = {
        k: redact_value_for_key(k, v)
        for k, v in overrides.items()
    }
    redacted_keys = [k for k in overrides if is_sensitive_key(k)]

    meta = {
        "model": eff.get("model"),
        "provider": active_provider,
        "effort": eff.get("effort"),
        "original_effort": eff.get("original_effort"),
        "requested": resolved["requested"],
        "effective": eff,
        "catalog_path": resolved["catalog_path"],
        "provider_keys": provider_keys,
        "env_set_keys": env_policy_keys,
        "whitelisted_keys": list(overrides.keys()),
        "redacted_keys": redacted_keys,
        "redacted_overrides": redacted_overrides,
        "warnings": warnings,
    }
    return list(overrides.items()), meta
