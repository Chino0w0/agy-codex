#!/usr/bin/env python3
"""Cross-platform installer for codex-in-anti (macOS / Linux / Windows).

Installs:
1. The `codex-worker` Antigravity skill into `$ANTIGRAVITY_SKILLS_DIR/codex-worker`.
2. The lightweight `worker.config.toml` into `$CODEX_HOME/worker.config.toml` (unless present).
3. Runs `codex_run.py doctor` at the end unless `--skip-doctor` is passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the Antigravity codex-worker skill and Codex worker profile."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.codex/worker.config.toml if present.",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="Override target Antigravity skills directory (default: ~/.gemini/config/skills).",
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


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent

    skills_dir = Path(
        args.skills_dir
        or os.environ.get("ANTIGRAVITY_SKILLS_DIR")
        or (Path.home() / ".gemini" / "config" / "skills")
    ).expanduser()
    codex_home = Path(
        args.codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()

    target_skill_dir = skills_dir / "codex-worker"
    target_scripts_dir = target_skill_dir / "scripts"
    target_resources_dir = target_skill_dir / "resources"
    target_profile = codex_home / "worker.config.toml"

    print(f"[1/3] Installing codex-worker skill to {target_skill_dir}")
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

    if os.name != "nt":
        mode = dest_script.stat().st_mode
        dest_script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"[2/3] Installing Codex worker profile to {target_profile}")
    codex_home.mkdir(parents=True, exist_ok=True)
    if target_profile.is_file() and not args.force:
        print(f"  -> Skipped: {target_profile} already exists (re-run with --force to overwrite).")
    else:
        shutil.copy2(repo_root / "profiles" / "worker.config.toml", target_profile)
        print(f"  -> Installed {target_profile}")

    if args.skip_doctor:
        print("[3/3] Skipped doctor check (--skip-doctor).")
        return 0

    print("[3/3] Running `codex_run.py doctor`")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    proc = subprocess.run([sys.executable, str(dest_script), "doctor"], env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
