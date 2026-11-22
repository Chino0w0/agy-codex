#!/usr/bin/env python3
"""Cross-platform installer for codex-in-anti (macOS / Linux / Windows).

Installs by default:
1. The standalone `codex-worker` Antigravity skill into `$ANTIGRAVITY_SKILLS_DIR/codex-worker`.
2. The `codex-in-anti` Antigravity MCP App plugin into `$ANTIGRAVITY_PLUGINS_DIR/codex-in-anti`
   (self-contained: includes `mcp_server.py`, `codex_run.py`, `handoff_schema.json`, and `app.html`).
3. The lightweight `worker.config.toml` into `$CODEX_HOME/worker.config.toml` (unless present).
4. Runs `codex_run.py doctor` at the end unless `--skip-doctor` is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the Antigravity codex-worker skill, MCP App plugin, and Codex worker profile."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.codex/worker.config.toml if present.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--skill-only",
        action="store_true",
        help="Install only the standalone CLI skill (skip the MCP App plugin).",
    )
    mode_group.add_argument(
        "--plugin-only",
        action="store_true",
        help="Install only the Antigravity MCP App plugin (skip the standalone CLI skill).",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="Override target Antigravity skills directory (default: ~/.gemini/config/skills).",
    )
    parser.add_argument(
        "--plugins-dir",
        default=None,
        help="Override target Antigravity plugins directory (default: ~/.gemini/config/plugins).",
    )
    parser.add_argument(
        "--codex-home",
        default=None,
        help="Override target Codex home directory (default: ~/.codex).",
    )
    parser.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Skip running `codex_run.py doctor` after installation.",
    )
    return parser.parse_args()


def make_executable(path: Path) -> None:
    if os.name != "nt" and path.is_file():
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_skill(repo_root: Path, skills_dir: Path) -> Path:
    target_skill_dir = skills_dir / "codex-worker"
    target_scripts_dir = target_skill_dir / "scripts"
    target_resources_dir = target_skill_dir / "resources"

    target_scripts_dir.mkdir(parents=True, exist_ok=True)
    target_resources_dir.mkdir(parents=True, exist_ok=True)

    src_skill = repo_root / "skill" / "codex-worker"
    shutil.copy2(src_skill / "SKILL.md", target_skill_dir / "SKILL.md")
    dest_script = target_scripts_dir / "codex_run.py"
    shutil.copy2(src_skill / "scripts" / "codex_run.py", dest_script)
    shutil.copy2(
        src_skill / "resources" / "handoff_schema.json",
        target_resources_dir / "handoff_schema.json",
    )
    make_executable(dest_script)
    return dest_script


def install_plugin(repo_root: Path, plugins_dir: Path) -> Path:
    src_plugin = repo_root / "plugin" / "codex-in-anti"
    src_skill = repo_root / "skill" / "codex-worker"
    target_plugin = plugins_dir / "codex-in-anti"

    target_assets = target_plugin / "assets"
    target_skills = target_plugin / "skills" / "codex-worker-mcp"
    target_server = target_plugin / "server"

    target_assets.mkdir(parents=True, exist_ok=True)
    target_skills.mkdir(parents=True, exist_ok=True)
    target_server.mkdir(parents=True, exist_ok=True)

    shutil.copy2(src_plugin / "plugin.json", target_plugin / "plugin.json")

    mcp_cfg = json.loads((src_plugin / "mcp_config.json").read_text(encoding="utf-8"))
    if os.name == "nt":
        server_cfg = mcp_cfg.get("mcpServers", {}).get("codex_worker")
        if isinstance(server_cfg, dict) and server_cfg.get("command") == "python3":
            server_cfg["command"] = "python"
    (target_plugin / "mcp_config.json").write_text(
        json.dumps(mcp_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    shutil.copy2(src_plugin / "assets" / "logo.svg", target_assets / "logo.svg")
    app_html = src_plugin / "assets" / "app.html"
    if not app_html.is_file():
        raise FileNotFoundError(
            f"Missing prebuilt MCP App UI bundle at {app_html}. "
            "Run `npm install && node build.mjs` inside plugin/codex-in-anti/ui-src first."
        )
    shutil.copy2(app_html, target_assets / "app.html")

    shutil.copy2(
        src_plugin / "skills" / "codex-worker-mcp" / "SKILL.md",
        target_skills / "SKILL.md",
    )

    dest_mcp_server = target_server / "mcp_server.py"
    dest_codex_run = target_server / "codex_run.py"
    shutil.copy2(src_plugin / "server" / "mcp_server.py", dest_mcp_server)
    shutil.copy2(src_skill / "scripts" / "codex_run.py", dest_codex_run)
    shutil.copy2(
        src_skill / "resources" / "handoff_schema.json",
        target_server / "handoff_schema.json",
    )
    make_executable(dest_mcp_server)
    make_executable(dest_codex_run)
    return dest_codex_run


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent

    skills_dir = Path(
        args.skills_dir
        or os.environ.get("ANTIGRAVITY_SKILLS_DIR")
        or (Path.home() / ".gemini" / "config" / "skills")
    ).expanduser()
    plugins_dir = Path(
        args.plugins_dir
        or os.environ.get("ANTIGRAVITY_PLUGINS_DIR")
        or (Path.home() / ".gemini" / "config" / "plugins")
    ).expanduser()
    codex_home = Path(
        args.codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()

    install_both = not args.skill_only and not args.plugin_only
    do_skill = install_both or args.skill_only
    do_plugin = install_both or args.plugin_only

    doctor_script: Path | None = None
    step = 1
    total_steps = (1 if do_skill else 0) + (1 if do_plugin else 0) + 1 + (0 if args.skip_doctor else 1)

    if do_skill:
        print(f"[{step}/{total_steps}] Installing standalone skill to {skills_dir / 'codex-worker'}")
        doctor_script = install_skill(repo_root, skills_dir)
        step += 1

    if do_plugin:
        print(
            f"[{step}/{total_steps}] Installing Antigravity MCP App plugin to {plugins_dir / 'codex-in-anti'}"
        )
        plugin_codex_run = install_plugin(repo_root, plugins_dir)
        if doctor_script is None:
            doctor_script = plugin_codex_run
        step += 1

    target_profile = codex_home / "worker.config.toml"
    print(f"[{step}/{total_steps}] Installing Codex worker profile to {target_profile}")
    codex_home.mkdir(parents=True, exist_ok=True)
    if target_profile.is_file() and not args.force:
        print(f"  -> Skipped: {target_profile} already exists (re-run with --force to overwrite).")
    else:
        shutil.copy2(repo_root / "profiles" / "worker.config.toml", target_profile)
        print(f"  -> Installed {target_profile}")
    step += 1

    if args.skip_doctor or doctor_script is None:
        print("Done! (Skipped doctor check.)")
        return 0

    print(f"[{step}/{total_steps}] Running `codex_run.py doctor`")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    proc = subprocess.run([sys.executable, str(doctor_script), "doctor"], env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
