# Agy Codex (`agy-codex`)

[简体中文](README.md) | [English](README.en.md)

**A native Antigravity 2.0 ↔ OpenAI Codex CLI multi-model subagent workbench plugin.**

> Forked from [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti) under the MIT License. Building on its whitelist isolation (`--ignore-user-config`) and MCP App foundation, **Agy Codex** upgrades the integration into an out-of-the-box Antigravity plugin with dual-mode `model_catalog_json` & native model discovery, extended reasoning efforts, multi-provider delegation, built-in subagent orchestration skills, and a Powertools-inspired 3-tab native workbench.

---

## Highlights

Let **Antigravity** act as orchestrator, code reviewer, and final verifier while dispatching bounded work to any specified model in your local **Codex CLI**:

- **Out-of-the-Box Model Catalog, Multi-Provider & Full Effort Support**:
  - Whitelist-isolated execution (`--ignore-user-config`) skips heavy desktop plugins, MCP servers, hooks, and memories while cleanly forwarding `model`, `model_provider`, active provider settings, proxy env policy, `model_catalog_json` (resolving relative paths safely against `CODEX_HOME`), and context window settings.
  - Works seamlessly **with or without** `model_catalog_json` (official OpenAI Codex login, LiteLLM, or custom OpenAI-compatible gateways).
  - Supports extended reasoning efforts (`none / minimal / low / medium / high / xhigh / max / ultra` and custom catalog-declared levels) plus per-task `model`, `provider`, and role `preset` (`coding / review / debug / fast`) overrides.
- **Native 3-Tab Antigravity MCP App Workbench (Zero Client Injection, Update-Safe)**:
  - Uses Antigravity's official **MCP App** surfaces (**left sidebar full-page**, **right side pane**, and **inline chat card**) with zero Electron CDP injection—completely conflict-free with `agy-powertools` and resilient across Antigravity client updates.
  - **Subagents Monitor**: Active & historical runs, parallel Git worktree status, live collapsible process timeline, one-click stop (full process-tree termination), and click-to-open changed files in Antigravity's side pane.
  - **Usage Analytics**: Merges Antigravity-dispatched tasks with read-only local `~/.codex` sessions (`state_*.sqlite` + incremental JSONL cache) for token counts, cache hit rate, duration, success rate, daily trends, model/provider/effort breakdowns, and Codex spawn trees.
  - **Models & Config Center**: Searchable model catalog, one-click default model/provider/effort switching, role preset editor, and built-in `doctor` health check—without ever mutating `~/.codex/config.toml`.
- **Built-in Rules & Orchestration Skills**:
  - Ships a concise always-on `rules/AGENTS.md` plus three progressive-disclosure skills (`codex-dispatch`, `codex-parallel-worktree`, `codex-second-opinion`) for model discovery, safe parallel worktree fan-out, and cross-model adversarial code review.
- **Zero-Bloat Packaging**: 100% Python stdlib backend + pre-bundled single-file HTML UI (no Node.js required for end users).

> **Current Status**: Repository initialized with finalized architecture specification ([Design Doc](docs/superpowers/specs/2026-11-26-agy-codex-design.md)). Implementation starts upon design approval.

---

## Prerequisites

- **Antigravity 2.0** (discovers global plugins under `~/.gemini/config/plugins/`)
- **OpenAI Codex CLI** installed and authenticated (or configured with custom providers)
- **Python 3.10+** (**Python 3.11+** recommended for stdlib `tomllib` whitelist isolation; end users do not need Node.js)

---

## Installation & Uninstall

### Option 1: Copy-Paste Prompt for Your Agent (Recommended)

> Please install Agy Codex from https://github.com/Chino0w0/agy-codex . Read README.en.md and docs/INSTALL_AGENT.md first, run the cross-platform installer and `doctor` health check, keep my existing plugins, Codex config, and chat history untouched, and do not force-close the client. Report the doctor results and how to activate it when done.

See [docs/INSTALL_AGENT.md](docs/INSTALL_AGENT.md) for details.

### Option 2: Manual CLI Install

#### Windows (PowerShell)
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

### Uninstall

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

```bash
# macOS / Linux
sh ./uninstall.sh
```

Pass `--keep-data` to preserve your workbench presets and run history.

---

## Design Specification

- [docs/superpowers/specs/2026-11-26-agy-codex-design.md](docs/superpowers/specs/2026-11-26-agy-codex-design.md)

---

## Acknowledgements & License

- Upstream project: [Silhouette-my/codex-in-anti](https://github.com/Silhouette-my/codex-in-anti)
- License: [MIT License](LICENSE)
