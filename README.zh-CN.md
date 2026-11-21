# codex-in-anti

[English](README.md) | [简体中文](README.zh-CN.md)

让 **Antigravity** 作为编排者与验收者，通过 **OpenAI Codex CLI** 安全、低开销地指挥 **GPT** 完成有界编码、调试、重构与独立 Code Review 任务。

## 为什么做这个项目？

如果直接在非交互模式下调用 `codex exec`，它会默认加载桌面端 `~/.codex/config.toml` 里的全部 GUI 插件、重型 MCP Server、Hooks、Memories 以及嵌套 Subagent 指令。我们在一次简单 Python 任务实测中发现：
- **裸调 `codex exec`**：消耗约 **59.8 万 tokens**，触发 2 次上下文压缩，耗时 ~70 秒；
- **接入本项目的 `worker` Profile 后**：仅消耗 **2.2 万 tokens（直降约 96%）**，0 次压缩，耗时 **~13 秒**。

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
├── README.zh-CN.md
└── LICENSE
```

## 开源协议

[MIT](LICENSE)
