#!/usr/bin/env python3
"""Unit tests for `install.py` and `uninstall.py`."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import install as installer

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestInstaller(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.codex_home = self.home / ".codex"
        self.plugins_dir = self.home / "plugins"
        self.data_dir = self.root / "plugin_data"
        for directory in (self.codex_home, self.plugins_dir, self.data_dir):
            directory.mkdir(parents=True)
        self.installed = self.plugins_dir / "agy-codex"

        self.repo = self.root / "source"
        self.source_plugin = self.repo / "plugin" / "agy-codex"
        for name in ("install.py", "uninstall.py", "profiles/worker.config.toml"):
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / name, dest)
        # 只复制实际 Hook 及最小依赖；UI 占位文件始终留在临时目录。
        original = REPO_ROOT / "plugin" / "agy-codex"
        for name in (
            "plugin.json", "hooks.json", "mcp_config.json", "rules/AGENTS.md",
            "skills/codex-dispatch/SKILL.md", "server/routing_hook.py", "server/codex_run.py",
            "server/codex_catalog.py", "server/codex_telemetry.py",
        ):
            dest = self.source_plugin / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / name, dest)
        assets = self.source_plugin / "assets"
        assets.mkdir()
        (assets / "app.html").write_text("<html></html>\n", encoding="utf-8", newline="\n")
        (self.source_plugin / "ui-src").mkdir()
        self.env = {
            **os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.codex_home), "AGY_CODEX_DATA_DIR": str(self.data_dir),
            "PLUGIN_DATA": str(self.data_dir), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _run(self, script: str, *args: str, io_encoding: str = "utf-8") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repo / script), *args],
            cwd=self.root,
            env={**self.env, "PYTHONIOENCODING": io_encoding},
            capture_output=True,
            encoding=io_encoding,
            timeout=30,
        )

    def _install(self, *args: str, io_encoding: str = "utf-8") -> subprocess.CompletedProcess[str]:
        return self._run(
            "install.py", "--plugins-dir", str(self.plugins_dir), "--codex-home", str(self.codex_home),
            "--data-dir", str(self.data_dir), "--skip-doctor", *args, io_encoding=io_encoding,
        )

    def _uninstall(self, *args: str, io_encoding: str = "utf-8") -> subprocess.CompletedProcess[str]:
        return self._run(
            "uninstall.py", "--plugins-dir", str(self.plugins_dir), "--data-dir", str(self.data_dir),
            *args, io_encoding=io_encoding,
        )

    def _assert_ok(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def _assert_refused(self, proc: subprocess.CompletedProcess[str], message: str) -> None:
        self.assertNotEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn(message, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def _make_symlink(self, link: Path, target: Path, *, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not available")

    def test_install_preserves_existing_worker_config_and_uninstall_keep_data(self) -> None:
        existing_worker = self.codex_home / "worker.config.toml"
        existing_worker.write_text('# custom user worker\nmodel = "pinned"\n', encoding="utf-8")
        config = self.codex_home / "config.toml"
        config.write_text('model = "pinned"\n', encoding="utf-8")
        user_cfg = self.data_dir / "workbench-config.json"
        user_cfg.write_text('{"defaults": {"effort": "xhigh"}}\n', encoding="utf-8")

        self._assert_ok(self._install())
        for name in (
            "plugin.json", "hooks.json", "server/routing_hook.py", "rules/AGENTS.md",
            "skills/codex-dispatch/SKILL.md", ".agy-codex-install.json",
        ):
            self.assertTrue((self.installed / name).is_file(), name)
        self.assertFalse((self.installed / "ui-src").exists())
        self.assertIn("custom user worker", existing_worker.read_text(encoding="utf-8"))

        self._assert_ok(self._uninstall("--keep-data"))
        self.assertFalse(self.installed.exists())
        self.assertTrue(user_cfg.is_file())
        self.assertTrue(config.is_file())

        self._assert_ok(self._uninstall())
        self.assertFalse(self.data_dir.exists())
        self.assertTrue(config.is_file())

    def test_commit_sha_receipt_prefers_explicit_value(self) -> None:
        receipt = self.installed / ".agy-codex-install.json"
        sha = "a" * 40
        self._assert_ok(self._install("--commit-sha", sha))
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["commitSha"], sha)

        (self.repo / ".git").mkdir()
        git_head = "b" * 40
        git_result = subprocess.CompletedProcess([], 0, stdout=f"{git_head}\n")
        with mock.patch.object(installer.subprocess, "run", return_value=git_result) as git:
            installer.install_plugin(self.repo, self.plugins_dir)
            git.assert_called_once()
            self.assertEqual(git.call_args.args[0], ["git", "-C", str(self.repo), "rev-parse", "HEAD"])
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["commitSha"], git_head)
            git.reset_mock()
            installer.install_plugin(self.repo, self.plugins_dir, commit_sha=sha)
            git.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["commitSha"], sha)

    def test_upgrade_preserves_profile_until_forced(self) -> None:
        self._assert_ok(self._install())
        existing_worker = self.codex_home / "worker.config.toml"
        existing_worker.write_text("# Keep this profile\n", encoding="utf-8")
        stale_file = self.installed / "removed-in-upgrade.txt"
        stale_file.write_text("old bundle", encoding="utf-8")
        self._assert_ok(self._install())
        self.assertEqual(existing_worker.read_text(encoding="utf-8"), "# Keep this profile\n")
        self.assertFalse(stale_file.exists())
        self._assert_ok(self._install("--force"))
        self.assertEqual(existing_worker.read_bytes(), (self.repo / "profiles/worker.config.toml").read_bytes())
        self.assertEqual(list(self.home.glob(".agy-codex-*")), [])

    def test_force_refuses_symlink_worker_profile(self) -> None:
        target = self.root / "target.toml"
        target.write_text("unchanged", encoding="utf-8")
        worker = self.codex_home / "worker.config.toml"
        self._make_symlink(worker, target)
        self._assert_refused(self._install("--force"), "symlink or reparse point")
        self.assertTrue(worker.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
        self.assertFalse(self.installed.exists())

    def test_force_refuses_directory_worker_profile(self) -> None:
        worker = self.codex_home / "worker.config.toml"
        worker.mkdir()
        self._assert_refused(self._install("--force"), "not a directory")
        self.assertEqual(list(worker.iterdir()), [])
        self.assertFalse(self.installed.exists())

    def test_refuses_source_plugin_destination(self) -> None:
        metadata = self.source_plugin / "plugin.json"
        original = metadata.read_bytes()
        for destination in (self.repo / "plugin", self.source_plugin):
            with self.subTest(destination=destination):
                self._assert_refused(self._install("--plugins-dir", str(destination)), "source plugin")
        self.assertEqual(metadata.read_bytes(), original)
        self.assertTrue((self.source_plugin / "assets/app.html").is_file())

    def test_refuses_unrelated_plugin_directory(self) -> None:
        self.installed.mkdir()
        metadata = self.installed / "plugin.json"
        metadata.write_text('{"name": "other-plugin"}', encoding="utf-8")
        marker = self.data_dir / "workbench-config.json"
        marker.touch()
        self._assert_refused(self._install(), "not marked as Agy Codex")
        self._assert_refused(self._uninstall(), "not marked as Agy Codex")
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["name"], "other-plugin")
        self.assertTrue(marker.is_file())

    def test_accepts_legacy_install_with_plugin_manifest(self) -> None:
        self.installed.mkdir()
        shutil.copy2(self.source_plugin / "plugin.json", self.installed / "plugin.json")
        self._assert_ok(self._install())
        (self.installed / ".agy-codex-install.json").unlink()
        self._assert_ok(self._uninstall())
        self.assertFalse(self.installed.exists())

    def test_refuses_symlink_plugin_and_data_directories(self) -> None:
        target = self.root / "linked-data"
        target.mkdir()
        marker = target / ".agy-codex-install.json"
        marker.write_text("{}", encoding="utf-8")
        self._make_symlink(self.installed, target, directory=True)
        self._assert_refused(self._install(), "symlink or reparse point")
        self._assert_refused(self._uninstall(), "symlink or reparse point")
        self.assertTrue(self.installed.is_symlink())
        self.installed.unlink()
        self._assert_ok(self._install())
        data_link = self.root / "data-link"
        self._make_symlink(data_link, target, directory=True)
        self._assert_refused(self._uninstall("--data-dir", str(data_link)), "symlink or reparse point")
        self.assertTrue(marker.is_file())
        self.assertTrue(self.installed.is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows junctions only")
    def test_refuses_windows_junction_plugin_directory(self) -> None:
        target = self.root / "junction-target"
        target.mkdir()
        marker = target / ".agy-codex-install.json"
        marker.write_text("{}", encoding="utf-8")
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(self.installed), str(target)],
            capture_output=True, timeout=30,
        )
        if proc.returncode:
            self.skipTest(f"Junction creation unavailable: {proc.stderr!r}")
        self._assert_refused(self._install(), "symlink or reparse point")
        self._assert_refused(self._uninstall(), "symlink or reparse point")
        self.assertTrue(marker.is_file())

    def test_failed_staging_preserves_old_install(self) -> None:
        self._assert_ok(self._install())
        old_receipt = (self.installed / ".agy-codex-install.json").read_bytes()
        old_hooks = (self.installed / "hooks.json").read_bytes()
        (self.source_plugin / "hooks.json").write_text("not JSON", encoding="utf-8")
        self._assert_refused(self._install(), "Installation failed")
        self.assertEqual((self.installed / "hooks.json").read_bytes(), old_hooks)
        self.assertEqual((self.installed / ".agy-codex-install.json").read_bytes(), old_receipt)
        self.assertEqual(list(self.home.glob(".agy-codex-*")), [])

    def test_failed_switch_restores_old_install(self) -> None:
        self._assert_ok(self._install())
        sentinel = self.installed / "old-install.txt"
        sentinel.touch()
        replace = os.replace

        def fail_switch(source: Path, target: Path) -> None:
            if source.name.startswith(".agy-codex-stage-"):
                self.assertFalse(self.installed.exists())
                self.assertEqual(source.parent, self.plugins_dir.parent)
                self.assertTrue((source / ".agy-codex-install.json").is_file())
                raise OSError("simulated switch failure")
            replace(source, target)

        with mock.patch.object(installer.os, "replace", side_effect=fail_switch):
            with self.assertRaisesRegex(OSError, "simulated switch failure"):
                installer.install_plugin(self.repo, self.plugins_dir)
        self.assertTrue(sentinel.is_file())
        self.assertEqual(list(self.home.glob(".agy-codex-*")), [])

    def test_unicode_paths_and_absolute_hook_commands(self) -> None:
        self.plugins_dir = self.home / "Plugin 安装"
        self.codex_home = self.home / "Codex 安装"
        self.data_dir = self.root / "Data 安装"
        self.data_dir.mkdir()
        (self.data_dir / "workbench-config.json").write_text('{"enabled": false}', encoding="utf-8")
        self.installed = self.plugins_dir / "agy-codex"
        self._assert_ok(self._install(io_encoding="ascii"))

        server = json.loads((self.installed / "mcp_config.json").read_text(encoding="utf-8"))
        py_cmd = server["mcpServers"]["codex"]["command"]
        self.assertEqual(py_cmd, sys.executable)
        script = self.installed / "server" / "routing_hook.py"
        self.assertTrue(Path(py_cmd).is_absolute())
        self.assertTrue(script.is_absolute())

        hooks = json.loads((self.installed / "hooks.json").read_text(encoding="utf-8"))["agy-codex-router"]
        self.assertTrue(hooks["enabled"])
        commands = {
            "SessionStart": hooks["SessionStart"][0]["command"],
            "PreToolUse": hooks["PreToolUse"][0]["hooks"][0]["command"],
        }
        blank_cwd = self.root / "empty cwd"
        blank_cwd.mkdir()
        env = {
            **self.env, "PATH": "", "CODEX_HOME": str(self.codex_home),
            "PLUGIN_DATA": str(self.data_dir), "AGY_CODEX_DATA_DIR": str(self.data_dir),
        }
        for event, command in commands.items():
            with self.subTest(event=event):
                expected = (
                    f'"{py_cmd}" "{script}" {event}'
                    if os.name == "nt"
                    else shlex.join((py_cmd, str(script), event))
                )
                self.assertEqual(command, expected)
                proc = subprocess.run(
                    command, shell=os.name != "nt", cwd=blank_cwd, env=env, input="{}",
                    capture_output=True, encoding="utf-8", timeout=30,
                )
                self._assert_ok(proc)
                result = json.loads(proc.stdout)
                if event == "SessionStart":
                    self.assertIn("enabled=false", result["injectSteps"][0]["systemMessage"]["systemMessage"])
                else:
                    self.assertEqual(result, {"decision": "allow"})

        self._assert_ok(self._uninstall(io_encoding="ascii"))
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.data_dir.exists())

    def test_doctor_path_output_with_ascii_redirection(self) -> None:
        self.codex_home = self.home / "Codex 安装"
        doctor = self.source_plugin / "server" / "codex_run.py"
        doctor.write_text(
            'import json, os\nprint(json.dumps({"home": os.environ["CODEX_HOME"]}, ensure_ascii=False))\n',
            encoding="utf-8",
            newline="\n",
        )
        proc = self._run(
            "install.py", "--plugins-dir", str(self.plugins_dir), "--codex-home", str(self.codex_home),
            "--data-dir", str(self.data_dir), io_encoding="ascii",
        )
        self._assert_ok(proc)
        self.assertIn("Codex ??", json.loads(proc.stdout.splitlines()[-1])["home"])

    def test_uninstall_rejects_protected_and_overlapping_data_before_deleting_plugin(self) -> None:
        self._assert_ok(self._install())
        plugin_child = self.installed / "nested-data"
        plugins_child = self.plugins_dir / "nested-data"
        codex_child = self.codex_home / "nested-data"
        for child in (plugin_child, plugins_child, codex_child):
            child.mkdir()
        for protected in (
            self.codex_home, self.home, self.root, self.plugins_dir, self.installed,
            plugin_child, plugins_child, codex_child,
        ):
            with self.subTest(protected=protected):
                marker = protected / "workbench-config.json"
                marker.touch()
                self._assert_refused(self._uninstall("--data-dir", str(protected)), "Refusing to remove")
                self.assertTrue(marker.is_file())
                self.assertTrue((self.installed / ".agy-codex-install.json").is_file())

    def test_uninstall_rejects_unmarked_nonempty_custom_data(self) -> None:
        self._assert_ok(self._install())
        unrelated = self.data_dir / "unrelated.txt"
        unrelated.touch()
        self._assert_refused(self._uninstall(), "not marked as Agy Codex")
        self.assertTrue(unrelated.is_file())
        self.assertTrue(self.installed.is_dir())

    def test_uninstall_preserves_nonempty_worktrees(self) -> None:
        self._assert_ok(self._install())
        worktree = self.data_dir / "worktrees" / "pending"
        worktree.mkdir(parents=True)
        uncommitted = worktree / "uncommitted.txt"
        uncommitted.write_text("keep me", encoding="utf-8")
        runs = self.data_dir / "runs"
        runs.mkdir()
        (runs / "state.json").touch()
        managed_file = self.data_dir / "stats-ledger.jsonl"
        managed_file.touch()
        unrelated = self.data_dir / "unrelated.txt"
        unrelated.touch()
        proc = self._uninstall()
        self._assert_ok(proc)
        self.assertIn("git worktree remove", proc.stdout)
        self.assertFalse(self.installed.exists())
        self.assertFalse(runs.exists())
        self.assertFalse(managed_file.exists())
        self.assertEqual(uncommitted.read_text(encoding="utf-8"), "keep me")
        self.assertTrue(unrelated.is_file())

    def test_uninstall_removes_empty_worktrees_directory(self) -> None:
        self._assert_ok(self._install())
        (self.data_dir / "worktrees").mkdir()
        self._assert_ok(self._uninstall())
        self.assertFalse(self.data_dir.exists())

    @unittest.skipUnless(os.name == "nt", "Windows directory CWD lock behavior only")
    def test_upgrade_in_place_when_windows_dir_rename_locked(self) -> None:
        self._assert_ok(self._install())
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(15)"],
            cwd=str(self.installed),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            proc = self._install()
            self._assert_ok(proc)
            self.assertIn("atomically in place", proc.stdout)
            self.assertTrue((self.installed / ".agy-codex-install.json").is_file())
        finally:
            holder.terminate()
            holder.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
