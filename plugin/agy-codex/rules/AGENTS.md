# Agy Codex 子 Agent 编排规范

1. **默认路由与结构化传参（开箱即走 Codex）**：只要「Codex 联动」开关开启（`enabled=true`，安装即默认开启）且本地 Codex CLI 已就绪，凡通用子 Agent 委派（编码实现、重构、排障、代码审查、只读调研、并行拆分）默认一律走 `agy-codex` MCP（`start_codex_task` -> `open_codex_panel` -> `get_codex_task_status` / `wait_codex_task` -> `stop_codex_task` -> `close_codex_task`），不再调用原生 `invoke_subagent`，也禁止直接在终端裸跑 `codex exec`。仅 Antigravity 专有子代理（`browser`、`image-generator`、`flutter_a11y_agent` 等）、用户当轮明确要求使用原生子代理、或「Codex 联动」处于暂停状态时使用原生 `invoke_subagent`。调用 `start_codex_task` 时传入可选结构化参数 `title`、`role`、`conversation_id`、`conversation_title`、`context_files`、`acceptance_criteria`、`non_goals`。
2. **可随时开关尊重用户**：当用户或工具返回 Codex 子代理已暂停（`enabled=false`）或 `intercept_native_subagents=false` 且用户未指定 Codex 时，立刻改用 Antigravity 原生子 Agent 完成任务，不得强行绕过开关。
3. **合规默认模型优先沿用与 Orchestrator 防嵌套排除**：模型选择优先级依次为：用户当轮显式指定模型 -> `preset.model` -> 工作台 `defaults.model` -> 用户 `~/.codex/config.toml` 中的合规默认模型（未在 `excluded_models` 且通过 Orchestrator 排除保护）。只要 `SessionStart` 提示或前序工具调用已确认合规默认模型，直接派发 `start_codex_task` 无需多余调用 `list_codex_models` 或打断询问；仅当四层均未指定合规模型（或需要确认特殊推理档位/未知模型 slug）时，先调用 `list_codex_models`（返回精简调度简报）列给用户选定再派发。绝不选择 Orchestrator 编排类模型或已排除模型，并如实区分 `requested` / `effective` / `observed` 身份，缺失即报未知，禁止猜值。
4. **单任务粒度、档位纪律与六段式自包含业务上下文**：
   - **单任务聚焦**：每个写子任务聚焦 1 个有界交付物（预计 2–5 分钟完成）；常规编码默认用 `medium` 或 `high`，仅将 `xhigh` 留给复杂跨模块排障与根因分析。
   - **严禁转述空洞口号**：禁止把 `"大厂级升级"`、`"全链路优化"` 等缺乏具体 UI/API 规格的模糊口号原样丢给 Codex。每个写任务 `prompt` 必须交付自包含的六段式业务上下文包：
     1. 业务背景与当前实现（功能服务于哪条业务流、现有代码如何运行、需解决的具体问题）
     2. 必读锚点文件（通过 `context_files` 传入 2–5 个设计文档、共享 Schema 或同风格参考页）
     3. 精确规格与接口契约（接口路径、字段类型、状态流转、文案/阈值常量）
     4. 改动范围与明确不做（白名单文件与 `non_goals`）
     5. 反脑补熔断（遇业务规则不明或源码与契约冲突，禁止猜想硬写，直接返回 `status="blocked"` 列明疑点）
     6. 可机检验收标准（`acceptance_criteria` 与必跑验证命令）
5. **契约先行并行、双超时分离与文件独占**：多模块并行改造前，主 Agent 必须先理解业务流并锁定共享接口契约，再将同一份契约与必读锚点文件（`context_files`）分发给各分支；利用 `max_concurrent_runs`（默认 5）并发推进并用 `wait_codex_task(run_ids=[...], wait_mode="any", timeout=120)` 滚动验收（单次 `wait_codex_task` 轮询上限固定为 120 秒以防 MCP 传输超时；`start_codex_task` 默认 `timeout=0` 不限总时长，由实时健康/卡死/循环监测与手动停止护航，禁止随意下调执行超时截断活跃任务）。写任务执行期间主 Agent 严禁同时修改同一组文件；涉及 2 个以上独立写任务并发时，必须各自传入唯一的 `worktree` 隔离分支。`wait_timed_out=true` 不等于任务失败，若返回 `health="stalled"` 或 `"loop_warning"` 则结合 `health_reason` / `recent_errors` 决定继续等待或 `stop_codex_task`，禁止重复派发同任务。
6. **右侧监控、会话隔离与零盲信验收**：调用 `start_codex_task`（或并发派发一批）后，紧接着调用一次 `open_codex_panel(tab="subagents", run_id=..., conversation_id=...)`（`tab` 支持 `"subagents" | "history" | "stats" | "models"`）在右侧边栏打开/刷新监控面板（不在对话框堆砌内联卡片），自动将活跃监控隔离到当前 Antigravity 大会话不与并行对话串台。所有 Codex 任务返回后，将 `status="partial"` 视为未完成任务（需审查并跟进补齐，不得当成 `done`），`status="blocked"` 按其列明疑点补充契约或由主 Agent 接管；主 Agent 必须亲自核对 `git diff` / 变更文件与关键验证命令结果后再向用户汇报。
7. **用完即销毁与关闭归档**：主 Agent 验收完 Codex 子任务结论与 `git diff` 后，调用 `close_codex_task`（批量清理时附带 `conversation_id` 避免影响其他并行对话）将其从右侧活跃标签栏移出，保留 24 小时日志于「历史对话」中按 Antigravity 大会话分组追溯；不要留一堆已完结标签占满侧栏。
