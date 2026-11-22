# codex-in-anti

[简体中文](README.md) | [English](README.en.md)

[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-black)](https://linux.do/)

让 **Antigravity** 作为编排者、Code Reviewer 与最终验收者，通过 **OpenAI Codex CLI** 安全、低开销地指挥 **GPT** 完成有界编码、调试与重构任务。

## 为什么做这个项目？

如果直接在非交互模式下调用 `codex exec`，它会默认加载桌面端 `~/.codex/config.toml` 里的全部 GUI 插件、重型 MCP Server、Hooks、Memories 以及嵌套 Subagent 指令。我们在 macOS 本地针对 3 类真实开发任务（只读源码分析、新建模块并自测、定位并修复 Bug）做了受控对比测试：在 `-p worker` 轻量 Profile 模式下，**总 Token 消耗从 108,617 降至 59,550（整体降低 45.2%，单任务最高降幅 64.2%）**，**总耗时降低 8.1%（单任务最高提速 17.3%）**，且所有验证测试均一次通过。

在此基础上，`codex-in-anti` 进一步升级为**白名单彻底隔离（`--ignore-user-config`）**与工程化编排方案：

- **白名单彻底隔离（默认 `isolated` 模式）**：通过 `--ignore-user-config` 跳过整个 `~/.codex/config.toml`（保留 `auth.json` 登录态），再由 Python `tomllib` 仅白名单提取 `model`、`model_provider`、推理强度、基础供应商字段及代理环境变量注入 `-c`——无论是内置插件还是个人第三方重型 MCP、Hooks、Memories、`chronicle`，**100% 零加载、无需维护黑名单**（Python 3.10 环境自动回退至 `-p worker` 模式）。
- **严格的职责与沙箱边界**：主 Agent（Antigravity）负责任务拆解、Code Review 与最终验收；Codex CLI 仅作为有界执行 Worker，写任务强制锁定 `workspace-write`，只读分析强制 `read-only`。
- **实时进度流 + 原生风格可视化面板（MCP App 插件）**：
  - **对话内联实时卡片 + 侧边栏面板**：提供 Antigravity MCP App 插件（`plugin/codex-in-anti`），在对话内或侧边栏实时展示类似 Codex 原生的可折叠过程流——当前运行的 Shell 命令、退出码与可展开输出、文件变更芯片（点击直接在 Antigravity 侧边栏打开文件 Review）、实时计时器与 Input/Cached/Output Token 统计，并支持一键停止任务；
  - **零构建依赖**：仓库已预打包单个离线 HTML UI（`assets/app.html`），终端用户无需安装 Node.js 即可直接安装使用；
  - **CLI 实时回传**：独立 Skill 模式通过 `codex exec --json` 在 `stderr` 实时输出里程碑，同时保持 `stdout` 为纯净结构化 JSON（含 `status`、`changed_files`、`verification`、`risks`、`next_steps`）。
- **Git Worktree 并行隔离（`--worktree`）**：支持一键在临时独立 Git Worktree 分支中执行任务，主工作区保持绝对干净，方便多任务并行派活后统一 Review 合并。
- **开箱自检 `doctor` & 跨平台安装**：提供零第三方依赖的 `install.py`（支持 macOS / Linux / Windows）与 `codex_run.py doctor` 环境自检命令。

## 环境要求

- **Antigravity**（用户级 Skills 目录 `~/.gemini/config/skills`、Plugins 目录 `~/.gemini/config/plugins`）
- **OpenAI Codex CLI**（已安装并完成 `codex login` 登录）
- **Python 3.10+**（推荐 **Python 3.11+** 以启用内置 `tomllib` 支持默认 `isolated` 白名单隔离；Python 3.10 会自动回退为 `profile` 模式；终端用户**无需安装 Node.js**）

## 快速安装

支持 **macOS / Linux / Windows**：

```bash
git clone https://github.com/Silhouette-my/codex-in-anti.git
cd codex-in-anti
python3 install.py
```

macOS / Linux 也可以直接运行 `./install.sh`（内部调用 `install.py`）。默认会自动完成：
1. 将独立 CLI Skill 安装到 `~/.gemini/config/skills/codex-worker/`（可传 `--plugin-only` 跳过）。
2. 将 **Codex Worker MCP App 插件** 安装到 `~/.gemini/config/plugins/codex-in-anti/`（可传 `--skill-only` 跳过）。
3. 将 `profiles/worker.config.toml` 复制到 `~/.codex/worker.config.toml`（若已存在则默认跳过，加 `--force` 可覆盖）。
4. 自动执行 `codex_run.py doctor`，确认 Codex CLI、登录态、配置白名单与 Schema 全部就绪。

可通过参数或环境变量自定义安装路径：

```bash
python3 install.py --skills-dir /custom/skills --plugins-dir /custom/plugins --codex-home /custom/.codex
# 或使用环境变量：
ANTIGRAVITY_SKILLS_DIR=/custom/skills ANTIGRAVITY_PLUGINS_DIR=/custom/plugins CODEX_HOME=/custom/.codex python3 install.py
```

## 使用方式

### 0. 在 Antigravity 中使用原生风格过程面板（MCP App 插件）

安装插件后，Antigravity 会自动发现 `Codex Worker` 插件（提供 `codex_worker` MCP Server 与 `codex-worker-mcp` Skill）：
- 当 Antigravity 调用 `start_codex_task` 派发任务时，对话中会**立即内联渲染 Codex 实时过程卡片**（每秒自动增量刷新命令、输出与文件改动），随后主 Agent 通过 `wait_codex_task` 等待交付并亲自核对 diff / 做 Code Review。
- 你也可以随时从右侧边栏 `+` 菜单或左侧全局栏打开 **Codex Worker** 面板（`open_codex_panel`），查看当前与历史任务的完整执行时间线。

### 1. 环境自检（`doctor`）

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py doctor
```

一键检查 Python 版本与 `tomllib` 支持情况、`codex` 路径与版本、`auth.json` 登录态、`config.toml` / `worker.config.toml` 解析及白名单摘要、`handoff_schema.json` 以及 `git` 可用性。

### 2. 在 Antigravity 中自然语言触发

安装后，Antigravity 会自动发现 `codex-worker` Skill。你可以直接说：
- “把这部分模块实现派给 Codex 写，写完你来做 Code Review 并跑测试验收。”
- “用 `--worktree` 同时派给 Codex 帮我把这两个独立模块写完，我再合并。”
- “让 GPT 在只读模式下帮我排查 `tests/test_api.py` 的报错原因。”

### 3. 直接调用封装脚本 `codex_run.py`

单次写入任务（默认 `isolated` 白名单隔离 + `workspace-write` + `--ephemeral`）：

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --effort medium \
  --prompt "在 src/config.py 中实现 parse_config()，并运行 pytest tests/test_config.py 验证通过。"
```

在隔离的 Git Worktree 中执行（不修改当前工作区）：

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --worktree feat-config \
  --prompt "重构配置加载模块并补全单元测试，最后运行 pytest。"
```

多轮持久化任务（保存 `session_id` 后继续迭代）：

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --persist \
  --prompt "重构鉴权中间件并运行全量测试。"

python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py resume \
  -C /path/to/repo \
  <session_id> \
  --prompt "补充处理 token 过期字段缺失的边界情况。"
```

### 常用参数说明

| 参数 | 说明 |
| :--- | :--- |
| `-C`, `--cwd` | Codex 工作目录（默认 `.`） |
| `--mode` | 沙箱模式：`write`（`workspace-write`）或 `read-only` |
| `--config-mode` | 配置隔离模式：`isolated`（白名单 + `--ignore-user-config`，默认）、`profile`（`-p worker`）、`inherit`（继承桌面配置） |
| `--worktree [NAME]` | 仅 `exec` 支持：在独立临时 Git Worktree 分支中执行，不污染当前工作区 |
| `-m`, `--model` | 临时覆盖使用的 Codex 模型 |
| `--effort` | 调整推理强度：`low` \| `medium` \| `high` |
| `--persist` | 持久化本次会话（关闭 `--ephemeral`，便于后续 `resume`） |
| `--add-dir` | 额外授权可写目录（可多次指定） |
| `--raw` | 关闭 JSON Schema 约束，直接返回纯文本 |
| `--search` | 开启实时联网检索 |
| `--quiet` | 关闭 `stderr` 实时进度里程碑输出 |
| `--timeout` | 子进程超时时间（秒，默认 `600`） |
| `--log-dir` | 运行日志与交付产物存档目录（默认 `$TMPDIR/codex-worker-runs`） |

## 交付结构与实时进度

执行期间，`stderr` 会实时打印关键进度（不影响主调用方解析 `stdout` 的 JSON）：

```text
[codex] session=01a201b6-c7d9-7822-81d9-86c49f2aeabf
[codex] run: /bin/zsh -lc 'pytest tests/test_config.py'
[codex] done (exit=0): /bin/zsh -lc 'pytest tests/test_config.py'
[codex] turn done (in=18420, cached=12800, out=312)
```

执行完成后，`stdout` 输出标准 JSON 摘要，并将完整事件流 `codex.log`、`output.json`（或 `output.txt`）与 `meta.json` 归档至日志目录：

```json
{
  "ok": true,
  "exit_code": 0,
  "timed_out": false,
  "duration_sec": 12.98,
  "model": "gpt-...",
  "session_id": "...",
  "tokens_used": 18732,
  "config_mode": "isolated",
  "usage": {
    "input_tokens": 18420,
    "cached_input_tokens": 12800,
    "output_tokens": 312,
    "reasoning_output_tokens": 0
  },
  "result": {
    "status": "done",
    "summary": "...",
    "changed_files": ["..."],
    "verification": [
      {
        "command": "pytest",
        "passed": true,
        "notes": "All tests passed."
      }
    ],
    "risks": [],
    "next_steps": []
  },
  "output_file": "/tmp/codex-worker-runs/.../output.json",
  "log_file": "/tmp/codex-worker-runs/.../codex.log"
}
```

如果传入了 `--worktree`，JSON 中还会附带 `worktree` 字段（含 `path`、`branch`、`base_repo` 和 `cleanup_command`）。

## 实测性能对比

以下为 `-p worker` Profile 模式与桌面基线配置的受控对比实测（`isolated` 白名单模式在此基础上进一步彻底移除了用户自定义插件与重型 MCP 的残留加载）：

在 macOS 上使用 `codex-cli 0.171.0` 对同一模型、`model_reasoning_effort = "medium"`、相同 `--output-schema`、完全一致的 Prompt 与全新工作目录进行顺序实测，唯一变量为是否启用轻量 Profile（基线 `--no-worker-profile` 加载桌面插件、Hooks、Memories、`chronicle` 与 MCP Server，对照组使用 `-p worker`）：

| 任务 | 沙箱模式 | 基线耗时 | Worker 耗时 | 耗时变化 | 基线 Tokens | Worker Tokens | Token 降幅 | 上下文压缩 (基线/Worker) |
| :--- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| **T1**：只读分析 `codex_run.py` | `read-only` | 18.16s | 17.99s | -0.9% | 37,692 | 24,703 | **-34.5%** | 0 / 0 |
| **T2**：新建 `math_utils.py` + 6+ `assert` 自测 | `write` | 21.60s | 17.86s | **-17.3%** | 35,516 | 12,730 | **-64.2%** | 0 / 0 |
| **T3**：修复 `parser.py` Bug 并跑通 `test_parser.py` | `write` | 16.85s | 16.17s | -4.0% | 35,409 | 22,117 | **-37.5%** | 0 / 0 |
| **合计（3 项任务）** | - | **56.61s** | **52.02s** | **-8.1%** | **108,617** | **59,550** | **-45.2%** | 0 / 0 |

**日志对比分析：**
- 基线模式在每一步工具调用都会触发 `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` Hooks，加载桌面插件与 MCP 工具定义（T2 在空目录中甚至额外调用了桌面 MCP 的 glob 工具），并输出 `chronicle` 特性告警。
- 启用轻量隔离后，Codex 跳过了所有 Hooks、桌面插件、Memories 与重型 MCP，直接完成文件读写、补丁应用与测试验证（所有写任务在跑完后均复验通过）。
- 若宿主桌面配置中存在大量远程 OAuth MCP 或引发上下文压缩，耗时与 Token 的节省幅度还会进一步放大。

## 个性化调优

按需编辑 `~/.codex/worker.config.toml`：
- **固定默认模型 / 推理强度**：取消注释并修改 `model = "..."` 或 `model_reasoning_effort = "medium"`（也可在运行时通过 `-m` / `--effort` 覆盖）。
- **自定义白名单字段**：在默认 `isolated` 模式下，脚本会自动从 `~/.codex/config.toml` 提取 `model`、`model_provider`、当前 provider 的基础配置以及 `[shell_environment_policy]` 代理设置；如果仅在 Python 3.10 下使用 `profile` 模式并需要屏蔽额外个人 MCP，可再补充 `[mcp_servers.<name>]\nenabled = false`。

## 目录结构

```text
codex-in-anti/
├── install.py                                  # 跨平台一键安装脚本 (macOS / Linux / Windows)
├── install.sh                                  # POSIX 入口脚本（转发至 install.py）
├── plugin/
│   └── codex-in-anti/                          # Antigravity MCP App 实时过程插件
│       ├── plugin.json                         # 插件元数据
│       ├── mcp_config.json                     # MCP Server 启动配置
│       ├── assets/
│       │   ├── app.html                        # 预构建单文件 MCP App 前端 UI
│       │   └── logo.svg                        # 插件图标
│       ├── server/
│       │   └── mcp_server.py                   # 零依赖 Python MCP App 服务端
│       ├── skills/
│       │   └── codex-worker-mcp/
│       │       └── SKILL.md                    # MCP App 编排指引
│       └── ui-src/                             # 前端源码（开发二次打包用，运行无需 Node）
├── profiles/
│   └── worker.config.toml                      # Codex CLI Worker 配置文件
├── skill/
│   └── codex-worker/
│       ├── SKILL.md                            # 独立 CLI 版 Antigravity 编排 Skill
│       ├── resources/
│       │   └── handoff_schema.json           # --output-schema 强约束交付结构
│       └── scripts/
│           └── codex_run.py                    # 零依赖 Python 封装脚手架
├── README.md
├── README.en.md
└── LICENSE
```

## 社区与致谢

本项目首发分享于 [LINUX DO](https://linux.do/) 社区，欢迎前去交流多模型协作与工作流优化经验。

## 开源协议

[MIT](LICENSE)
