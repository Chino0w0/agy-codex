# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**Antigravity 2.0 ↔ OpenAI Codex CLI 原生多模型子 Agent 协同与监控工作台插件。**

> 本项目 Fork 自 [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)（保留 MIT 协议与上游版权声明），在其白名单隔离与 MCP App 架构基础上，升级为**真正开箱即用**的一体化 Antigravity 原生插件：**安装即激活默认 Codex 子代理路由、四源模型发现、一键切换 Codex 与原生引擎、以及 4 视图监控工作台**。

---

## 核心特性

让 **Antigravity** 担任架构规划、任务拆解、Code Review 与最终验收者，将本机 **Codex CLI** 变成安装后默认接管通用子任务的执行引擎：

- **真正开箱即用（默认接管通用子代理，开关实时切换）**：
  - 安装即启用内置 **`hooks.json`（`SessionStart` + `PreToolUse`）**：只要「Codex 联动」开关处于开启状态（默认开启），Antigravity 派发通用子任务（编码实现、重构、排障、代码审查、代码调研、并行拆分）时**无需在对话里手动点名**，默认一律走 Codex；
  - 顶部栏与全部 4 个视图均提供高对比度 **「Codex 联动」** 一键开关，随时关闭即可立即 100% 切回 Antigravity 原生子代理；
  - 内置智能放行白名单与兜底降级：`browser`（内置浏览器）、`image-generator`（生图）等 Antigravity 专有能力自动放行；用户当轮明确说「不用 Codex / 用原生子代理」时自动放行；若本机尚未安装或登录 Codex CLI，钩子毫秒级**自动降级放行原生**，绝不阻塞使用；
  - **合规默认模型优先沿用**：依次读取「用户显式指定 -> 角色预设 -> 工作台默认模型 -> `~/.codex/config.toml` 合规默认模型」（经 Orchestrator 防嵌套保护与排除清单过滤），免配置即可直接开跑。
- **四源灵活模型映射与 Orchestrator 防嵌套保护**：
  - 按 `custom_models`（面板自定义） > `model_catalog_json`（自动/自定义/禁用可配） > `~/.codex/models_cache.json` > `config.toml` 自动合并模型，且全局支持手输任意模型名；
  - 默认 `isolated` 白名单隔离模式（`--ignore-user-config`）跳过桌面端重型插件、MCP、Hooks 与 Memories，按需透传模型、Provider、代理、Windows 沙箱、`safe.directory=*`（仅子进程环境）与指定 MCP 命名空间；
  - 支持 **`exclude_orchestrator_models`** 双重防嵌套保护、全范围推理档位（`none / minimal / low / medium / high / xhigh / max / ultra` 及自定义档位）与可配置 `catalog_mode` / `effort_policy` / `context_window_policy` / `native_passthrough_subagents` / `excluded_models`。
- **高效并行子 Agent 编排与契约式委派（`v1.2.0`）**：
  - 模型可见 MCP 工具：`list_codex_models`、`start_codex_task`（支持 `context_files`、`acceptance_criteria`、`non_goals`）、`get_codex_task_status`、`wait_codex_task`（支持 `run_ids` + `wait_mode="any"|"all"` 滚动验收）、`stop_codex_task`、`close_codex_task` 与 `open_codex_panel`；
  - 并发无阻塞 `serve_stdio` JSON-RPC 调度与跨进程 `pid` 存活识别，`--ephemeral --thread-source subagent` 用后即销毁不污染 Codex Desktop，并修复 Windows 下子进程管道死锁与 `partial` 交接状态识别；
  - 内置精简常驻 `rules/AGENTS.md` + 3 个按需披露 Skill（`codex-dispatch`、`codex-parallel-worktree`、`codex-second-opinion`）与 6 套可定制角色预设。
- **4 视图原生 MCP App 工作台（零客户端注入、各界面可即时开关）**：
  - 基于 Antigravity 官方 **MCP App** 原生入口（左侧「插件 -> Agy Codex」全屏页 + 右侧 `aux_pane` 分屏 + 对话内联卡片），不做 CDP 页面注入，升级 Antigravity 客户端不失效；
  - **活跃监控**：运行中优先排序与实时增量步骤流（含前置超时倒计时、完整指令折叠、Worktree 分支与侧栏文件直开、一键关闭/批量归档）；
  - **历史对话**：严格按 Antigravity 大会话分组折叠，4 行清晰卡片层次（状态标题 + 角色/归档徽章 + 工作摘要 + 模型耗时指标），支持 1 至 168h 可配归档留存（默认 24h）与一键恢复；
  - **统计面板**：专注统计 Antigravity 与 Codex 联动任务（永久紧凑账本 `stats-ledger.jsonl`，不含费用估算），展示累计派发、成功率、缓存命中趋势、模型/角色/Provider/档位分布与报错一键复制；
  - **模型与配置**：带选择性 `?` 说明气泡，支持随时开关 Codex 联动、默认原生接管路由与 Orchestrator 保护，按需透传 MCP、原生放行白名单、预设路由、**Dry-Run 脱敏命令预览**与 `doctor` 体检。
