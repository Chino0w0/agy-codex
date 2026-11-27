#!/usr/bin/env python3
"""Antigravity lifecycle routing hook for Agy Codex (`SessionStart` / `PreToolUse`).

Reads workbench configuration (`enabled`, `intercept_native_subagents`,
`exclude_orchestrator_models`, `native_passthrough_subagents`) and checks fast local
Codex CLI readiness (zero subprocess calls) so general subagent delegations
default to `agy-codex` MCP when enabled and fail open to native Antigravity
subagents when disabled, overridden by the user, or not yet configured.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import codex_catalog  # noqa: E402
import codex_run  # noqa: E402

BUILTIN_PASSTHROUGH_SUBAGENTS: frozenset[str] = frozenset(
    {
        "browser",
        "image-generator",
        "flutter_a11y_agent",
        "bundle-scrubber",
        "session-analyst",
    }
)

_NATIVE_TARGET = r"(?:(?:(?:the\s+)?Antigravity\s+)?native\s+subagents?|invoke_subagent)\b"
_CHINESE_NATIVE_TARGET = r"(?:Antigravity\s*)?原生(?:子代理|子\s*agent|agent)?"
USER_NATIVE_OVERRIDE_RE = re.compile(
    # 先吞掉否定指令，避免其中的肯定子串作为单独指令命中。
    r"\b(?:do\s+not|don['’]t|not|never)\s+(?:use|call|invoke|choose)\s+" + _NATIVE_TARGET
    + r"|(?:不要|别|勿|不|禁止)\s*(?:使用|用|调用|选择)\s*" + _CHINESE_NATIVE_TARGET
    + r"|(?P<override>"
    + r"(?:不|别|勿|不要|停止|关闭|禁用|暂停)\s*(?:使用|用)?\s*codex\b"
    + r"|(?:使用|用|调用)\s*" + _CHINESE_NATIVE_TARGET
    + r"|\b(?:use|call|invoke|choose)\s+" + _NATIVE_TARGET
    + r"|\b(?:bypass|without)\s+codex\b)",
    re.IGNORECASE,
)


def resolve_hook_data_dir() -> Path:
    """Resolve persistent plugin data directory (`PLUGIN_DATA` first)."""
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data and plugin_data.strip():
        return Path(plugin_data).expanduser()
    return codex_catalog.get_plugin_data_dir()


def _provider_has_auth(prov: dict[str, Any]) -> bool:
    if prov.get("requires_openai_auth") is False:
        return True
    for key in ("env_key", "experimental_bearer_token", "bearer_token", "base_url"):
        val = prov.get(key)
        if isinstance(val, str) and val.strip():
            return True
    return False


def check_codex_readiness(
    codex_home: Path,
    data_dir: Path,
    *,
    workbench_config: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Check binary, credentials, and compliant default model without spawning processes."""
    try:
        codex_run.find_codex_bin()
    except FileNotFoundError:
        return False, "未检测到本地 codex CLI 可执行文件", None

    try:
        resolved = codex_catalog.resolve_effective_run_config(
            codex_home, data_dir, workbench_config=workbench_config
        )
    except (RuntimeError, ValueError):
        return False, "无法解析 Codex 默认配置", None

    auth_file = codex_home / "auth.json"
    auth_ready = auth_file.is_file() and auth_file.stat().st_size > 0
    env_ready = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    providers = resolved["base_cfg"].get("model_providers")
    provider_ready = isinstance(providers, dict) and any(
        isinstance(pval, dict) and _provider_has_auth(pval) for pval in providers.values()
    )

    if not (auth_ready or env_ready or provider_ready):
        return False, "尚未检测到 Codex 登录凭据或自定义 Provider 配置", None

    return True, None, resolved["effective"]["model"]


