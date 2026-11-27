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
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


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


def _safe_print(msg: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        print(msg.encode(encoding, errors="replace").decode(encoding), file=stream)


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _validate_plugin_target(target: Path, source: Path) -> None:
    if _is_link_or_reparse_point(target):
        raise ValueError(f"Refusing to install over a symlink or reparse point: {target}")
    resolved = target.resolve()
    if resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise ValueError(f"Refusing to install over the source plugin or an overlapping directory: {target}")
    if not target.exists():
        return
    if not target.is_dir():
        raise ValueError(f"Plugin destination is not a directory: {target}")
    if not any(target.iterdir()):
        return
    receipt = target / ".agy-codex-install.json"
    if not _is_link_or_reparse_point(receipt) and receipt.is_file():
        return
    meta = target / "plugin.json"
    if not _is_link_or_reparse_point(meta) and meta.is_file():
        try:
            metadata = json.loads(meta.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
        else:
            if isinstance(metadata, dict) and metadata.get("name") == "agy-codex":
                return
    raise ValueError(f"Refusing to replace a non-empty directory not marked as Agy Codex: {target}")


def _rewrite_hook_commands(node: Any, py_cmd: str, script: Path) -> Any:
    prefix = "python3 server/routing_hook.py "
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == "command" and isinstance(value, str) and value.startswith(prefix):
                event = value[len(prefix):].strip()
                if os.name == "nt":
                    result[key] = f'"{py_cmd}" "{script}" {subprocess.list2cmdline([event])}'
                else:
                    result[key] = shlex.join((py_cmd, str(script), event))
            else:
                result[key] = _rewrite_hook_commands(value, py_cmd, script)
        return result
    if isinstance(node, list):
        return [_rewrite_hook_commands(item, py_cmd, script) for item in node]
    return node


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _build_plugin(repo_root: Path, src_plugin: Path, stage: Path, target_plugin: Path) -> None:
    plugin_meta = json.loads((src_plugin / "plugin.json").read_text(encoding="utf-8"))
    shutil.copy2(src_plugin / "plugin.json", stage / "plugin.json")

    py_cmd = sys.executable
    mcp_cfg = json.loads((src_plugin / "mcp_config.json").read_text(encoding="utf-8"))
    mcp_cfg["mcpServers"]["codex"]["command"] = py_cmd
    _write_json(stage / "mcp_config.json", mcp_cfg)

    receipt_files = [
        {"path": name, "sha256": sha256_file(stage / name)}
        for name in ("plugin.json", "mcp_config.json")
    ]

    src_hooks = src_plugin / "hooks.json"
    if src_hooks.is_file():
        hooks_cfg = json.loads(src_hooks.read_text(encoding="utf-8"))
        script = target_plugin / "server" / "routing_hook.py"
        _write_json(stage / "hooks.json", _rewrite_hook_commands(hooks_cfg, py_cmd, script))
        receipt_files.append({"path": "hooks.json", "sha256": sha256_file(stage / "hooks.json")})

    for sub in ("assets", "rules", "skills", "server"):
        src_sub = src_plugin / sub
        if src_sub.is_dir():
            for entry in copy_tree_clean(src_sub, stage / sub):
                receipt_files.append({"path": f"{sub}/{entry['path']}", "sha256": entry["sha256"]})

    for script_name in ("mcp_server.py", "codex_run.py", "routing_hook.py"):
        make_executable(stage / "server" / script_name)

    receipt = {
        "name": plugin_meta.get("name", "agy-codex"),
        "version": plugin_meta.get("version", "1.0.0"),
        "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sourceRoot": str(repo_root),
        "files": receipt_files,
    }
    _write_json(stage / ".agy-codex-install.json", receipt)


def _clean_temp_tree(path: Path) -> None:
    retries = 3 if os.name == "nt" else 0
    for attempt in range(retries + 1):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            if attempt == retries:
                _safe_print(f"Warning: Could not clean temporary directory {path}: {exc}", error=True)
                return
        time.sleep(0.1)


def install_plugin(repo_root: Path, plugins_dir: Path) -> Path:
    src_plugin = (repo_root / "plugin" / "agy-codex").resolve()
    plugins_dir = plugins_dir.resolve()
    target_plugin = plugins_dir / "agy-codex"
    _validate_plugin_target(target_plugin, src_plugin)

    if not (src_plugin / "plugin.json").is_file():
        raise FileNotFoundError(f"Missing plugin.json at {src_plugin}")

    app_html = src_plugin / "assets" / "app.html"
    if not app_html.is_file():
        raise FileNotFoundError(
            f"Missing prebuilt MCP App UI bundle at {app_html}. "
            "Run `npm install && npm run build` inside plugin/agy-codex/ui-src first."
        )

    plugins_dir.mkdir(parents=True, exist_ok=True)
    stage_prefix = ".agy-codex-stage-"
    stage = Path(tempfile.mkdtemp(prefix=f"{stage_prefix}{os.getpid()}-", dir=plugins_dir.parent))
    backup = stage.with_name(".agy-codex-backup-" + stage.name.removeprefix(stage_prefix))
    try:
        _build_plugin(repo_root, src_plugin, stage, target_plugin)
        _validate_plugin_target(target_plugin, src_plugin)
        # 暂存全部完成后才移动旧版本；切换失败时恢复原安装。
        had_old_plugin = target_plugin.exists()
        if had_old_plugin:
            if os.path.lexists(backup):
                raise FileExistsError(f"Plugin backup path is already occupied: {backup}")
            os.replace(target_plugin, backup)
        try:
            os.replace(stage, target_plugin)
        except OSError:
            if had_old_plugin:
                try:
                    if os.path.lexists(target_plugin):
                        raise FileExistsError(f"Plugin destination is already occupied: {target_plugin}")
                    os.replace(backup, target_plugin)
                except OSError as exc:
                    raise OSError(
                        f"Plugin switch and rollback failed; old installation retained at {backup}: {exc}"
                    ) from exc
            raise
        if had_old_plugin:
            _clean_temp_tree(backup)
    finally:
        if stage.exists():
            _clean_temp_tree(stage)
    return target_plugin / "server" / "codex_run.py"


def _validate_profile_target(target: Path) -> None:
    if _is_link_or_reparse_point(target):
        raise ValueError(f"Refusing to write worker profile to a symlink or reparse point: {target}")
    if target.exists() and not target.is_file():
        raise ValueError(f"Worker profile destination must be a regular file, not a directory or special file: {target}")


def _install_profile(source: Path, target: Path, *, force: bool) -> None:
    _validate_profile_target(target)
    exists = target.is_file()
    if exists and not force:
        _safe_print(f"  -> Skipped: {target} already exists (re-run with --force to overwrite).")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    temp = Path(name)
    try:
        os.close(fd)
        shutil.copy2(source, temp)
        _validate_profile_target(target)
        if target.is_file() and not force:
            _safe_print(f"  -> Skipped: {target} already exists (re-run with --force to overwrite).")
            return
        os.replace(temp, target)
        _safe_print(f"  -> Installed {target}")
    finally:
        temp.unlink(missing_ok=True)


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
    target_profile = codex_home / "worker.config.toml"
    _validate_profile_target(target_profile)

    _safe_print(f"[{step}/{total_steps}] Installing Agy Codex plugin to {plugins_dir / 'agy-codex'}")
    codex_run_path = install_plugin(repo_root, plugins_dir)
    step += 1

    legacy_plugin = plugins_dir / "codex-in-anti"
    if legacy_plugin.is_dir():
        _safe_print(
            f"  [note] Legacy plugin folder detected at {legacy_plugin} (left untouched; "
            "you may remove it manually if migrating)."
        )

    _safe_print(f"[{step}/{total_steps}] Installing Codex worker profile to {target_profile}")
    _install_profile(repo_root / "profiles" / "worker.config.toml", target_profile, force=args.force)
    step += 1

    if args.skip_doctor:
        _safe_print("Done! (Skipped doctor check.)")
        return 0

    _safe_print(f"[{step}/{total_steps}] Running `codex_run.py doctor`")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    env["PYTHONIOENCODING"] = f"{sys.stdout.encoding or 'utf-8'}:replace"
    if args.data_dir:
        env["AGY_CODEX_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    proc = subprocess.run([sys.executable, str(codex_run_path), "doctor"], env=env)
    return proc.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        _safe_print(f"Installation failed: {exc}", error=True)
        raise SystemExit(1) from None
