# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**Antigravity 2.0 ↔ OpenAI Codex CLI 原生多模型子 Agent 协同与监控工作台插件。**

> 本项目 Fork 自 [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)（保留 MIT 协议与上游版权声明），在其白名单隔离与 MCP App 架构基础上，面向所有 Codex 用户升级为一体化 Antigravity 原生插件：**支持 `model_catalog_json` 与原生模型双模发现、全推理档位与多 Provider 委派、常驻子 Agent 编排规范、以及融合本机全量 Codex 数据的三视图原生工作台**。

---

## 为什么做 Agy Codex？

让 **Antigravity** 专注于架构规划、任务拆解、Code Review 与最终验收，把你本地 **Codex CLI** 里的任意指定模型变成高效、低开销、可观测的外部子 Agent：

- **自定义目录与原生模型全兼容（开箱即用）**：
  - 默认 `isolated` 白名单隔离模式（`--ignore-user-config`）跳过桌面端重型插件、MCP、Hooks 与 Memories，只精准透传当前模型、Provider、代理以及 `model_catalog_json`（自动解析相对路径）、上下文窗口等关键配置；
  - **无需定制即可适用于所有用户**：有 `model_catalog_json` 时自动识别全部自定义模型与支持档位；没有 catalog 时自动平滑降级为原生 Codex 模式，直接兼容官方账号与任意第三方 OpenAI 兼容网关；
  - 完整支持 `none / minimal / low / medium / high / xhigh / max / ultra` 及任意自定义推理档位，并支持单次任务动态切换 `model`、`provider` 或角色预设（`coding / review / debug / fast`）。
- **类 Agy Powertools 的原生三视图工作台（零客户端注入、升级不失效）**：
  - 基于 Antigravity 官方 **MCP App** 三大原生入口（**左侧边栏全屏页** + **右侧分屏面板** + **对话内联实时卡片**），**不做 Electron CDP 注入**，与 `agy-powertools` 完全和平共处、Antigravity 客户端怎么升级都不会失效；
  - **子 Agent 监控台**：多任务与并行 Worktree 实时进度、可折叠过程树、一键停止（彻底清理子进程树）、点击文件在 Antigravity 侧栏直开 Review；
  - **用量统计看板**：融合 Antigravity 派发任务与本机 `~/.codex` 全量会话（只读解析 SQLite + 增量缓存），展示任务/会话数、成功率、总 Token、缓存命中率、每日趋势、模型/Provider/档位分布与 Codex 子线程树（不做费用估算，专注研发效率数据）；
  - **模型与配置中心**：可搜索浏览全部模型与支持档位，一键切换默认 Worker 模型、Provider、推理档位与角色预设，内置 `doctor` 环境体检，绝不修改 `~/.codex/config.toml`。
- **内置高效率协同规则与 Skills**：
  - 精简常驻 `rules/AGENTS.md` + 3 个按需披露的专业 Skill（`codex-dispatch`、`codex-parallel-worktree`、`codex-second-opinion`），规范模型发现、文件独占、并行 Worktree 编排与跨模型交叉审查。
- **零门槛部署**：纯 Python 3.10+ 标准库 + 预打包单文件前端 UI，终端用户**无需安装 Node.js**、无需管理员权限。

> **当前状态**：已完成仓库创建、中英文文档与完整设计规范（[设计文档](docs/superpowers/specs/2026-11-26-agy-codex-design.md)），核心代码改造待方案确认后立即实施。

---

## 环境要求

- **Antigravity 2.0**（自动发现 `~/.gemini/config/plugins/` 下的插件）
- **OpenAI Codex CLI**（已安装并完成 `codex login` 或配置好自定义 Provider）
- **Python 3.10+**（推荐 **Python 3.11+** 以启用标准库 `tomllib` 白名单隔离；终端用户无需 Node.js）

---

## 安装与卸载

### 方式一：复制提示词交给 Agent 自动安装（推荐）

直接把下面这段话发给 Antigravity 或其他可执行本地命令的 Agent：

> 请帮我安装 Agy Codex：https://github.com/Chino0w0/agy-codex 。先阅读 README.md 和 docs/INSTALL_AGENT.md，使用项目自带的跨平台安装脚本完成安装并运行环境自检（doctor），保留我现有的其他插件、Codex 配置与聊天记录，不要强制退出当前客户端。安装完成后告诉我自检结果和生效方式。

详细步骤见 [Agent 安装指南](docs/INSTALL_AGENT.md)。

### 方式二：命令行手动安装

#### Windows（PowerShell）
```powershell
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```
（本机已有 `python` 时也可直接运行 `python install.py`。）

#### macOS / Linux
```bash
git clone https://github.com/Chino0w0/agy-codex.git
cd agy-codex
sh ./install.sh
```
（或直接运行 `python3 install.py`。）

### 卸载

在项目目录执行对应卸载脚本即可移除插件目录（默认保留其他所有插件与 `~/.codex/config.toml`，加 `--keep-data` 可保留统计缓存与角色预设）：

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

```bash
# macOS / Linux
sh ./uninstall.sh
```

---

## 架构文档与路线图

完整的模块设计、MCP 工具协议清单、双模目录解析策略、只读 SQLite 统计融合方案与前端工作台规划，请参阅：

- [docs/superpowers/specs/2026-11-26-agy-codex-design.md](docs/superpowers/specs/2026-11-26-agy-codex-design.md)

---

## 致谢与开源协议

- 上游原项目：[Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)（首发分享于 [LINUX DO](https://linux.do/) 社区）
- 开源协议：[MIT License](LICENSE)
