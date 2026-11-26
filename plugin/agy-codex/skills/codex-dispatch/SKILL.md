---
name: codex-dispatch
description: >-
  Delegate bounded coding, debugging, exploration, or refactoring tasks to any specified
  model in local OpenAI Codex CLI via Agy Codex MCP tools (`list_codex_models`,
  `start_codex_task`, `get_codex_task_status`, `wait_codex_task`, `stop_codex_task`).
  Trigger whenever delegating single or sequential bounded tasks to Codex / GPT / custom models.
---

# Codex Dispatch（单任务精准委派与模型路由）

让 **Antigravity** 担任架构规划、任务拆解、Code Review 与最终验收者，将有界执行任务精准派给本机 **Codex CLI** 中指定的模型与推理档位。

## 何时派给 Codex vs Antigravity 亲自处理

- **派给 Codex**：边界清晰的模块实现、样板/脚本生成、有明确复现步骤的 Bug 修复、独立只读代码调研，或需要跨模型视角验证的任务。
- **Antigravity 亲自做**：1–2 行的极小修改、需要浏览器交互或用户多轮澄清的流程、全局架构决策与最终 `git diff` 审查验收。

## 标准派发流程

### 1. 模型与预设发现（首次或不确定时必做）
调用 `list_codex_models(query=..., provider=...)` 获取：
- 可用模型（含 `custom_models`、`model_catalog_json`、`models_cache.json` 与 `config.toml`）、支持的 `supported_reasoning_levels` 与 `context_window`；
- 已脱敏的 `providers`、`presets`（默认包含 `explore` / `coding` / `frontend` / `review` / `debug` / `fast`，含各自 `when_to_use`）与 `excluded_models`（不要主动把已排除模型当默认值或回退候选）。

### 2. 选型与五段式自包含任务契约
在 `prompt` 中不要只丢一句话，使用精炼的五段式契约让 Codex 一次性独立跑通：

```text
任务：<明确交付物与可验收目标>
范围：<允许修改的文件/目录；禁止修改的边界；工作区模式>
选型：<选用的 preset 或 model/provider/effort 及一句理由>
边界：<是否允许子线程；勿提出交互式反问>
验证：<完成后必须执行的测试/静态检查/构建命令>
```

### 3. 启动、监控与收尾
1. 调用 `start_codex_task(prompt=..., cwd=..., preset=..., model=..., provider=..., effort=..., mode=...)`：
   - 对话中会立即渲染可折叠的 Codex 实时过程卡片，并返回 `run_id`、`requested`、`effective` 与可能的 `warnings`（如档位自动降级提醒）。
2. 主 Agent 可先推进自己的只读分析或其他不冲突工作；在真正依赖结果时调用 `wait_codex_task(run_id=..., timeout=...)`（或先用 `get_codex_task_status` 非阻塞探活）。
3. **等待超时 ≠ 失败**：如果 `wait_codex_task` 返回 `wait_timed_out=true`，任务仍在运行中，请继续用 `get_codex_task_status` / `wait_codex_task` 跟踪，或确认跑偏时调用 `stop_codex_task(run_id)`，**禁止重复启动同一任务造成写冲突**。

## 失败归因与控制纪律（防死循环）

1. 若 `ok=false` 或测试未通过，先读取 `error` 与 `log_file` 定位根因（范围不清 / 缺上下文 / 沙箱限制 / 模型能力不足）：
   - **明确属于模型能力或档位不足时**：直接切换更强合规模型或提高 `effort` 重派（若原任务仍在跑，先 `stop_codex_task`），不要在弱档位反复重试。
   - 若上下文和方向正确仅差收尾修补，启动时传 `persist=true`，再用 `session_id` 续聊迭代，**最多 `resume` 2 轮**；仍未解决则由主 Agent 亲自接管。
2. **零盲信验收门禁**：无论 Codex 自报 `status: "done"` 与否，主 Agent 返回前必须核对 `changed_files`、`git diff` 与关键测试输出，由主 Agent 统一整合答复用户。
