#!/usr/bin/env python3
"""Cross-platform installer for Agy Codex (`agy-codex`).

Installs:
1. The `agy-codex` plugin bundle (`plugin.json`, `mcp_config.json`, `assets/`, `rules/`,
   `skills/`, `server/`) into `$ANTIGRAVITY_PLUGINS_DIR/agy-codex`.
2. The lightweight `worker.config.toml` into `$CODEX_HOME/worker.config.toml` (unless present).
3. Runs `codex_run.py doctor` unless `--skip-doctor` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the Agy Codex Antigravity plugin.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.codex/worker.config.toml if present.",
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
        "--data-dir",
        default=None,
        help="Override plugin data directory for doctor check.",
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_tree_clean(src: Path, dst: Path) -> list[dict[str, str]]:
    """Copy a directory tree (skipping __pycache__) and return file receipt entries."""
    receipt_files: list[dict[str, str]] = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel_root = Path(root).relative_to(src)
        target_root = dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for fname in files:
            if fname.endswith((".pyc", ".pyo")):
                continue
            src_file = Path(root) / fname
            dst_file = target_root / fname
            shutil.copy2(src_file, dst_file)
            rel_path = (rel_root / fname).as_posix()
            receipt_files.append({"path": rel_path, "sha256": sha256_file(dst_file)})
    return receipt_files


def install_plugin(repo_root: Path, plugins_dir: Path) -> Path:
    src_plugin = repo_root / "plugin" / "agy-codex"
    target_plugin = plugins_dir / "agy-codex"

    if not (src_plugin / "plugin.json").is_file():
        raise FileNotFoundError(f"Missing plugin.json at {src_plugin}")

    app_html = src_plugin / "assets" / "app.html"
    if not app_html.is_file():
        raise FileNotFoundError(
            f"Missing prebuilt MCP App UI bundle at {app_html}. "
            "Run `npm install && npm run build` inside plugin/agy-codex/ui-src first."
        )

    # Clean old installed runtime files while keeping directory safe
    if target_plugin.exists():
        for item in target_plugin.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    target_plugin.mkdir(parents=True, exist_ok=True)

    plugin_meta = json.loads((src_plugin / "plugin.json").read_text(encoding="utf-8"))
    shutil.copy2(src_plugin / "plugin.json", target_plugin / "plugin.json")

    mcp_cfg = json.loads((src_plugin / "mcp_config.json").read_text(encoding="utf-8"))
    if os.name == "nt":
        server_cfg = mcp_cfg.get("mcpServers", {}).get("codex")
        if isinstance(server_cfg, dict) and server_cfg.get("command") == "python3":
            server_cfg["command"] = "python"
    (target_plugin / "mcp_config.json").write_text(
        json.dumps(mcp_cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    receipt_files = [
        {"path": "plugin.json", "sha256": sha256_file(target_plugin / "plugin.json")},
        {"path": "mcp_config.json", "sha256": sha256_file(target_plugin / "mcp_config.json")},
    ]

    src_hooks = src_plugin / "hooks.json"
    if src_hooks.is_file():
        hooks_cfg = json.loads(src_hooks.read_text(encoding="utf-8"))
        if os.name == "nt" and isinstance(hooks_cfg, dict):
            def _adapt_cmd(node: Any) -> Any:
                if isinstance(node, dict):
                    return {
                        k: (
                            f"python {v[8:]}"
                            if k == "command" and isinstance(v, str) and v.startswith("python3 ")
                            else _adapt_cmd(v)
                        )
                        for k, v in node.items()
                    }
                if isinstance(node, list):
                    return [_adapt_cmd(item) for item in node]
                return node

            hooks_cfg = _adapt_cmd(hooks_cfg)
        (target_plugin / "hooks.json").write_text(
            json.dumps(hooks_cfg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        receipt_files.append(
            {"path": "hooks.json", "sha256": sha256_file(target_plugin / "hooks.json")}
        )

    for sub in ("assets", "rules", "skills", "server"):
        src_sub = src_plugin / sub
        if src_sub.is_dir():
            sub_files = copy_tree_clean(src_sub, target_plugin / sub)
            for f in sub_files:
                receipt_files.append({"path": f"{sub}/{f['path']}", "sha256": f["sha256"]})

    for script_name in ("mcp_server.py", "codex_run.py", "routing_hook.py"):
        make_executable(target_plugin / "server" / script_name)

    receipt = {
        "name": plugin_meta.get("name", "agy-codex"),
        "version": plugin_meta.get("version", "1.0.0"),
        "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceRoot": str(repo_root),
        "files": receipt_files,
    }
    (target_plugin / ".agy-codex-install.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target_plugin / "server" / "codex_run.py"


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent

    plugins_dir = Path(
        args.plugins_dir
        or os.environ.get("ANTIGRAVITY_PLUGINS_DIR")
        or (Path.home() / ".gemini" / "config" / "plugins")
    ).expanduser()
    codex_home = Path(
        args.codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()

    total_steps = 2 + (0 if args.skip_doctor else 1)
    step = 1

    print(f"[{step}/{total_steps}] Installing Agy Codex plugin to {plugins_dir / 'agy-codex'}")
    codex_run_path = install_plugin(repo_root, plugins_dir)
    step += 1

    legacy_plugin = plugins_dir / "codex-in-anti"
    if legacy_plugin.is_dir():
        print(
            f"  [note] Legacy plugin folder detected at {legacy_plugin} (left untouched; "
            "you may remove it manually if migrating)."
        )

    target_profile = codex_home / "worker.config.toml"
    print(f"[{step}/{total_steps}] Installing Codex worker profile to {target_profile}")
    codex_home.mkdir(parents=True, exist_ok=True)
    if target_profile.is_file() and not args.force:
        print(f"  -> Skipped: {target_profile} already exists (re-run with --force to overwrite).")
    else:
        shutil.copy2(repo_root / "profiles" / "worker.config.toml", target_profile)
        print(f"  -> Installed {target_profile}")
    step += 1

    if args.skip_doctor:
        print("Done! (Skipped doctor check.)")
        return 0

    print(f"[{step}/{total_steps}] Running `codex_run.py doctor`")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    if args.data_dir:
        env["AGY_CODEX_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    proc = subprocess.run([sys.executable, str(codex_run_path), "doctor"], env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
