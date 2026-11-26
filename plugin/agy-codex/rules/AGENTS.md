# Agy Codex 子 Agent 编排规范

1. **统一路由**：将代码实现、重构、排障或跨模型核查委派给 Codex / GPT / 指定 Codex 模型时，优先调用 `agy-codex` MCP 工具（`list_codex_models` → `start_codex_task` → `open_codex_panel` → `get_codex_task_status` / `wait_codex_task` → `stop_codex_task`），不要直接在终端裸跑 `codex exec`。
2. **先发现再点名**：不认识模型 slug、不确定其支持哪些推理档位或需要使用角色预设时，先调用 `list_codex_models` 查询；遵循用户在配置中设置的 `presets` 与 `excluded_models`（不主动自动挑选已排除模型），并如实区分 `requested` / `effective` / `observed` 身份，缺失即报未知，禁止猜值。
3. **尽早拆分与滚动并行**：对于多模块改造或互不依赖的子任务，尽早派发并利用 `max_concurrent_runs`（默认 5）并发推进，主 Agent 同步做自身核查或架构工作，不要空等。多任务并行时用 `wait_codex_task(run_ids=[...], wait_mode="any")` 滚动验收；等待超时不等于任务失败，继续跟踪或按需 `stop_codex_task`，禁止重复派发同任务。
4. **文件独占与 Git Worktree 隔离**：写任务执行期间，主 Agent 严禁同时修改同一组文件；涉及 2 个以上独立写任务并发时，必须各自传入不同的 `worktree` 参数隔离分支。
5. **右侧监控与零盲信验收**：调用 `start_codex_task`（或并发派发一批）后，紧接着调用一次 `open_codex_panel(tab="subagents", run_id=...)` 在右侧边栏打开/刷新监控面板（不在对话框堆砌内联卡片）；所有 Codex 任务返回后，主 Agent 必须亲自核对 `git diff` / 变更文件与关键验证命令结果，再由主 Agent 统一整合向用户汇报。
