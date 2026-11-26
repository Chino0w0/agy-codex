#!/usr/bin/env python3
"""Unit tests for `install.py` and `uninstall.py`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.plugins_dir = self.root / "plugins"
        self.codex_home = self.root / ".codex"
        self.data_dir = self.root / "plugin_data"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Ensure app.html exists for isolated installer tests even before UI build if needed
        self.app_html = REPO_ROOT / "plugin" / "agy-codex" / "assets" / "app.html"
        self._created_placeholder_html = False
        if not self.app_html.is_file():
            self.app_html.write_text("<html><body>placeholder</body></html>", encoding="utf-8")
            self._created_placeholder_html = True

    def tearDown(self) -> None:
        if self._created_placeholder_html and self.app_html.is_file():
            self.app_html.unlink()
        self._tmp.cleanup()

    def test_install_preserves_existing_worker_config_and_uninstall_keep_data(self) -> None:
        existing_worker = self.codex_home / "worker.config.toml"
        existing_worker.write_text('# custom user worker\nmodel = "pinned"\n', encoding="utf-8")
        (self.codex_home / "config.toml").write_text('model = "pinned"\n', encoding="utf-8")
        user_cfg = self.data_dir / "workbench-config.json"
        user_cfg.write_text('{"defaults": {"effort": "xhigh"}}\n', encoding="utf-8")

        res = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "install.py"),
                "--plugins-dir",
                str(self.plugins_dir),
                "--codex-home",
                str(self.codex_home),
                "--data-dir",
                str(self.data_dir),
                "--skip-doctor",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr + res.stdout)

        installed = self.plugins_dir / "agy-codex"
        self.assertTrue((installed / "plugin.json").is_file())
        self.assertTrue((installed / "rules" / "AGENTS.md").is_file())
        self.assertTrue((installed / "skills" / "codex-dispatch" / "SKILL.md").is_file())
        self.assertTrue((installed / ".agy-codex-install.json").is_file())
        self.assertFalse((installed / "ui-src").exists())

        mcp_cfg = json.loads((installed / "mcp_config.json").read_text(encoding="utf-8"))
        expected_cmd = "python" if os.name == "nt" else "python3"
        self.assertEqual(mcp_cfg["mcpServers"]["codex"]["command"], expected_cmd)

        # Preserves existing worker.config.toml & plugin data
        self.assertIn("custom user worker", existing_worker.read_text(encoding="utf-8"))
        self.assertTrue(user_cfg.is_file())

        # Uninstall with --keep-data preserves data_dir and config.toml
        un_keep = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "uninstall.py"),
                "--plugins-dir",
                str(self.plugins_dir),
                "--data-dir",
                str(self.data_dir),
                "--keep-data",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(un_keep.returncode, 0)
        self.assertFalse(installed.exists())
        self.assertTrue(user_cfg.is_file())
        self.assertTrue((self.codex_home / "config.toml").is_file())

        # Full uninstall cleans data_dir too, while config.toml stays untouched
        un_full = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "uninstall.py"),
                "--plugins-dir",
                str(self.plugins_dir),
                "--data-dir",
                str(self.data_dir),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(un_full.returncode, 0)
        self.assertFalse(self.data_dir.exists())
        self.assertTrue((self.codex_home / "config.toml").is_file())


if __name__ == "__main__":
    unittest.main()
