#!/usr/bin/env python3
"""Unit and live-subprocess tests for `routing_hook.py` (SessionStart & PreToolUse)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "plugin" / "agy-codex"
SERVER_DIR = PLUGIN_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_catalog  # noqa: E402
import routing_hook  # noqa: E402


class TestRoutingHook(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.codex_home = self.root / ".codex"
        self.data_dir = self.root / "plugin_data"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        fake_py = self.root / "fake_codex.py"
        fake_py.write_text("print('codex-cli 0.172.0')\n", encoding="utf-8")
        if os.name == "nt":
            self.fake_bin = self.root / "fake_codex.cmd"
            self.fake_bin.write_text(f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
        else:
            self.fake_bin = self.root / "fake_codex.sh"
            self.fake_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
            self.fake_bin.chmod(0o755)

        (self.codex_home / "config.toml").write_text(
            'model = "gpt-5.4"\nmodel_provider = "openai"\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.codex_home / "auth.json").write_text('{"token": "ok"}\n', encoding="utf-8")

        self._old_env = {
            k: os.environ.get(k)
            for k in ("CODEX_HOME", "AGY_CODEX_DATA_DIR", "PLUGIN_DATA", "CODEX_BIN", "OPENAI_API_KEY")
        }
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["AGY_CODEX_DATA_DIR"] = str(self.data_dir)
        os.environ["PLUGIN_DATA"] = str(self.data_dir)
        os.environ["CODEX_BIN"] = str(self.fake_bin)
        os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _subagent_payload(self, type_name: str = "implementer", user_input: str = "修复登录页样式") -> dict[str, object]:
        return {
            "hookEventName": "PreToolUse",
            "toolCall": {
                "id": "tool_1",
                "name": "invoke_subagent",
                "args": {"TypeName": type_name, "Role": "Worker", "Prompt": "Do task"},
            },
            "lastUserInput": user_input,
        }

    def test_1_fresh_install_defaults_redirect_general_subagent(self) -> None:
        # No workbench-config.json yet; config.toml has compliant model gpt-5.4
        res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("implementer"), self.codex_home, self.data_dir
        )
        self.assertEqual(res["decision"], "deny")
        self.assertIn("gpt-5.4", res["reason"])
        self.assertIn("start_codex_task", res["reason"])

        research_res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("research"), self.codex_home, self.data_dir
        )
        self.assertEqual(research_res["decision"], "deny")

        ss = routing_hook.handle_session_start({}, self.codex_home, self.data_dir)
        msg = ss["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("默认路由已激活", msg)
        self.assertIn("gpt-5.4", msg)
        self.assertIn("未配置时不限总时长", msg)
        self.assertIn("timeout=120", msg)

    def test_2_switch_off_allows_native_subagent_immediately(self) -> None:
        codex_catalog.save_workbench_config({"enabled": False}, self.data_dir)
        res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("implementer"), self.codex_home, self.data_dir
        )
        self.assertEqual(res, {"decision": "allow"})
        ss = routing_hook.handle_session_start({}, self.codex_home, self.data_dir)
        self.assertIn("enabled=false", ss["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_3_intercept_subswitch_off_allows_native(self) -> None:
        codex_catalog.save_workbench_config(
            {"enabled": True, "intercept_native_subagents": False}, self.data_dir
        )
        res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("implementer"), self.codex_home, self.data_dir
        )
        self.assertEqual(res, {"decision": "allow"})
        ss = routing_hook.handle_session_start({}, self.codex_home, self.data_dir)
        self.assertIn("intercept_native_subagents=false", ss["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_4_builtin_and_custom_passthrough_subagents_allowed(self) -> None:
        for native_only in ("browser", "image-generator", "flutter_a11y_agent", "bundle-scrubber", "session-analyst"):
            res = routing_hook.handle_pre_tool_use(
                self._subagent_payload(native_only), self.codex_home, self.data_dir
            )
            self.assertEqual(res, {"decision": "allow"}, msg=f"{native_only} should pass through")

        codex_catalog.save_workbench_config(
            {"mapping": {"native_passthrough_subagents": ["browser", "custom-helper"]}},
            self.data_dir,
        )
        custom_res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("custom-helper"), self.codex_home, self.data_dir
        )
        self.assertEqual(custom_res, {"decision": "allow"})

    def test_5_explicit_user_native_override_allowed(self) -> None:
        for phrase in ("这个任务不用 Codex，直接用原生子代理", "please bypass codex for this step"):
            res = routing_hook.handle_pre_tool_use(
                self._subagent_payload("implementer", user_input=phrase),
                self.codex_home,
                self.data_dir,
            )
            self.assertEqual(res, {"decision": "allow"}, msg=f"Should allow for override: {phrase}")

    def test_6_unready_binary_or_auth_fails_open(self) -> None:
        # Missing auth -> fail-open allow + helpful SessionStart notice
        (self.codex_home / "auth.json").unlink()
        res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("implementer"), self.codex_home, self.data_dir
        )
        self.assertEqual(res, {"decision": "allow"})
        ss = routing_hook.handle_session_start({}, self.codex_home, self.data_dir)
        self.assertIn("自动放行 Antigravity 原生子代理", ss["injectSteps"][0]["systemMessage"]["systemMessage"])

        # Missing binary -> fail-open allow
        (self.codex_home / "auth.json").write_text('{"token": "ok"}\n', encoding="utf-8")
        os.environ["CODEX_BIN"] = str(self.root / "nonexistent-codex.exe")
        orig_which = routing_hook.codex_run.shutil.which
        orig_is_file = routing_hook.Path.is_file
        try:
            routing_hook.codex_run.shutil.which = lambda *_a, **_kw: None  # type: ignore[assignment]
            ready, reason, _ = routing_hook.check_codex_readiness(self.codex_home, self.data_dir)
            if not ready:
                self.assertIn("codex CLI", reason or "")
                res_no_bin = routing_hook.handle_pre_tool_use(
                    self._subagent_payload("implementer"), self.codex_home, self.data_dir
                )
                self.assertEqual(res_no_bin, {"decision": "allow"})
        finally:
            routing_hook.codex_run.shutil.which = orig_which  # type: ignore[assignment]
            routing_hook.Path.is_file = orig_is_file  # type: ignore[misc]

    def test_7_orchestrator_config_model_skipped_and_asks_list(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'model = "my-orchestrator-model"\nmodel_provider = "gw"\n[model_providers.gw]\nbase_url = "https://gw.example/v1"\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.codex_home / "auth.json").unlink()
        res = routing_hook.handle_pre_tool_use(
            self._subagent_payload("implementer"), self.codex_home, self.data_dir
        )
        self.assertEqual(res["decision"], "deny")
        self.assertNotIn("my-orchestrator-model", res["reason"])
        self.assertIn("list_codex_models", res["reason"])

        ss = routing_hook.handle_session_start({}, self.codex_home, self.data_dir)
        msg = ss["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertNotIn("my-orchestrator-model", msg)
        self.assertIn("list_codex_models", msg)

    def test_8_malformed_stdin_fails_open(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SERVER_DIR / "routing_hook.py"), "PreToolUse"],
            input="{not-valid-json",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout.strip()), {"decision": "allow"})

    def test_9_live_subprocess_session_start_and_pre_tool_use(self) -> None:
        p1 = subprocess.run(
            [sys.executable, str(SERVER_DIR / "routing_hook.py"), "SessionStart"],
            input=json.dumps({"conversationId": "conv-live-1", "hookEventName": "SessionStart"}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        self.assertEqual(p1.returncode, 0)
        out1 = json.loads(p1.stdout)
        self.assertIn("injectSteps", out1)

        p2 = subprocess.run(
            [sys.executable, str(SERVER_DIR / "routing_hook.py"), "PreToolUse"],
            input=json.dumps(self._subagent_payload("implementer")),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        self.assertEqual(p2.returncode, 0)
        out2 = json.loads(p2.stdout)
        self.assertEqual(out2["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
