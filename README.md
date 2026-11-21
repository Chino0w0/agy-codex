# codex-in-anti

[简体中文](README.md) | [English](README.en.md)

[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-black)](https://linux.do/)

让 **Antigravity** 作为编排者与验收者，通过 **OpenAI Codex CLI** 安全、低开销地指挥 **GPT** 完成有界编码、调试、重构与独立 Code Review 任务。

## 为什么做这个项目？

如果直接在非交互模式下调用 `codex exec`，它会默认加载桌面端 `~/.codex/config.toml` 里的全部 GUI 插件、重型 MCP Server、Hooks、Memories 以及嵌套 Subagent 指令。我们在 macOS 本地针对 3 类真实开发任务（只读源码分析、新建模块并自测、定位并修复 Bug）做了受控对比测试：接入 `-p worker` 后，**总 Token 消耗从 108,617 降至 59,550（整体降低 45.2%，单任务最高降幅 64.2%）**，**总耗时降低 8.1%（单任务最高提速 17.3%）**，且所有验证测试均一次通过。

`codex-in-anti` 将整套工程化实践固化为开箱即用的开源方案：

- **不污染桌面端配置**：通过 `-p worker` 叠加 `~/.codex/worker.config.toml`，完全不修改你原有的 `~/.codex/config.toml`。
- **严格的沙箱隔离**：写任务强制锁定 `workspace-write`，只读分析与 Code Review 强制 `read-only`，避免无意继承全局 `danger-full-access`。
- **结构化 JSON 交付**：通过 `--output-schema` 与 `-o` 约束 Codex 输出统一的 `status`、`changed_files`、`verification`、`risks`、`next_steps`，不再从杂乱日志里正则捞结果。
- **支持单次、多轮与交叉审查**：默认 `--ephemeral` 不留垃圾历史；需要多轮迭代时传 `--persist` 并用 `resume <session_id>` 续接；支持 `review` 子命令做独立审查。
- **零第三方依赖**：纯 Python 3.10+ 标准库封装，自动解决非登录 Shell 找不到 `codex` 二进制、非 Git 目录报错等问题。

## 环境要求

- **Antigravity**（用户级 Skills 目录通常为 `~/.gemini/config/skills`）
- **OpenAI Codex CLI**（已安装并完成 `codex login` 登录）
- **Python 3.10+**

## 快速安装

```bash
git clone https://github.com/Silhouette-my/codex-in-anti.git
cd codex-in-anti
./install.sh
```

`./install.sh` 会自动完成：
1. 将 `codex-worker` Skill 安装到 `~/.gemini/config/skills/codex-worker/`。
2. 将 `profiles/worker.config.toml` 复制到 `~/.codex/worker.config.toml`（若已存在则默认跳过，加 `--force` 可覆盖）。

可通过环境变量自定义安装路径：

```bash
ANTIGRAVITY_SKILLS_DIR=/custom/skills CODEX_HOME=/custom/.codex ./install.sh
```

## 使用方式

### 1. 在 Antigravity 中自然语言触发

安装后，Antigravity 会自动发现 `codex-worker` Skill。你可以直接说：
- “把这部分模块实现派给 Codex 写，写完你核对 diff 并跑测试验收。”
- “用 Codex 对我当前未提交的代码做一轮只读 Code Review。”
- “让 GPT 在只读模式下帮我排查 `tests/test_api.py` 的报错原因。”

### 2. 直接调用封装脚本 `codex_run.py`

单次写入任务：

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py exec \
  -C /path/to/repo \
  --effort medium \
  --prompt "在 src/config.py 中实现 parse_config()，并运行 pytest tests/test_config.py 验证通过。"
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

对未提交的 Git 改动做只读 Code Review：

```bash
python3 ~/.gemini/config/skills/codex-worker/scripts/codex_run.py review \
  -C /path/to/repo \
  --uncommitted \
  --raw
```

### 常用参数说明

| 参数 | 说明 |
| :--- | :--- |
| `-C`, `--cwd` | Codex 工作目录（默认 `.`） |
| `--mode` | 沙箱模式：`write`（`workspace-write`）或 `read-only` |
| `-m`, `--model` | 临时覆盖使用的 Codex 模型 |
| `--effort` | 调整推理强度：`low` \| `medium` \| `high` |
| `--persist` | 持久化本次会话（关闭 `--ephemeral`，便于后续 `resume`） |
| `--add-dir` | 额外授权可写目录（可多次指定） |
| `--raw` | 关闭 JSON Schema 约束，直接返回纯文本 |
| `--search` | 开启实时联网检索 |
| `--no-worker-profile` | 跳过 `-p worker`，直接使用基础 `~/.codex/config.toml` |
| `--timeout` | 子进程超时时间（秒，默认 `600`） |
| `--log-dir` | 运行日志与交付产物存档目录（默认 `$TMPDIR/codex-worker-runs`） |

## 交付结构

每次执行后，脚本会在终端输出标准 JSON 摘要，并将 `codex.log`、`output.json`（或 `output.txt`）与 `meta.json` 归档至日志目录：

```json
{
  "ok": true,
  "exit_code": 0,
  "timed_out": false,
  "duration_sec": 12.98,
  "model": "gpt-...",
  "session_id": "...",
  "tokens_used": 22138,
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

## 实测性能对比

在 macOS 上使用 `codex-cli 0.171.0` 对同一模型、`model_reasoning_effort = "medium"`、相同 `--output-schema`、完全一致的 Prompt 与全新工作目录进行顺序实测，唯一变量为是否启用轻量 Profile（基线 `--no-worker-profile` 加载桌面插件、Hooks、Memories、`chronicle` 与 MCP Server，对照组默认使用 `-p worker`）：

| 任务 | 沙箱模式 | 基线耗时 | Worker 耗时 | 耗时变化 | 基线 Tokens | Worker Tokens | Token 降幅 | 上下文压缩 (基线/Worker) |
| :--- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| **T1**：只读分析 `codex_run.py` | `read-only` | 18.16s | 17.99s | -0.9% | 37,692 | 24,703 | **-34.5%** | 0 / 0 |
| **T2**：新建 `math_utils.py` + 6+ `assert` 自测 | `write` | 21.60s | 17.86s | **-17.3%** | 35,516 | 12,730 | **-64.2%** | 0 / 0 |
| **T3**：修复 `parser.py` Bug 并跑通 `test_parser.py` | `write` | 16.85s | 16.17s | -4.0% | 35,409 | 22,117 | **-37.5%** | 0 / 0 |
| **合计（3 项任务）** | - | **56.61s** | **52.02s** | **-8.1%** | **108,617** | **59,550** | **-45.2%** | 0 / 0 |

**日志对比分析：**
- 从运行日志可以看到，基线模式在每一步工具调用都会触发 `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` Hooks，加载桌面插件与 MCP 工具定义（T2 在空目录中甚至额外调用了桌面 MCP 的 glob 工具），并输出 `chronicle` 特性告警。
- 启用 `-p worker` 后，Codex 跳过了所有 Hooks、桌面插件、Memories 与重型 MCP，直接完成文件读写、补丁应用与测试验证（所有写任务在跑完后均复验通过）。
- 若宿主桌面配置中存在大量远程 OAuth MCP 或引发上下文压缩，耗时与 Token 的节省幅度还会进一步放大。

## 个性化调优

按需编辑 `~/.codex/worker.config.toml`：
- **固定默认模型**：取消注释 `model = "..."`（或在运行时通过 `-m` 传入）。
- **关闭个人重型 MCP**：如果你在 `~/.codex/config.toml` 中配置了额外的远程 MCP（如 Notion、Canva、Jira 等），可在 `worker.config.toml` 中添加 `[mcp_servers.<name>]\nenabled = false`，避免无头执行时出现 OAuth 报错或启动拖慢。

## 目录结构

```text
codex-in-anti/
├── install.sh                                  # 幂等安装脚本
├── profiles/
│   └── worker.config.toml                      # Codex CLI 轻量 Worker 配置 (-p worker)
├── skill/
│   └── codex-worker/
│       ├── SKILL.md                            # Antigravity 编排与验收 Skill
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
