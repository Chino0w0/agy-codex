# Agy Codex 子 Agent 编排规范

1. **统一路由与结构化传参**：将代码实现、重构、排障或跨模型核查委派给 Codex / GPT / 指定 Codex 模型时，优先调用 `agy-codex` MCP 工具（`list_codex_models` → `start_codex_task` → `open_codex_panel` → `get_codex_task_status` / `wait_codex_task` → `stop_codex_task` → `close_codex_task`），不要直接在终端裸跑 `codex exec`。调用 `start_codex_task` 时传入可选结构化参数 `title`、`role`、`conversation_id`、`conversation_title`、`context_files`、`acceptance_criteria`、`non_goals`，以便分组展示与精确约束执行。
2. **可随时开关尊重用户**：当用户或工具返回 Codex 子代理已暂停（`enabled=false`）时，立刻改用 Antigravity 原生子 Agent 完成任务，不得强行绕过开关。
3. **先发现、未指定先问用户与 Orchestrator 防嵌套排除**：不认识模型 slug、不确定其支持哪些推理档位或需要使用角色预设时，先调用 `list_codex_models` 查询；当用户在当前对话未明确指定具体模型、且所用 `preset` 与工作台 `defaults.model` 均未绑定具体模型时，先将可见候选模型与支持档位列给用户选定，再调用 `start_codex_task`。遵循用户在配置中设置的 `presets`、`excluded_models` 与 `exclude_orchestrator_models=true`（绝不选择 Orchestrator 编排类模型或已排除模型作为 Codex 子代理目标），并如实区分 `requested` / `effective` / `observed` 身份，缺失即报未知，禁止猜值。
4. **单任务粒度、档位纪律与六段式自包含业务上下文**：
   - **单任务聚焦**：每个写子任务聚焦 1 个有界交付物（预计 2–5 分钟完成）；常规编码默认用 `medium` 或 `high`，仅将 `xhigh` 留给复杂跨模块排障与根因分析。
   - **严禁转述空洞口号**：禁止把 `"大厂级升级"`、`"全链路优化"` 等缺乏具体 UI/API 规格的模糊口号原样丢给 Codex。每个写任务 `prompt` 必须交付自包含的六段式业务上下文包：
     1. 业务背景与当前实现（功能服务于哪条业务流、现有代码如何运行、需解决的具体问题）
     2. 必读锚点文件（通过 `context_files` 传入 2–5 个设计文档、共享 Schema 或同风格参考页）
     3. 精确规格与接口契约（接口路径、字段类型、状态流转、文案/阈值常量）
     4. 改动范围与明确不做（白名单文件与 `non_goals`）
     5. 反脑补熔断（遇业务规则不明或源码与契约冲突，禁止猜想硬写，直接返回 `status="blocked"` 列明疑点）
     6. 可机检验收标准（`acceptance_criteria` 与必跑验证命令）
5. **契约先行并行与文件独占**：多模块并行改造前，主 Agent 必须先理解业务流并锁定共享接口契约，再将同一份契约与必读锚点文件（`context_files`）分发给各分支；利用 `max_concurrent_runs`（默认 5）并发推进并用 `wait_codex_task(run_ids=[...], wait_mode="any")` 滚动验收。写任务执行期间主 Agent 严禁同时修改同一组文件；涉及 2 个以上独立写任务并发时，必须各自传入唯一的 `worktree` 隔离分支。等待超时不等于任务失败，继续跟踪或按需 `stop_codex_task`，禁止重复派发同任务。
6. **右侧监控与零盲信验收**：调用 `start_codex_task`（或并发派发一批）后，紧接着调用一次 `open_codex_panel(tab="subagents", run_id=...)`（`tab` 支持 `"subagents" | "history" | "stats" | "models"`）在右侧边栏打开/刷新监控面板（不在对话框堆砌内联卡片）。所有 Codex 任务返回后，将 `status="partial"` 视为未完成任务（需审查并跟进补齐，不得当成 `done`），`status="blocked"` 按其列明疑点补充契约或由主 Agent 接管；主 Agent 必须亲自核对 `git diff` / 变更文件与关键验证命令结果后再向用户汇报。
7. **用完即销毁与关闭归档**：主 Agent 验收完 Codex 子任务结论与 `git diff` 后，调用 `close_codex_task` 将其从右侧活跃标签栏移出，保留 24 小时日志于「历史对话」中按 Antigravity 大会话分组追溯；不要留一堆已完结标签占满侧栏。
