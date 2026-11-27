---
name: codex-dispatch
description: >-
  Delegate bounded coding, debugging, exploration, or refactoring tasks to any specified
  model in local OpenAI Codex CLI via Agy Codex MCP tools (`list_codex_models`,
  `start_codex_task`, `get_codex_task_status`, `wait_codex_task`, `stop_codex_task`,
  `close_codex_task`, `open_codex_panel`).
  Trigger whenever delegating single or sequential bounded tasks to Codex / GPT / custom models.
---

# Codex Dispatch（单任务精准委派与模型路由）

让 **Antigravity** 担任架构规划、任务拆解、Code Review 与最终验收者，将有界执行任务精准派给本机 **Codex CLI** 中指定的模型与推理档位。

## 何时派给 Codex vs Antigravity 亲自处理

- **派给 Codex**：边界清晰的模块实现、样板/脚本生成、有明确复现步骤的 Bug 修复、独立只读代码调研，或需要跨模型视角验证的任务。
- **Antigravity 亲自做**：1–2 行的极小修改、需要浏览器交互或用户多轮澄清的流程、全局架构决策与最终 `git diff` 审查验收。
- **可随时开关尊重用户**：当用户或工具返回 Codex 子代理已暂停（`enabled=false`）时，立刻改用 Antigravity 原生子 Agent 完成任务，不得强行绕过开关。

## 标准派发流程

### 1. 模型与预设发现（首次或不确定时必做）
调用 `list_codex_models(query=..., provider=...)` 获取：
- 可见模型（`visibility != "hide"`，含 `custom_models`、`model_catalog_json`、`models_cache.json` 与 `config.toml`）、支持的 `supported_reasoning_levels` 与 `context_window`；
- 已脱敏的 `providers`、`presets`（默认包含 `explore` / `coding` / `frontend` / `review` / `debug` / `fast`，含各自 `when_to_use`）与 `excluded_models`（不要主动把已排除模型当默认值或回退候选）。
- **Orchestrator 防嵌套排除**：遵循 `exclude_orchestrator_models=true`，绝不选择 Orchestrator 编排类模型作为 Codex 子代理目标。
- **用户选型优先**：如果用户在当前请求中没有明确指定具体模型，且所用 `preset` 与 `workbench_config.defaults.model` 也未绑定具体模型（均为 `null`），派发 `start_codex_task` 前先展示可见候选模型与支持档位让用户选定（或建议用「模型与配置」预设绑定），再按用户选择执行。

### 2. 单任务粒度、档位纪律与六段式自包含业务上下文

- **单任务粒度**：每个写子任务聚焦 **1 个有界交付物**（预计 2–5 分钟完成）；若需求跨越多个独立模块或阶段，先拆分成多个小任务依次或并行派发。
- **推理档位纪律**：常规编码任务默认使用 `medium` 或 `high`，仅将 `xhigh` 保留给复杂跨模块排障或疑难根因分析，避免无谓耗时与上下文膨胀。
- **严禁转述空洞口号**：禁止将 `"大厂级升级"`、`"全链路优化"` 等缺乏具体 UI/API 规格的模糊口号原样转发给 Codex；主 Agent 必须先消化需求与源码，再写出可执行契约。

在 `start_codex_task` 中结合结构化参数 `context_files`、`acceptance_criteria`、`non_goals`（以及 `title`、`role`、`conversation_id`、`conversation_title`），并在 `prompt` 中交付完整自包含的六段式业务上下文包：

```text
1. 业务背景与当前实现：<功能服务于哪条业务流、现有代码如何运行、需解决的具体问题>
2. 必读锚点文件：<通过 context_files 传入 2–5 个设计文档、共享 Schema 或同风格参考页>
3. 精确规格与接口契约：<接口路径、字段类型、状态流转、文案/阈值常量>
4. 改动范围与明确不做：<白名单文件/目录；与 non_goals 一致的禁止边界>
5. 反脑补熔断：<遇业务规则不明或源码与契约冲突，禁止猜想硬写，直接返回 status="blocked" 列明疑点>
6. 可机检验收标准：<与 acceptance_criteria 一致的检查项及必跑测试/构建/静态检查命令>
```

### 3. 启动、右侧监控与关闭归档
1. 调用 `start_codex_task(prompt=..., cwd=..., preset=..., model=..., provider=..., effort=..., mode=..., title=..., role=..., conversation_id=..., conversation_title=..., context_files=[...], acceptance_criteria=[...], non_goals=[...])` 立即拿到 `run_id`、`requested`、`effective` 与可能的 `warnings`（如档位自动降级或缺少业务上下文提醒）。
2. 紧接着调用一次 `open_codex_panel(tab="subagents", run_id=<run_id>)`（`tab` 支持 `"subagents" | "history" | "stats" | "models"`），在右侧边栏打开或刷新实时进度标签页（不在对话流里渲染内联卡片）。
3. 主 Agent 可先推进自己的只读分析或其他不冲突工作；在真正依赖结果时调用 `wait_codex_task(run_id=..., timeout=...)`（或先用 `get_codex_task_status` 非阻塞探活）。
4. **等待超时 ≠ 失败**：如果 `wait_codex_task` 返回 `wait_timed_out=true`，任务仍在运行中，请继续用 `get_codex_task_status` / `wait_codex_task` 跟踪，或确认跑偏时调用 `stop_codex_task(run_id)`，**禁止重复启动同一任务造成写冲突**。
5. **用完即销毁与关闭归档**：主 Agent 验收完 Codex 子任务结论与 `git diff` 后，调用 `close_codex_task(run_id)` 将其从右侧活跃标签栏移出，保留 24 小时日志于「历史对话」中按 Antigravity 大会话分组追溯；不要留一堆已完结标签占满侧栏。

## 失败归因与控制纪律（防死循环）

1. 若 `ok=false`、`status="partial"`、`status="blocked"` 或测试未通过，先读取 `error` 与 `log_file` 定位根因（范围不清 / 缺上下文 / 沙箱限制 / 模型能力不足）：
   - **将 `status="partial"` 视为未完成任务**：Codex 仅完成部分目标时绝不可当成 `done` 放行，主 Agent 必须审查已完成 diff 与剩余项，再决定补派精准续接任务或亲自收尾。
   - **`status="blocked"` 反脑补触发**：根据其列明的疑点补充规格契约或由主 Agent 亲自排查后重派。
   - **明确属于模型能力或档位不足时**：直接切换更强合规模型或提高 `effort` 重派（若原任务仍在跑，先 `stop_codex_task`），不要在弱档位反复重试。
   - 若上下文和方向正确仅差收尾修补，启动时传 `persist=true`，再用 `session_id` 续聊迭代，**最多 `resume` 2 轮**；仍未解决则由主 Agent 亲自接管。
2. **零盲信验收门禁**：无论 Codex 自报 `status: "done"` 与否，主 Agent 返回前必须核对 `changed_files`、`git diff` 与关键测试输出，由主 Agent 统一整合答复用户。
