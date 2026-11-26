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
- 若用户希望用 Antigravity 内置模型并行，走 `superpowers:dispatching-parallel-agents`。
- 若用户要求调度 Codex / GPT / 指定外部模型并行，或需要同一仓库多模型同时改文件并统一审查，使用本 Skill。

## 并行编排四步法

### 1. 尽早拆解独立域与槽位规划
- 按模块或测试文件划分互不交叉的写入范围；**同一组文件绝不分配给两个并发写任务**。
- 先检查 `list_codex_models` 返回的 `max_concurrent_runs`（默认 5）与当前活跃任务数，同时保持活跃槽位不超过上限。
- 若用户未指定各分支使用哪个模型、且对应 `preset` 与 `workbench_config.defaults.model` 均未绑定具体模型，先请用户确认各分支的模型与档位分配再派发。
- 无需等主 Agent 读完所有文件再派发；尽早把已经清晰的独立分支派出去，主 Agent 同步推进自身分析或测试设计。

### 2. 并发派发（强制写任务启用 `worktree`）
- **写任务**：为每个分支传唯一的 `worktree` 名称（例如 `worktree="auth-fix"`、`worktree="cli-flags"`），每个子 Agent 会在独立 Git worktree（分支 `codex/<name>`）中工作，主工作区保持干净。
- **只读任务**：可传 `mode="read-only"`，无需创建 `worktree`。
- 可按任务复杂度混搭模型与档位（例如轻量批量脚本用 `preset="fast"`，核心模块用 `preset="coding"`）。
- 一批并发任务发出后，调用一次 `open_codex_panel(tab="subagents", run_id=<first_run_id>)` 在右侧边栏集中查看所有活跃子代理实时进度。

### 3. 滚动验收（Rolling Verification）
不要死等所有分支全部跑完才开始看结果：
1. 调用 `wait_codex_task(run_ids=[id1, id2, ...], wait_mode="any", timeout=300)`：
   - 只要任一分支先完成就会立即返回；
   - 或用 `get_codex_task_status(run_ids=[...])` 随时非阻塞查看全部槽位进展。
2. 对已完成的 `codex/<name>` 分支立即做增量审查：
   - `git diff HEAD...codex/<name>` 检查改动范围与代码质量；
   - 若有新需求或可利用空出槽位，立刻派发下一批待办任务，保持流水线满载。
3. 若某分支超时未完，调用 `get_codex_task_status` 跟踪或决定是否 `stop_codex_task`；更换执行者前务必先停止原任务。

### 4. 合并集成与清理
1. 按依赖顺序合并或 cherry-pick 各个通过审查的 `codex/<name>` 分支到主工作区。
2. **主 Agent 亲自在合并后的工作区运行全量测试/构建**，确认跨分支无语义冲突或回归。
3. 使用返回结果中的 `worktree.cleanup_command` 清理临时 worktree 目录与临时分支，最后统一向用户汇报集成结论。
