---
name: codex-second-opinion
description: >-
  Request an independent cross-model adversarial code review or deep root-cause second opinion
  from a Codex model in strict read-only sandbox (`preset="review"` or `preset="debug"`).
  Trigger before merging significant changes or when debugging a stubborn issue.
---

# Codex Second Opinion（跨模型独立审查与会诊）

在完成重要功能、准备合并分支或遇到复杂疑难 Bug 时，请 Codex 中的另一个模型家族以**严格只读（`read-only`）**身份提供独立视角审查，避免单一模型盲区。

## 开关尊重、结构化参数与档位纪律

- **开箱即用与开关尊重**：在 `enabled=true` 且 `intercept_native_subagents=true` 且本地 Codex CLI 已就绪时，独立只读审查与根因会诊子任务默认优先走 `agy-codex` MCP（`preset="review"` / `"debug"`）；当用户或工具返回 Codex 子代理已暂停（`enabled=false`，或 `intercept_native_subagents=false` 且用户未指定 Codex）时，立刻改用 Antigravity 原生子 Agent 完成审查，不得强行绕过开关。
- **合规默认模型优先沿用与 Orchestrator 防嵌套排除**：模型选择依次遵循「用户当轮指定 -> `preset.model` -> 工作台 `defaults.model` -> `~/.codex/config.toml` 合规默认模型」（符合 `exclude_orchestrator_models=true` 与 `excluded_models`，绝不选择 Orchestrator 编排类模型），仅四层均未解析出合规模型时先调用 `list_codex_models` 列给用户选定。
- **结构化传参**：调用 `start_codex_task` 时传入可选参数 `title`、`role`、`conversation_id`、`conversation_title`、`context_files`、`acceptance_criteria`、`non_goals`，并通过 `open_codex_panel(tab="subagents", run_id=...)`（`tab` 支持 `"subagents" | "history" | "stats" | "models"`）在右侧查看进度。
- **档位纪律与粒度**：常规合并前代码审查（`review`）使用 `medium` 或 `high`，仅将 `xhigh` 留给复杂疑难根因会诊（`debug`）；单次会诊聚焦 1 个有界问题域（2–5 分钟）。

## 两种常用模式

1. **合并前代码审查（`preset="review"`，默认 `read-only` + `medium`）**：
   - 通过 `context_files` 传入 2–5 个待审核心文件/设计契约，`non_goals` 明确只读与不修改代码，`acceptance_criteria` 列出重点核查项（正确性、边界条件、安全与并发、测试覆盖），禁止只传 `"大厂级升级"` 或 `"全链路优化"` 这类空洞口号而不给具体验收规格。
2. **疑难根因会诊（`preset="debug"`，默认 `read-only` + `xhigh`）**：
   - 当主线排查陷入僵局或存在多个竞争假设时，把完整现象、复现命令、已排除假设、接口契约与 2–5 个关键锚点文件（`context_files`）交给推理强度更高的 Codex 模型做独立根因推演；若线索不足则要求其返回 `status="blocked"` 列明疑点而非臆测结论。

## 分级、上下文交接与输出要求

在 `prompt` 中要求 Codex 按以下规范报告：
- 每条发现按 **`Critical` / `Important` / `Minor`** 分级；
- 必须附带精确的 **`file:line`** 位置、触发条件与最小可验证证据；
- 严禁修改任何文件（由 `mode="read-only"` 强制保证）。
- 若后续要把审查发现转交给写 Worker 修复，主 Agent 必须补齐完整的六段式自包含业务上下文包（1. 业务背景与当前实现、2. 2–5 个必读锚点 `context_files`、3. 精确规格与接口契约、4. 改动范围与 `non_goals`、5. 遇疑点返回 `status="blocked"` 的反脑补熔断、6. `acceptance_criteria` 与必跑验证命令）。

## 主 Agent 甄别与归档原则（禁止审查套审查）

1. **技术甄别而非盲从**：逐条核实 Codex 指出的 `file:line` 与复现逻辑，有道理则采纳修复，误报则明确说明原因并忽略；若返回 `status="partial"`，视为未完成审查，需先补齐剩余范围复核而不能当作 `done`。
2. **禁止无限套娃审查**：一般只对真正新发现的高风险改动追加单点复核，避免对已确认通过的代码循环互相审查消耗时间和上下文。
3. **用完即销毁与关闭归档**：甄别结论并验证 diff 与测试通过后，调用 `close_codex_task(run_id)` 将审查标签从右侧活跃栏移出，保留 24 小时日志于「历史对话」按 Antigravity 大会话分组追溯，不要留一堆已完结标签占满侧栏。
4. **最终责任在主 Agent**：采纳修改后必须亲自验证 diff 与测试通过再结束任务。
