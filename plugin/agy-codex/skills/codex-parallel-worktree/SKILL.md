---
name: codex-parallel-worktree
description: >-
  Orchestrate 2+ independent coding or investigation subtasks concurrently across Codex workers
  using isolated Git worktrees (`worktree`), slot limits (`max_concurrent_runs`), and rolling
  verification (`wait_codex_task` with `wait_mode="any"`). Trigger when splitting multi-module
  changes or parallel investigations across Codex subagents.
---

# Codex Parallel Worktree（多子 Agent 并行与滚动验收）

当面临 2 个以上互不依赖、可并行推进的模块或调查域时，利用 `agy-codex` 的 `worktree` 隔离与滚动等待能力实现高吞吐并行开发。

## 与 Antigravity 原生子 Agent 的协作边界
- **开箱即默认与开关尊重**：只要「Codex 联动」开关（`enabled=true`）与默认接管（`intercept_native_subagents=true`）开启且本地 Codex CLI 已就绪，2 个以上可并行模块的编码/重构/排障/调研拆分默认通过 `agy-codex` 的 `worktree` 并发派发（仅 `browser`、`image-generator` 等原生专有子代理走原生）。当用户或工具返回 `enabled=false`（或 `intercept_native_subagents=false` 且用户未指定 Codex）时，立刻改用 Antigravity 原生并行（`superpowers:dispatching-parallel-agents`），不得强行绕过开关。

## 并行编排四步法

### 1. 契约先行、单任务粒度与槽位规划
- **严格契约先行（Contract-First Parallelism）**：派发任何并行写 Worker 前，主 Agent **必须**先通读相关代码以理解完整业务流、锁定跨模块共享接口契约（接口路径、字段类型、状态流转、文案与阈值常量），并将同一份锁定契约与 2–5 个必读锚点文件（`context_files`）分发给每个分支；严禁在业务流和接口契约尚未确定前抢跑派发。
- **单任务粒度与档位纪律**：每个写分支只承担 **1 个有界交付物**（预计 2–5 分钟完成）；常规编码默认 `medium` 或 `high`，将 `xhigh` 留给复杂跨模块排障与根因分析。
- **严禁转述空洞口号**：禁止把 `"大厂级升级"`、`"全链路优化"` 等缺乏具体 UI/API 规格的模糊口号原样传给子分支。每个写分支 `prompt` 都必须包含自包含的六段式业务上下文包：
  1. 业务背景与当前实现（功能服务于哪条业务流、现有代码如何运行、需解决的具体问题）
  2. 必读锚点文件（通过 `context_files` 传入 2–5 个设计文档、共享 Schema 或同风格参考页）
  3. 精确规格与接口契约（接口路径、字段类型、状态流转、文案/阈值常量）
  4. 改动范围与明确不做（白名单文件与 `non_goals`）
  5. 反脑补熔断（遇业务规则不明或源码与契约冲突，禁止猜想硬写，直接返回 `status="blocked"` 列明疑点）
  6. 可机检验收标准（`acceptance_criteria` 与必跑验证命令）
- 按模块或测试文件划分互不交叉的写入范围；**同一组文件绝不分配给两个并发写任务**。
- 先检查 `list_codex_models` 返回的 `max_concurrent_runs`（默认 5）与当前活跃任务数，同时保持活跃槽位不超过上限。
- **合规默认模型优先沿用与 Orchestrator 防嵌套排除**：遵循 `exclude_orchestrator_models=true`，绝不选择 Orchestrator 编排类模型作为 Codex 子代理目标。模型优先级依次为「用户当轮指定 -> `preset.model` -> 工作台 `defaults.model` -> `~/.codex/config.toml` 合规默认模型」；只要四层之一已解析出合规模型即可直接并发派发，仅当四层均未绑定合规模型时先请用户确认模型与档位分配。

### 2. 并发派发（强制写任务启用 `worktree`）
- **写任务**：为每个分支传唯一的 `worktree` 名称（例如 `worktree="auth-fix"`、`worktree="cli-flags"`），每个子 Agent 会在独立 Git worktree（分支 `codex/<name>`）中工作，主工作区保持干净。
- **只读任务**：可传 `mode="read-only"`，无需创建 `worktree`。
- 调用 `start_codex_task`（保持默认 `timeout=600`）时传入可选结构化参数 `title`、`role`、`conversation_id`、`conversation_title`、`context_files`、`acceptance_criteria`、`non_goals`，并按任务复杂度混搭模型与档位（例如轻量批量脚本用 `preset="fast"`，核心模块用 `preset="coding"`，避免常规编码滥用 `xhigh`）。
- 一批并发任务发出后，调用一次 `open_codex_panel(tab="subagents", run_id=<first_run_id>)`（`tab` 支持 `"subagents" | "history" | "stats" | "models"`）在右侧边栏集中查看所有活跃子代理实时进度。

### 3. 滚动验收（Rolling Verification）
不要死等所有分支全部跑完才开始看结果：
1. 调用 `wait_codex_task(run_ids=[id1, id2, ...], wait_mode="any", timeout=120)`（单次等待封顶 120s，若返回 `wait_timed_out=true` 则继续轮询等待而非重复派发）：
   - 只要任一分支先完成就会立即返回；
   - 或用 `get_codex_task_status(run_ids=[...])` 随时非阻塞查看全部槽位进展。
2. 对已完成的 `codex/<name>` 分支立即做增量审查：
   - `git diff HEAD...codex/<name>` 检查改动范围与代码质量；
   - 若某分支返回 `status="partial"`，必须将其视为**未完成工作**（审查现有改动并补派收尾任务或由主 Agent 接管，严禁当作 `done` 直接合入）；若返回 `status="blocked"`，先核对并补充缺失契约再继续；
   - 若有新需求或可利用空出槽位，在契约明确的前提下派发下一批待办任务，保持流水线满载。
3. 若某分支超时未完，调用 `get_codex_task_status` 跟踪或决定是否 `stop_codex_task`；更换执行者前务必先停止原任务。

### 4. 合并集成、关闭归档与清理
1. 按依赖顺序合并或 cherry-pick 各个通过审查的 `codex/<name>` 分支到主工作区。
2. **主 Agent 亲自在合并后的工作区运行全量测试/构建**，确认跨分支无语义冲突或回归。
3. 使用返回结果中的 `worktree.cleanup_command` 清理临时 worktree 目录与临时分支，并对已完结子任务调用 `close_codex_task(run_id=...)` 将其从右侧活跃标签栏移出（保留 24 小时日志于「历史对话」按 Antigravity 大会话分组追溯），避免已完结标签占满侧栏，最后统一向用户汇报集成结论。