def handle_session_start(
    payload: dict[str, Any],
    codex_home: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Build SessionStart systemMessage reflecting current live switch and readiness."""
    _ = payload
    wb_cfg = codex_catalog.load_workbench_config(data_dir)
    if not wb_cfg.get("enabled", True):
        return {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": (
                            "[Agy Codex 路由状态] Codex 联动开关当前为关闭 (enabled=false)。"
                            "请直接使用 Antigravity 原生子代理 (invoke_subagent) 执行子任务。"
                        )
                    }
                }
            ]
        }

    ready, reason, default_model = check_codex_readiness(
        codex_home, data_dir, workbench_config=wb_cfg
    )
    if not ready:
        return {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": (
                            f"[Agy Codex 就绪提醒] {reason}。在完成 `codex login` 或配置自定义 Provider 前，"
                            "通用子任务已自动放行 Antigravity 原生子代理 (invoke_subagent)，不阻塞正常使用。"
                        )
                    }
                }
            ]
        }

    intercept = bool(wb_cfg.get("intercept_native_subagents", True))
    if not intercept:
        return {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": (
                            "[Agy Codex 路由状态] Codex 联动已启用，默认接管开关为按需模式 "
                            "(intercept_native_subagents=false)。仅在需要 Codex 特定模型或显式指定时调用 `agy-codex` MCP。"
                        )
                    }
                }
            ]
        }

    max_conc = int((wb_cfg.get("mapping") or {}).get("max_concurrent_runs", 5))
    if default_model:
        model_hint = (
            f"当前合规默认模型为 `{default_model}`（并发槽位上限 {max_conc}），"
            "常规委派可直接调用 `start_codex_task(prompt=..., cwd=..., preset='coding|explore|frontend|review|debug|fast')`，"
            "无需先调 `list_codex_models` 或打断询问用户；`start_codex_task` 可省略 `timeout`，未配置时不限总时长，"
            "轮询等待用 `wait_codex_task(..., timeout=120)`（超时返回 `wait_timed_out=true` 时继续等待，勿重复派发）。"
        )
    else:
        model_hint = (
            "当前尚未绑定合规默认模型，首次委派前请先调用 `list_codex_models` 列出候选模型与档位让用户选定。"
        )

    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": (
                        "[Agy Codex 默认路由已激活] 联动开关已开启 (enabled=true, intercept_native_subagents=true)："
                        "凡通用子任务（编码实现、重构、排障、代码审查、代码调研、并行拆分）默认一律通过 `agy-codex` MCP "
                        "（`start_codex_task` -> `open_codex_panel(tab='subagents')` -> `wait_codex_task` -> `close_codex_task`）派发，"
                        "仅 `browser`、`image-generator` 等原生专有子代理或用户明确要求时才调用原生 `invoke_subagent`。"
                        + model_hint
                    )
                }
            }
        ]
    }


def _parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def handle_pre_tool_use(
    payload: dict[str, Any],
    codex_home: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Intercept general `invoke_subagent` calls when Codex default routing is active."""
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {"decision": "allow"}

    tool_name = str(tool_call.get("name") or "").strip().lower()
    if tool_name not in ("invoke_subagent", "subagent"):
        return {"decision": "allow"}

    wb_cfg = codex_catalog.load_workbench_config(data_dir)
    if not wb_cfg.get("enabled", True):
        return {"decision": "allow"}
    if not wb_cfg.get("intercept_native_subagents", True):
        return {"decision": "allow"}

    args = _parse_tool_args(tool_call.get("args"))
    raw_type = args.get("TypeName") or args.get("typeName") or args.get("type_name") or ""
    type_name = str(raw_type).strip().lower()

    extra_passthrough = {
        str(x).strip().lower()
        for x in ((wb_cfg.get("mapping") or {}).get("native_passthrough_subagents") or [])
        if str(x).strip()
    }
    passthrough = BUILTIN_PASSTHROUGH_SUBAGENTS | extra_passthrough
    if type_name and type_name in passthrough:
        return {"decision": "allow"}

    last_user_input = str(payload.get("lastUserInput") or "")
    if any(match.group("override") for match in USER_NATIVE_OVERRIDE_RE.finditer(last_user_input)):
        return {"decision": "allow"}

    ready, _, default_model = check_codex_readiness(
        codex_home, data_dir, workbench_config=wb_cfg
    )
    if not ready:
        return {"decision": "allow"}

    if default_model:
        model_guidance = f"（当前合规默认模型: {default_model}，可直接派发无需再问）"
    else:
        model_guidance = "（若未选定模型，请先调用 `list_codex_models` 列给用户选定）"

    sub_label = type_name or "general"
    return {
        "decision": "deny",
        "reason": (
            f"当前「Codex 联动」处于开启状态，通用子代理 `{sub_label}` 已由 Agy Codex 默认接管。"
            f"请改用 `agy-codex` MCP 的 `start_codex_task` 派发（可省略 `timeout`，未配置时不限总时长）{model_guidance}，并在紧接着调用 "
            "`open_codex_panel(tab=\"subagents\", run_id=...)` 展示进度，完成后通过 "
            "`wait_codex_task(..., timeout=120)` 验收并在结束时调用 `close_codex_task`。"
            "若用户需要改回原生子代理，可在插件顶部随时关闭「Codex 联动」开关。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cli_event = args[0] if args else ""

    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    event_name = cli_event
    try:
        raw_stdin = sys.stdin.read()
        payload = json.loads(raw_stdin) if raw_stdin and raw_stdin.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if not event_name:
            event_name = str(payload.get("hookEventName") or "")

        codex_home = codex_catalog.get_codex_home()
        data_dir = resolve_hook_data_dir()

        if event_name == "PreToolUse":
            result = handle_pre_tool_use(payload, codex_home, data_dir)
        elif event_name == "SessionStart":
            result = handle_session_start(payload, codex_home, data_dir)
        else:
            result = {}
    except Exception:
        result = {"decision": "allow"} if event_name == "PreToolUse" else {}

    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
