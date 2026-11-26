# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**Antigravity 2.0 ↔ OpenAI Codex CLI 原生多模型子 Agent 协同与监控工作台插件。**

> 本项目 Fork 自 [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)（保留 MIT 协议与上游版权声明），在其白名单隔离与 MCP App 架构基础上，升级为**通用开箱即用**的一体化 Antigravity 原生插件：**四源灵活模型映射、全推理档位与多 Provider 委派、滚动并行子 Agent 编排、以及融合本机全量 Codex 数据的三视图原生工作台**。

---

## 核心特性

让 **Antigravity** 担任架构规划、任务拆解、Code Review 与最终验收者，将本机 **Codex CLI** 中任意指定模型作为高效、低开销、可观测的外部子 Agent：

- **四源灵活模型映射（通用兼容，不绑死 `model_catalog_json`）**：
  - 按 `custom_models`（面板自定义） > `model_catalog_json`（自动/自定义/禁用可配） > `~/.codex/models_cache.json` > `config.toml` 自动合并模型，且全局支持手输任意模型名；
  - 默认 `isolated` 白名单隔离模式（`--ignore-user-config`）跳过桌面端重型插件、MCP、Hooks 与 Memories，精准透传模型、Provider、代理、Windows 沙箱（`[windows]` / `[sandbox_workspace_write]` / `allow_login_shell`）以及用户指定的 `extra_whitelist_keys`；
  - 面板与配置文件支持自由调整 **`catalog_mode`**（`auto`/`custom`/`off`）、**`effort_policy`**（`clamp_with_warning` 自动降级 / `passthrough` 原样透传）、**`context_window_policy`**（`same_model_only` / `always_forward` / `never_forward`）与 **`excluded_models`** 排除清单；
  - 支持全范围推理档位（`none / minimal / low / medium / high / xhigh / max / ultra` 及任意自定义档位）与单次任务动态切换 `model`、`provider` 或角色预设。
- **高效子 Agent 编排与跨模型协同**：
  - 完整 MCP 工具集：`list_codex_models`、`start_codex_task`、`get_codex_task_status`、`wait_codex_task`（支持 `run_ids` + `wait_mode="any"|"all"` 滚动并行验收，等待超时不误判失败）、`stop_codex_task`（Windows `taskkill /T /F` 彻底清理进程树）与 `open_codex_panel`；
  - 全程严格区分 `requested`（请求）/ `effective`（生效）/ `observed`（实测）三态模型身份；
  - 内置精简常驻 `rules/AGENTS.md` + 3 个按需披露 Skill（`codex-dispatch`、`codex-parallel-worktree`、`codex-second-opinion`）与 6 套可定制角色预设（`explore` / `coding` / `frontend` / `review` / `debug` / `fast`）。
- **类 Agy Powertools 的原生三视图工作台（零客户端注入、升级不失效）**：
  - 基于 Antigravity 官方 **MCP App** 原生入口（左侧边栏全屏页 + 右侧分屏 + 对话内联实时卡片），不做 CDP 页面注入，与 `agy-powertools` 完全和平共处；
  - **子 Agent 监控台**：活跃槽位指示（`max_concurrent_runs`）、实时可折叠过程树、Worktree 分支与一键清理命令、点击文件在侧栏直开、发送复核/挂载上下文，以及本机 Codex 会话上下文占比条与子线程派生树；
  - **用量统计看板**：融合 Antigravity 派发任务与本机 `~/.codex` 全量会话（只读解析 `state_*.sqlite` + 尾部 JSONL 增量缓存），支持范围/时间筛选，展示总 Token、缓存命中率、每日趋势、模型/Provider/档位分布与报错复制（不做费用估算）；
  - **模型与配置中心**：可视化调节全部映射策略、角色预设、自定义模型、排除列表，支持一键 **Dry-Run 命令行预览**（敏感凭据全脱敏）与 `doctor` 健康体检。
- **零门槛部署**：纯 Python 3.10+ 标准库 + 预打包单文件 `assets/app.html`，终端用户**无需安装 Node.js**、无需管理员权限。

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
