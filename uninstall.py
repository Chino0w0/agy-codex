#!/usr/bin/env python3
"""Cross-platform uninstaller for Agy Codex (`agy-codex`).

Removes `$ANTIGRAVITY_PLUGINS_DIR/agy-codex` and (unless `--keep-data`)
`${PLUGIN_DATA}` (`~/.gemini/antigravity/plugin_data/agy-codex`).
Never touches `~/.codex/config.toml`, `auth.json`, `state_*.sqlite`, other plugins,
or `~/.gemini/config/config.json`.
"""

from __future__ import annotations

import argparse
import os
import shutil
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

    target_plugin = plugins_dir / "agy-codex"
    if target_plugin.exists():
        shutil.rmtree(target_plugin)
        print(f"Removed plugin directory: {target_plugin}")
    else:
        print(f"Plugin directory not present: {target_plugin}")

    if args.keep_data:
        print(f"Preserved plugin data directory: {data_dir}")
    elif data_dir.exists():
        shutil.rmtree(data_dir)
        print(f"Removed plugin data directory: {data_dir}")
    else:
        print(f"Plugin data directory not present: {data_dir}")

    print("Agy Codex uninstalled cleanly. Codex config, sessions, and other plugins were untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
