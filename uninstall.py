#!/usr/bin/env python3
"""Cross-platform uninstaller for Agy Codex (`agy-codex`).

Removes `$ANTIGRAVITY_PLUGINS_DIR/agy-codex` and (unless `--keep-data`)
`${PLUGIN_DATA}` (`~/.gemini/antigravity/plugin_data/agy-codex`).
Preserves non-empty worktrees. Never touches `~/.codex/config.toml`, `auth.json`, `state_*.sqlite`, other plugins,
or `~/.gemini/config/config.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uninstall the Agy Codex Antigravity plugin.")
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Preserve workbench-config.json, telemetry cache, and run logs.",
    )
    parser.add_argument(
        "--plugins-dir",
        default=None,
        help="Override Antigravity plugins directory (default: ~/.gemini/config/plugins).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override Agy Codex plugin data directory.",
    )
    return parser.parse_args()


_DATA_FILES = ("workbench-config.json", "stats-ledger.jsonl", "telemetry-cache.json")
_DATA_DIRS = ("runs", "worktrees")


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


def _has_plugin_marker(path: Path) -> bool:
    receipt = path / ".agy-codex-install.json"
    if not _is_link_or_reparse_point(receipt) and receipt.is_file():
        return True
    meta = path / "plugin.json"
    if _is_link_or_reparse_point(meta) or not meta.is_file():
        return False
    try:
        metadata = json.loads(meta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return isinstance(metadata, dict) and metadata.get("name") == "agy-codex"


def _has_data_marker(path: Path) -> bool:
    files = (path / name for name in _DATA_FILES)
    dirs = (path / name for name in _DATA_DIRS)
    return any(not _is_link_or_reparse_point(item) and item.is_file() for item in files) or any(
        not _is_link_or_reparse_point(item) and item.is_dir() for item in dirs
    )


def _validate_removable_dir(path: Path, *, kind: str, plugins_dir: Path, codex_home: Path) -> Path:
    if _is_link_or_reparse_point(path):
        raise ValueError(f"Refusing to remove a symlink or reparse point as {kind}: {path}")
    resolved = path.resolve()
    plugins_dir = plugins_dir.resolve()
    codex_home = codex_home.resolve()
    if (
        resolved == resolved.parent
        or resolved == Path.home().resolve()
        or codex_home.is_relative_to(resolved)
        or plugins_dir.is_relative_to(resolved)
    ):
        raise ValueError(f"Refusing to remove a protected directory as {kind}: {path}")
    if kind == "data directory":
        target_plugin = (plugins_dir / "agy-codex").resolve()
        if any(
            resolved.is_relative_to(protected) or protected.is_relative_to(resolved)
            for protected in (target_plugin, plugins_dir, codex_home)
        ):
            raise ValueError(f"Refusing to remove a data directory overlapping the plugin or Codex home: {path}")
    if not resolved.exists():
        return resolved
    if not resolved.is_dir():
        raise ValueError(f"Not a directory ({kind}): {path}")
    if kind == "plugin directory" and not _has_plugin_marker(resolved):
        raise ValueError(f"Refusing to remove a plugin directory not marked as Agy Codex: {path}")
    if kind == "data directory" and resolved.name != "agy-codex" and not _has_data_marker(resolved):
        if any(resolved.iterdir()):
            raise ValueError(f"Refusing to remove a non-empty data directory not marked as Agy Codex: {path}")
    return resolved


def _has_worktrees(data_dir: Path) -> bool:
    worktrees = data_dir / "worktrees"
    # 不遍历链接；未知类型也保留，避免误删重定向的工作树。
    return _is_link_or_reparse_point(worktrees) or (
        worktrees.exists() and (not worktrees.is_dir() or any(worktrees.iterdir()))
    )


def _remove_data_except_worktrees(data_dir: Path) -> None:
    for name in _DATA_FILES:
        item = data_dir / name
        if not _is_link_or_reparse_point(item) and item.is_file():
            item.unlink()
    runs = data_dir / "runs"
    if not _is_link_or_reparse_point(runs) and runs.is_dir():
        shutil.rmtree(runs)
    _safe_print(
        f"Warning: Preserved {data_dir / 'worktrees'}. "
        "Use `git worktree remove` to clean up the remaining worktrees, then rerun uninstall."
    )


def main() -> int:
    args = parse_args()
    plugins_dir = Path(
        args.plugins_dir
        or os.environ.get("ANTIGRAVITY_PLUGINS_DIR")
        or (Path.home() / ".gemini" / "config" / "plugins")
    ).expanduser()
    data_dir = Path(
        args.data_dir
        or os.environ.get("AGY_CODEX_DATA_DIR")
        or (Path.home() / ".gemini" / "antigravity" / "plugin_data" / "agy-codex")
    ).expanduser()

    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    # 在删除任何内容前检查所有将被删除的目录和工作树状态。
    target_plugin = _validate_removable_dir(
        plugins_dir / "agy-codex", kind="plugin directory", plugins_dir=plugins_dir, codex_home=codex_home
    )
    preserve_worktrees = False
    if not args.keep_data:
        data_dir = _validate_removable_dir(data_dir, kind="data directory", plugins_dir=plugins_dir, codex_home=codex_home)
        preserve_worktrees = _has_worktrees(data_dir)

    if target_plugin.exists():
        shutil.rmtree(target_plugin)
        _safe_print(f"Removed plugin directory: {target_plugin}")
    else:
        _safe_print(f"Plugin directory not present: {target_plugin}")

    if args.keep_data:
        _safe_print(f"Preserved plugin data directory: {data_dir}")
    elif preserve_worktrees:
        _remove_data_except_worktrees(data_dir)
    elif data_dir.exists():
        shutil.rmtree(data_dir)
        _safe_print(f"Removed plugin data directory: {data_dir}")
    else:
        _safe_print(f"Plugin data directory not present: {data_dir}")

    _safe_print("Agy Codex uninstalled. Codex config, sessions, and other plugins were untouched.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        _safe_print(f"Uninstallation failed: {exc}", error=True)
        raise SystemExit(1) from None
