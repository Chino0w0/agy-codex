# Agent 安装指南（Agy Codex）

适用于 Antigravity、Codex 或其他可执行本地命令的编码 Agent。安装过程纯本地执行，不修改 Antigravity 客户端安装包，不改写 `~/.codex/config.toml`，无需管理员权限，也无需安装 Node.js。

## 安装步骤

1. 确认项目根目录包含 `install.py`（Windows 可直接执行 `install.ps1`，macOS / Linux 可执行 `install.sh`）。
2. 检查本地可用 Python（要求 `>= 3.10`，推荐 `>= 3.11` 以启用 `tomllib` 白名单隔离；Windows 优先用 `python`，macOS / Linux 优先用 `python3`）。
3. 在项目根目录执行统一安装入口：
   - **Windows (PowerShell)**：`powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`（或 `python install.py`）
   - **macOS / Linux**：`sh ./install.sh`（或 `python3 install.py`）
4. 安装完成后，脚本会自动执行 `doctor` 环境自检并输出 JSON 报告，核对：
   - `~/.gemini/config/plugins/agy-codex/plugin.json` 已就位；
   - `python`、`codex_bin`、`config`、`schema` 检查通过（若未登录 Codex 或缺失配置，将 `hint` 清晰告知用户，不要自行捏造凭据）。
5. 保留用户所有其他插件、账户设置、聊天记录及已有 `~/.codex/worker.config.toml`；不要强制关闭正在承载当前对话的 Antigravity 客户端，也不必手动编辑 `~/.gemini/config/config.json`。
6. 若 Antigravity 正在运行，新安装的插件会自动被发现；如覆盖升级了已运行中的 MCP Server，可提示用户在 **MCP Servers** 设置中刷新 `agy-codex`（或重新打开 Antigravity）。

## 卸载步骤

- **Windows (PowerShell)**：`powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1`（保留预设与历史数据可加 `--keep-data`）
- **macOS / Linux**：`sh ./uninstall.sh`（或 `python3 uninstall.py --keep-data`）

卸载仅移除 `~/.gemini/config/plugins/agy-codex/`（以及未指定 `--keep-data` 时移除 `~/.gemini/antigravity/plugin_data/agy-codex/`），不会删除 `~/.codex/config.toml`、Codex 历史会话、其他 Antigravity 插件或聊天记录。