- **零门槛部署**：纯 Python 3.10+ 标准库 + 预打包单文件 `assets/app.html`，终端用户**无需安装 Node.js**、无需管理员权限；支持命令行或交给 Agent 一句话自动安装与卸载。

---

## 环境要求

- **Antigravity 2.0**（自动发现 `~/.gemini/config/plugins/` 下的插件）
- **OpenAI Codex CLI**（已安装并完成 `codex login` 或配置好自定义 Provider）
- **Python 3.10+**（推荐 **Python 3.11+** 以启用标准库 `tomllib` 白名单隔离；终端用户无需 Node.js）

---

## 安装与卸载

### 方式一：复制提示词交给 Agent 自动安装（推荐）

> 请帮我安装 Agy Codex：https://github.com/Chino0w0/agy-codex 。先阅读 README.md 和 docs/INSTALL_AGENT.md，使用项目自带的跨平台安装脚本完成安装并运行环境自检（doctor），保留我现有的其他插件、Codex 配置与聊天记录，不要强制退出当前客户端。安装完成后告诉我自检结果和生效方式。

详细步骤见 [Agent 安装指南](docs/INSTALL_AGENT.md)。

### 方式二：命令行手动安装

#### Windows（PowerShell）
```powershell
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

#### macOS / Linux
```bash
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
sh ./install.sh
```

### CLI 工具命令（安装后立即可用）

```bash
# 环境体检
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py doctor

# 查看已发现的模型、Provider 与预设（加 --include-hidden 显示隐藏模型）
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py models

# 干跑预览实际生成的脱敏命令行与告警
python ~/.gemini/config/plugins/agy-codex/server/codex_run.py preview --preset debug
```

### 卸载

```powershell
# Windows（保留配置与历史可加 --keep-data）
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

```bash
# macOS / Linux
sh ./uninstall.sh
```

---

## 目录结构

```text
agy-codex/
├── docs/
│   ├── INSTALL_AGENT.md                        # Agent 自动安装指南
│   └── superpowers/specs/                      # 架构设计文档
├── install.py / install.ps1 / install.sh       # 跨平台统一安装器
├── uninstall.py / uninstall.ps1 / uninstall.sh # 跨平台统一卸载器
├── profiles/
│   └── worker.config.toml                      # Codex CLI Worker 轻量配置模板
├── plugin/
│   └── agy-codex/                              # Antigravity 一体化原生插件
│       ├── plugin.json                         # 插件元数据
│       ├── mcp_config.json                     # MCP Server 配置
│       ├── assets/
│       │   ├── app.html                        # 预构建单文件 MCP App 前端 UI
│       │   └── logo.svg                        # 插件图标
│       ├── rules/
│       │   └── AGENTS.md                       # 常驻路由与子 Agent 编排规范
│       ├── skills/
│       │   ├── codex-dispatch/SKILL.md         # 单任务精准委派与模型路由
│       │   ├── codex-parallel-worktree/SKILL.md# 多分支并行 Worktree 滚动验收
│       │   └── codex-second-opinion/SKILL.md   # 跨模型独立审查与会诊
│       ├── server/
│       │   ├── mcp_server.py                   # 零依赖 Python MCP App 服务端
│       │   ├── codex_run.py                    # 封装执行器 + doctor/models/preview CLI
│       │   ├── codex_catalog.py                # 四源模型目录、预设与安全白名单引擎
│       │   ├── codex_telemetry.py              # 本地 SQLite/JSONL 只读统计融合引擎
│       │   └── handoff_schema.json             # 结构化交付 JSON Schema
│       └── ui-src/                             # 前端 TypeScript 源码（仅二次开发打包用）
└── tests/                                      # Python unittest 自动化测试套件
```

---

## 致谢与开源协议

- 上游原项目：[Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)（首发分享于 [LINUX DO](https://linux.do/) 社区）
- 开源协议：[MIT License](LICENSE)
