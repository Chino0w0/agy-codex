#!/usr/bin/env python3
"""End-to-end unit tests for `codex_run.py` and `mcp_server.py`."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SERVER_DIR = Path(__file__).resolve().parent.parent / "plugin" / "agy-codex" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_run  # noqa: E402
import mcp_server  # noqa: E402


FAKE_CODEX_SCRIPT = r"""
import json, sys, time
from pathlib import Path

args = sys.argv[1:]
out_file = None
sleep_sec = 0.05
for i, a in enumerate(args):
    if a == "-o" and i + 1 < len(args):
        out_file = Path(args[i + 1])
prompt = args[-1] if args else ""
if prompt == "-":
    prompt = sys.stdin.read()
if "SLEEP_LONG" in prompt:
    sleep_sec = 3.0

print(json.dumps({"type": "thread.started", "thread_id": "0199-fake-session-1"}), flush=True)
print(json.dumps({
    "type": "item.completed",
    "item": {"id": "cmd_1", "type": "command_execution", "command": "pwsh -c 'pytest -q'", "status": "completed", "exit_code": 0, "aggregated_output": "1 passed"}
}), flush=True)
time.sleep(sleep_sec)
handoff = {
    "status": "done",
    "summary": f"Handled: {prompt[:40]}",
    "changed_files": ["src/demo.py"],
    "verification": [{"command": "pytest -q", "passed": True, "notes": "1 passed"}],
    "risks": [],
    "next_steps": []
}
if out_file:
    out_file.write_text(json.dumps(handoff), encoding="utf-8")
print(json.dumps({
    "type": "item.completed",
    "item": {"id": "msg_1", "type": "agent_message", "text": json.dumps(handoff)}
}), flush=True)
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 1200, "cached_input_tokens": 600, "output_tokens": 150, "reasoning_output_tokens": 20}
}), flush=True)
"""


class TestMcpServerAndRunner(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.codex_home = self.root / ".codex"
        self.data_dir = self.root / "plugin_data"
        self.workdir = self.root / "repo"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workdir.mkdir(parents=True, exist_ok=True)

        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "demo/model-a"',
                    'model_provider = "gw"',
                    'model_reasoning_effort = "high"',
                    "",
                    "[model_providers.gw]",
                    'name = "Gateway"',
                    'base_url = "https://gw.example.com/v1"',
                    'experimental_bearer_token = "secret-gw-token-999"',
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        # Create a fake executable wrapper script invoking python with FAKE_CODEX_SCRIPT
        fake_py = self.root / "fake_codex.py"
        fake_py.write_text(FAKE_CODEX_SCRIPT, encoding="utf-8", newline="\n")
        if os.name == "nt":
            fake_bin = self.root / "fake_codex.cmd"
            fake_bin.write_text(f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
        else:
            fake_bin = self.root / "fake_codex.sh"
            fake_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
            fake_bin.chmod(0o755)
        self.fake_bin = fake_bin

        self._old_env = {
            "CODEX_HOME": os.environ.get("CODEX_HOME"),
            "AGY_CODEX_DATA_DIR": os.environ.get("AGY_CODEX_DATA_DIR"),
            "CODEX_BIN": os.environ.get("CODEX_BIN"),
        }
        os.environ["CODEX_HOME"] = str(self.codex_home)
        os.environ["AGY_CODEX_DATA_DIR"] = str(self.data_dir)
        os.environ["CODEX_BIN"] = str(self.fake_bin)

        self.manager = mcp_server.RunManager(data_dir=self.data_dir, codex_home=self.codex_home)
        self.server = mcp_server.McpServer(manager=self.manager)

    def tearDown(self) -> None:
        self.manager.shutdown()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_protocol_and_visibility_separation(self) -> None:
        init = self.server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "agy-codex")
        self.assertEqual(init["result"]["serverInfo"]["version"], "1.5.0")

        tools_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
        for model_tool in (
            "list_codex_models",
            "start_codex_task",
            "wait_codex_task",
            "get_codex_task_status",
            "stop_codex_task",
            "close_codex_task",
            "open_codex_panel",
        ):
            self.assertIn(model_tool, tools)
            self.assertNotEqual(
                ((tools[model_tool].get("_meta") or {}).get("ui") or {}).get("visibility"),
                ["app"],
            )

        for app_tool in (
            "start_user_task",
            "check_for_updates",
            "start_plugin_update",
            "get_update_status",
            "get_run_events",
            "list_runs",
            "close_run",
            "reopen_run",
            "get_history",
            "send_followup",
            "toggle_codex_enabled",
            "toggle_exclude_orchestrator",
            "get_stats",
            "get_workbench_config",
            "save_workbench_config",
            "preview_resolved_command",
            "run_doctor",
            "cancel_run",
        ):
            self.assertIn(app_tool, tools)
            self.assertEqual(tools[app_tool]["_meta"]["ui"]["visibility"], ["app"])

        start_props = tools["start_codex_task"]["inputSchema"]["properties"]
        for field_name in ("context_files", "acceptance_criteria", "non_goals"):
            self.assertIn(field_name, start_props)

        defined_names = {t["name"] for t in mcp_server.TOOLS_DEF}
        for removed_tool in ("list_local_sessions", "get_local_session_detail"):
            self.assertNotIn(removed_tool, tools)
            self.assertNotIn(removed_tool, defined_names)

        tab_prop = tools["open_codex_panel"]["inputSchema"]["properties"]["tab"]
        self.assertIn("history", tab_prop.get("enum", []))

        self.assertIsNone(((tools["start_codex_task"].get("_meta") or {}).get("ui") or {}).get("resourceUri"))
        self.assertEqual(tools["open_codex_panel"]["_meta"]["ui"]["resourceUri"], mcp_server.APP_URI)
        panel_meta = tools["open_codex_panel"]["_meta"]["com.google.antigravity/ui"]["entrypoints"]
        self.assertEqual({e["type"] for e in panel_meta}, {"aux_pane", "global"})

        res_read = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": mcp_server.APP_URI}}
        )
        self.assertEqual(res_read["result"]["contents"][0]["mimeType"], "text/html;profile=mcp-app")

    def test_user_direct_can_start_and_resume_while_disabled(self) -> None:
        self._call_tool_raw(4, "toggle_codex_enabled", {"enabled": False})
        args = {"prompt": "Hello Codex", "cwd": str(self.workdir), "mode": "write"}
        self.assertIn("error", self._call_tool_raw(5, "start_codex_task", {**args, "origin": "user"}))
        result = self._call_tool_raw(6, "start_user_task", args)["result"]["structuredContent"]
        self.assertEqual(result["origin"], "user")
        self.assertTrue(result["persist"])
        self.assertTrue(result["raw"])
        self.assertEqual(result["role"], "用户直连")
        self.assertEqual(result["parent_session_id"], "user-direct")
        self.assertEqual(result["parent_session_title"], "用户直连 Codex")
        self.assertFalse(any("Write task prompt is short" in message for message in result["warnings"]))
        self.assertEqual(result["runs"][0]["run_id"], result["run_id"])
        run_id = result["run_id"]
        done = self.manager.wait_runs([run_id], timeout=10)
        self.assertEqual(done["status"], "done")
        self.assertIsInstance(done["result"], str)
        self.assertEqual(done["session_id"], "0199-fake-session-1")

        # 从新 RunManager 的磁盘记录恢复，确保重启后仍保留直连属性。
        second = mcp_server.RunManager(data_dir=self.data_dir, codex_home=self.codex_home)
        self.addCleanup(second.shutdown)
        followup = second.send_followup(run_id, "Follow-up question")
        summary = followup.to_summary_dict()
        for key in ("origin", "raw", "role", "parent_session_id", "parent_session_title", "cwd", "mode", "model",
                    "provider", "effort", "preset", "config_mode", "persist"):
            self.assertEqual(summary[key], done[key], key)
        self.assertEqual(followup.resume_session_id, done["session_id"])
        self.assertTrue(followup.done_event.wait(10))
        self.assertEqual(followup.status, "done")

        for name in ("open_codex_panel", "list_runs", "get_workbench_config"):
            with self.subTest(name=name):
                payload = self._call_tool_raw(7, name, {})["result"]["structuredContent"]
                self.assertEqual(Path(payload["default_cwd"]).resolve(), self.workdir.resolve())
                if name == "get_workbench_config":
                    self.assertEqual(payload["plugin_version"], "1.5.0")

    def test_default_cwd_falls_back_outside_plugin(self) -> None:
        with mock.patch.object(Path, "cwd", return_value=self.workdir):
            self.assertEqual(self.manager.infer_default_cwd(), str(self.workdir))
        with mock.patch.object(Path, "cwd", return_value=mcp_server.PLUGIN_ROOT / "server"):
            self.assertEqual(self.manager.infer_default_cwd(), str(Path.home()))

    def test_update_tool_routes(self) -> None:
        updater = mcp_server.codex_updater
        root = mcp_server.PLUGIN_ROOT
        with mock.patch.object(updater, "check_for_updates", return_value={"latest_id": "1.6.0"}) as check:
            result = self._call_tool_raw(8, "check_for_updates", {"force": True, "channel": "release", "dismiss_version": "1.6.0"})
            self.assertEqual(result["result"]["structuredContent"], {"latest_id": "1.6.0"})
            check.assert_called_once_with(root, self.data_dir, force=True, channel="release", dismiss_version="1.6.0")
        with mock.patch.object(updater, "start_plugin_update", return_value={"status": "checking"}) as start:
            result = self._call_tool_raw(9, "start_plugin_update", {"channel": "main", "expected_version": "1.6.0"})
            self.assertEqual(result["result"]["structuredContent"], {"status": "checking"})
            start.assert_called_once_with(root, self.data_dir, channel="main", expected_version="1.6.0", codex_home=self.codex_home)
        result = self._call_tool_raw(10, "get_update_status", {})["result"]["structuredContent"]
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["current_version"], "1.5.0")

    def test_preview_doctor_models_and_empty_panel(self) -> None:
        models_res = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "list_codex_models", "arguments": {}}}
        )["result"]["structuredContent"]
        self.assertNotIn("secret-gw-token-999", json.dumps(models_res))
        self.assertTrue(any(p["id"] == "gw" for p in models_res["providers"]))

        preview_res = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "preview_resolved_command", "arguments": {"preset": "debug"}},
            }
        )["result"]["structuredContent"]
        self.assertNotIn("secret-gw-token-999", json.dumps(preview_res))
        self.assertIn("xhigh", json.dumps(preview_res))

        panel_res = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "open_codex_panel", "arguments": {}}}
        )["result"]["structuredContent"]
        self.assertEqual(panel_res["view"], "panel")
        self.assertEqual(panel_res["tab"], "subagents")

        doc_res = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "run_doctor", "arguments": {}}}
        )["result"]["structuredContent"]
        self.assertTrue(doc_res["checks"]["auth"]["ok"])
        self.assertNotIn("secret-gw-token-999", json.dumps(doc_res))

    def test_end_to_end_start_status_rolling_wait_and_stop(self) -> None:
        # Start two runs: one fast, one slow
        r_fast = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "start_codex_task",
                    "arguments": {"prompt": "Quick task", "cwd": str(self.workdir), "preset": "fast"},
                },
            }
        )["result"]["structuredContent"]
        r_slow = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "start_codex_task",
                    "arguments": {"prompt": "SLEEP_LONG task", "cwd": str(self.workdir), "mode": "read-only"},
                },
            }
        )["result"]["structuredContent"]

        # Non-blocking status check
        st = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {"name": "get_codex_task_status", "arguments": {"run_ids": [r_fast["run_id"], r_slow["run_id"]]}},
            }
        )["result"]["structuredContent"]
        self.assertEqual(len(st["runs"]), 2)

        # Rolling wait with wait_mode="any" returns fast run while slow is still running
        w_any = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 23,
                "method": "tools/call",
                "params": {
                    "name": "wait_codex_task",
                    "arguments": {"run_ids": [r_fast["run_id"], r_slow["run_id"]], "wait_mode": "any", "timeout": 10},
                },
            }
        )["result"]["structuredContent"]
        self.assertFalse(w_any["wait_timed_out"])
        self.assertIn(r_fast["run_id"], w_any["finished_ids"])

        # Waiting briefly on slow run returns wait_timed_out=True without failing it
        w_to = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 24,
                "method": "tools/call",
                "params": {"name": "wait_codex_task", "arguments": {"run_id": r_slow["run_id"], "timeout": 1}},
            }
        )["result"]["structuredContent"]
        self.assertTrue(w_to.get("wait_timed_out"))
        self.assertEqual(w_to.get("status"), "running")

        # Stop slow run cleanly
        stopped = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 25,
                "method": "tools/call",
                "params": {"name": "stop_codex_task", "arguments": {"run_id": r_slow["run_id"]}},
            }
        )["result"]["structuredContent"]
        self.assertEqual(stopped["status"], "cancelled")

        # Verify fast run completed with structured result and stripped pwsh wrapper
        ev_fast = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 26,
                "method": "tools/call",
                "params": {"name": "get_run_events", "arguments": {"run_id": r_fast["run_id"]}},
            }
        )["result"]["structuredContent"]
        self.assertEqual(ev_fast["status"], "done")
        self.assertEqual(ev_fast["tokens_used"], 1350)
        self.assertEqual(ev_fast["result"]["changed_files"], ["src/demo.py"])
        cmd_ev = next(e for e in ev_fast["events"] if e["kind"] == "command")
        self.assertEqual(cmd_ev["display_command"], "pytest -q")

        # Verify events.jsonl is stored separately and state.json does not embed events
        run_dir = self.data_dir / "runs" / r_fast["run_id"]
        state_disk = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("events", state_disk)
        events_file = run_dir / "events.jsonl"
        self.assertTrue(events_file.is_file())
        disk_events = [
            json.loads(line)
            for line in events_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(len(disk_events) >= 1)
        self.assertEqual(len(ev_fast["events"]), len(disk_events))

    def test_list_codex_models_never_lists_hidden_models(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "demo/visible-m"',
                    'model_provider = "gw"',
                    'model_catalog_json = "models-catelog.local.json"',
                    "",
                    "[model_providers.gw]",
                    'name = "Gateway"',
                    'base_url = "https://gw.example.com/v1"',
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.codex_home / "models-catelog.local.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "demo/visible-m",
                            "display_name": "Visible Model",
                            "visibility": "list",
                            "supported_reasoning_levels": ["low", "medium", "high"],
                        },
                        {
                            "slug": "demo/hidden-m",
                            "display_name": "Hidden Model",
                            "visibility": "hide",
                            "supported_reasoning_levels": ["low", "medium"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
            newline="\n",
        )

        tools_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 30, "method": "tools/list"})
        list_tool = next(t for t in tools_resp["result"]["tools"] if t["name"] == "list_codex_models")
        self.assertNotIn("include_hidden", list_tool["inputSchema"]["properties"])

        for call_id, args in ((31, {}), (32, {"include_hidden": True})):
            res = self.server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": call_id,
                    "method": "tools/call",
                    "params": {"name": "list_codex_models", "arguments": args},
                }
            )["result"]["structuredContent"]
            slugs = [m["slug"] for m in res["models"]]
            self.assertIn("demo/visible-m", slugs)
            self.assertNotIn("demo/hidden-m", slugs)
            self.assertNotIn("hidden_models_count", res)
            self.assertEqual(res["all_models_count"], res["visible_models_count"])

        wb_default = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 33, "method": "tools/call", "params": {"name": "get_workbench_config", "arguments": {}}}
        )["result"]["structuredContent"]
        self.assertNotIn("demo/hidden-m", [m["slug"] for m in wb_default["models"]])

        wb_inc = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 34,
                "method": "tools/call",
                "params": {"name": "get_workbench_config", "arguments": {"include_hidden": True}},
            }
        )["result"]["structuredContent"]
        self.assertIn("demo/hidden-m", [m["slug"] for m in wb_inc["models"]])

    def test_subagent_declaration_in_inherit_and_profile_modes(self) -> None:
        for cfg_mode in ("inherit", "profile"):
            cmd, _ = codex_run.build_codex_command(
                str(self.fake_bin),
                self.workdir,
                self.root / f"out-{cfg_mode}.json",
                codex_home=self.codex_home,
                data_dir=self.data_dir,
                cli_config_mode=cfg_mode,
                check_git=False,
            )
            joined = " ".join(cmd)
            self.assertIn("agents.enabled=false", joined)
            self.assertIn("features.multi_agent=false", joined)
            self.assertIn("SUBAGENT ROLE DECLARATION", joined)

        ephemeral_cmd, _ = codex_run.build_codex_command(
            str(self.fake_bin),
            self.workdir,
            self.root / "out-ephemeral.json",
            codex_home=self.codex_home,
            data_dir=self.data_dir,
            persist=False,
            check_git=False,
        )
        self.assertIn("--ephemeral", ephemeral_cmd)
        self.assertIn("--thread-source", ephemeral_cmd)
        ts_idx = ephemeral_cmd.index("--thread-source")
        self.assertEqual(ephemeral_cmd[ts_idx + 1], "subagent")

    def test_strip_shell_wrapper_windows_paths_and_blocked_handoff(self) -> None:
        self.assertEqual(
            codex_run.strip_shell_wrapper(
                '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "python --version"'
            ),
            "python --version",
        )
        self.assertEqual(
            codex_run.strip_shell_wrapper('"C:\\windows\\system32\\bash.exe" -lc \'ls -la\''),
            "ls -la",
        )

        wt_root = codex_run.get_worktree_root(self.data_dir)
        self.assertEqual(wt_root, (self.data_dir / "worktrees").resolve())

        blocked_py = self.root / "fake_blocked.py"
        blocked_py.write_text(
            '\n'.join(
                [
                    "import json, sys",
                    "from pathlib import Path",
                    "args = sys.argv[1:]",
                    "out_file = next((Path(args[i+1]) for i, a in enumerate(args) if a == '-o' and i + 1 < len(args)), None)",
                    'payload = {"status": "blocked", "summary": "sandbox policy rejected shell", "changed_files": [], "verification": [], "risks": [], "next_steps": []}',
                    "if out_file: out_file.write_text(json.dumps(payload), encoding='utf-8')",
                    'print(json.dumps({"type": "thread.started", "thread_id": "0199-blocked-1"}), flush=True)',
                    'print(json.dumps({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": json.dumps(payload)}}), flush=True)',
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        if os.name == "nt":
            blocked_bin = self.root / "fake_blocked.cmd"
            blocked_bin.write_text(f'@echo off\r\n"{sys.executable}" "{blocked_py}" %*\r\n', encoding="utf-8")
        else:
            blocked_bin = self.root / "fake_blocked.sh"
            blocked_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{blocked_py}" "$@"\n', encoding="utf-8")
            blocked_bin.chmod(0o755)
        os.environ["CODEX_BIN"] = str(blocked_bin)

        started = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 40,
                "method": "tools/call",
                "params": {"name": "start_codex_task", "arguments": {"prompt": "check", "cwd": str(self.workdir)}},
            }
        )["result"]["structuredContent"]
        waited = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "wait_codex_task", "arguments": {"run_id": started["run_id"], "timeout": 10}},
            }
        )["result"]["structuredContent"]
        self.assertEqual(waited["status"], "blocked")
        self.assertFalse(waited["ok"])
        self.assertIn("sandbox policy rejected shell", waited["error"])

    def test_multiline_prompt_delivered_via_stdin(self) -> None:
        multiline_prompt = "Line 1 header\nLine 2 BODY_OK\nLine 3 TAIL_MARKER"
        started = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {
                    "name": "start_codex_task",
                    "arguments": {"prompt": multiline_prompt, "cwd": str(self.workdir)},
                },
            }
        )["result"]["structuredContent"]
        waited = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 51,
                "method": "tools/call",
                "params": {"name": "wait_codex_task", "arguments": {"run_id": started["run_id"], "timeout": 10}},
            }
        )["result"]["structuredContent"]
        self.assertEqual(waited["status"], "done")
        self.assertIn("Line 2 BODY_OK", waited["result"]["summary"])

    def _call_tool_raw(self, req_id: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def _assert_rejected(self, resp: dict[str, object]) -> str:
        if "error" in resp:
            err_obj = resp["error"]
            msg = str(err_obj.get("message") or "")
            self.assertTrue(len(msg) > 0)
            return msg
        result = resp.get("result") or {}
        sc = result.get("structuredContent") or {}
        is_err = bool(result.get("isError")) or sc.get("ok") is False
        self.assertTrue(is_err, f"Expected tool call to be rejected, got: {resp!r}")
        msg = str(sc.get("error") or sc.get("message") or "")
        if not msg and isinstance(result.get("content"), list) and result["content"]:
            msg = str(result["content"][0].get("text") or "")
        self.assertTrue(len(msg) > 0)
        return msg

    def test_toggle_codex_enabled_blocks_and_reenables_start_codex_task(self) -> None:
        disabled = self._call_tool_raw(60, "toggle_codex_enabled", {"enabled": False})["result"]["structuredContent"]
        self.assertIs(disabled["enabled"], False)

        blocked_resp = self._call_tool_raw(
            61,
            "start_codex_task",
            {"prompt": "Should fail while disabled", "cwd": str(self.workdir)},
        )
        err_msg = self._assert_rejected(blocked_resp)
        self.assertTrue(
            any(tok in err_msg.lower() for tok in ("disabled", "paused", "enabled", "暂停", "关闭")),
            f"Unexpected toggle block message: {err_msg!r}",
        )

        reenabled = self._call_tool_raw(62, "toggle_codex_enabled", {"enabled": True})["result"]["structuredContent"]
        self.assertIs(reenabled["enabled"], True)

        started = self._call_tool_raw(
            63,
            "start_codex_task",
            {"prompt": "Allowed after re-enable", "cwd": str(self.workdir)},
        )["result"]["structuredContent"]
        waited = self._call_tool_raw(
            64,
            "wait_codex_task",
            {"run_id": started["run_id"], "timeout": 10},
        )["result"]["structuredContent"]
        self.assertEqual(waited["status"], "done")

    def test_close_codex_task_history_grouping_running_guard_and_reopen(self) -> None:
        finished_start = self._call_tool_raw(
            70,
            "start_codex_task",
            {
                "prompt": "Implement auth helper",
                "cwd": str(self.workdir),
                "title": "Auth refactor",
                "role": "coding",
                "conversation_id": "conv-123",
                "conversation_title": "Main Feature Thread",
            },
        )["result"]["structuredContent"]
        fast_id = finished_start["run_id"]
        waited = self._call_tool_raw(71, "wait_codex_task", {"run_id": fast_id, "timeout": 10})["result"]["structuredContent"]
        self.assertEqual(waited["status"], "done")

        slow_start = self._call_tool_raw(
            72,
            "start_codex_task",
            {"prompt": "SLEEP_LONG running guard", "cwd": str(self.workdir)},
        )["result"]["structuredContent"]
        slow_id = slow_start["run_id"]

        # Must refuse closing a still-running run
        refused_resp = self._call_tool_raw(73, "close_codex_task", {"run_id": slow_id})
        refused_msg = self._assert_rejected(refused_resp)
        self.assertTrue(
            any(tok in refused_msg.lower() for tok in ("running", "stop", "运行")),
            f"Unexpected running close refusal message: {refused_msg!r}",
        )
        self._call_tool_raw(74, "stop_codex_task", {"run_id": slow_id})

        # Closing finished run sets closed_by="agent" and removes it from default list_runs
        close_response = self._call_tool_raw(75, "close_codex_task", {"run_id": fast_id})
        self.assertIn("result", close_response, close_response)
        closed = close_response["result"]["structuredContent"]
        self.assertEqual(closed["closed_by"], "agent")
        self.assertIsNotNone(closed.get("closed_at"))

        active_runs = self._call_tool_raw(76, "list_runs", {})["result"]["structuredContent"]["runs"]
        self.assertNotIn(fast_id, [r["run_id"] for r in active_runs])

        all_runs = self._call_tool_raw(77, "list_runs", {"include_closed": True})["result"]["structuredContent"]["runs"]
        self.assertIn(fast_id, [r["run_id"] for r in all_runs])

        # Inspectable in get_history grouped by parent_session_id / parent_session_title
        hist = self._call_tool_raw(78, "get_history", {})["result"]["structuredContent"]
        groups = hist.get("groups") or []
        group = next((g for g in groups if g.get("parent_session_id") == "conv-123"), None)
        self.assertIsNotNone(group, f"Missing conv-123 group in history: {hist!r}")
        self.assertEqual(group["parent_session_title"], "Main Feature Thread")
        hist_run = next((r for r in group["runs"] if r["run_id"] == fast_id), None)
        self.assertIsNotNone(hist_run)
        self.assertEqual(hist_run["role"], "coding")
        self.assertEqual(hist_run["closed_by"], "agent")
        self.assertTrue(bool(hist_run.get("work_digest")))

        # reopen_run restores the run to active monitor
        reopened = self._call_tool_raw(79, "reopen_run", {"run_id": fast_id})["result"]["structuredContent"]
        self.assertIsNone(reopened.get("closed_at"))
        active_after = self._call_tool_raw(80, "list_runs", {})["result"]["structuredContent"]["runs"]
        self.assertIn(fast_id, [r["run_id"] for r in active_after])

    def test_concurrent_wait_does_not_block_ui_and_close_requests(self) -> None:
        fast_start = self._call_tool_raw(
            90,
            "start_codex_task",
            {"prompt": "Fast concurrent task", "cwd": str(self.workdir), "preset": "fast"},
        )["result"]["structuredContent"]
        fast_id = fast_start["run_id"]
        fast_done = self._call_tool_raw(
            91, "wait_codex_task", {"run_id": fast_id, "timeout": 10}
        )["result"]["structuredContent"]
        self.assertEqual(fast_done["status"], "done")

        slow_start = self._call_tool_raw(
            92,
            "start_codex_task",
            {"prompt": "SLEEP_LONG concurrent wait", "cwd": str(self.workdir), "mode": "read-only"},
        )["result"]["structuredContent"]
        slow_id = slow_start["run_id"]

        req_r_fd, req_w_fd = os.pipe()
        resp_r_fd, resp_w_fd = os.pipe()
        r_stream = os.fdopen(req_r_fd, "r", encoding="utf-8", buffering=1)
        req_writer = os.fdopen(req_w_fd, "w", encoding="utf-8", buffering=1)
        w_stream = os.fdopen(resp_w_fd, "w", encoding="utf-8", buffering=1)
        resp_reader = os.fdopen(resp_r_fd, "r", encoding="utf-8", buffering=1)

        resp_q: queue.Queue[tuple[float, dict[str, object]]] = queue.Queue()

        def read_responses() -> None:
            for raw in resp_reader:
                line = raw.strip()
                if not line:
                    continue
                resp_q.put((time.monotonic(), json.loads(line)))

        srv_thread = threading.Thread(
            target=mcp_server.serve_stdio,
            args=(self.server, r_stream, w_stream),
            daemon=True,
        )
        reader_thread = threading.Thread(target=read_responses, daemon=True)
        srv_thread.start()
        reader_thread.start()

        try:
            t0 = time.monotonic()
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 100,
                    "method": "tools/call",
                    "params": {"name": "wait_codex_task", "arguments": {"run_id": slow_id, "timeout": 10}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 101,
                    "method": "tools/call",
                    "params": {"name": "list_runs", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 102,
                    "method": "tools/call",
                    "params": {"name": "get_run_events", "arguments": {"run_id": fast_id}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 103,
                    "method": "tools/call",
                    "params": {"name": "close_run", "arguments": {"run_id": fast_id}},
                },
            ]
            req_writer.write(json.dumps(requests[0]) + "\n")
            req_writer.flush()
            time.sleep(0.05)
            for req in requests[1:]:
                req_writer.write(json.dumps(req) + "\n")
            req_writer.flush()

            arrival_order: list[int] = []
            arrivals: dict[int, float] = {}
            responses: dict[int, dict[str, object]] = {}
            for _ in range(4):
                ts, msg = resp_q.get(timeout=10.0)
                rid = int(msg["id"])  # type: ignore[arg-type]
                arrival_order.append(rid)
                arrivals[rid] = ts - t0
                responses[rid] = msg

            for rid in (100, 101, 102, 103):
                self.assertIn("result", responses[rid], f"Unexpected response for {rid}: {responses[rid]!r}")

            self.assertEqual(responses[100]["result"]["structuredContent"]["status"], "done")
            self.assertIn(fast_id, responses[103]["result"]["structuredContent"]["closed_ids"])
            for interactive_id in (101, 102, 103):
                self.assertLess(arrivals[interactive_id], 1.5)
                self.assertLess(
                    arrival_order.index(interactive_id),
                    arrival_order.index(100),
                    f"Interactive request {interactive_id} waited for wait_codex_task: order={arrival_order}",
                )
        finally:
            try:
                self._call_tool_raw(104, "stop_codex_task", {"run_id": slow_id})
            except Exception:
                pass
            for stream in (req_writer, r_stream, w_stream, resp_reader):
                try:
                    stream.close()
                except Exception:
                    pass
            srv_thread.join(timeout=5.0)
            reader_thread.join(timeout=5.0)

    def test_running_state_persists_pid_and_survives_fresh_manager_read(self) -> None:
        started = self._call_tool_raw(
            110,
            "start_codex_task",
            {"prompt": "SLEEP_LONG pid persistence", "cwd": str(self.workdir), "mode": "read-only"},
        )["result"]["structuredContent"]
        run_id = started["run_id"]
        state_path = self.data_dir / "runs" / run_id / "state.json"

        try:
            state_disk: dict[str, object] = {}
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                if state_path.is_file():
                    try:
                        loaded = json.loads(state_path.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict) and isinstance(loaded.get("pid"), int):
                            state_disk = loaded
                            break
                    except Exception:
                        pass
                time.sleep(0.05)

            self.assertIsInstance(state_disk.get("pid"), int)
            self.assertGreater(int(state_disk["pid"]), 0)  # type: ignore[arg-type]
            for key in ("updated_at", "timeout_sec", "remaining_timeout_sec"):
                self.assertIn(key, state_disk, f"Missing {key!r} in state.json: {state_disk!r}")

            fresh_manager = mcp_server.RunManager(data_dir=self.data_dir, codex_home=self.codex_home)
            fresh_runs = fresh_manager.list_runs(limit=20)
            live_run = next((r for r in fresh_runs if r["run_id"] == run_id), None)
            self.assertIsNotNone(live_run)
            self.assertEqual(live_run["status"], "running")
        finally:
            self._call_tool_raw(111, "stop_codex_task", {"run_id": run_id})

    def test_prompt_txt_full_retrieval_context_files_and_short_write_warning(self) -> None:
        spec_file = self.workdir / "spec.md"
        spec_file.write_text("# Contract Spec\nEndpoint: /api/v1/items\n", encoding="utf-8", newline="\n")

        long_prompt = "Implement full workflow context. " + ("DETAIL_" * 90) + " TAIL_END_MARKER"
        self.assertGreater(len(long_prompt), 500)

        started = self._call_tool_raw(
            120,
            "start_codex_task",
            {
                "prompt": long_prompt,
                "cwd": str(self.workdir),
                "mode": "write",
                "context_files": ["spec.md"],
                "acceptance_criteria": ["python -m unittest passes"],
                "non_goals": ["Do not touch frontend"],
            },
        )["result"]["structuredContent"]
        run_id = started["run_id"]
        waited = self._call_tool_raw(
            121, "wait_codex_task", {"run_id": run_id, "timeout": 10}
        )["result"]["structuredContent"]
        self.assertEqual(waited["status"], "done")

        prompt_txt = self.data_dir / "runs" / run_id / "prompt.txt"
        self.assertTrue(prompt_txt.is_file())
        prompt_text = prompt_txt.read_text(encoding="utf-8")
        self.assertIn("TAIL_END_MARKER", prompt_text)
        self.assertIn("spec.md", prompt_text)
        self.assertIn("python -m unittest passes", prompt_text)
        self.assertIn("Do not touch frontend", prompt_text)

        listed = self._call_tool_raw(122, "list_runs", {})["result"]["structuredContent"]["runs"]
        run_item = next(r for r in listed if r["run_id"] == run_id)
        self.assertNotIn("prompt", run_item)
        self.assertLessEqual(len(run_item["prompt_preview"]), 180)
        self.assertNotIn("TAIL_END_MARKER", run_item["prompt_preview"])

        events_res = self._call_tool_raw(
            123, "get_run_events", {"run_id": run_id}
        )["result"]["structuredContent"]
        self.assertIn("[READ-FIRST CONTEXT FILES]", events_res["prompt"])
        self.assertIn("TAIL_END_MARKER", events_res["prompt"])

        short_start = self._call_tool_raw(
            124,
            "start_codex_task",
            {"prompt": "fix bug", "cwd": str(self.workdir), "mode": "write"},
        )["result"]["structuredContent"]
        self.assertTrue(
            len(short_start.get("warnings") or []) >= 1,
            f"Expected warning for short write prompt without context_files, got: {short_start!r}",
        )
        self._call_tool_raw(125, "wait_codex_task", {"run_id": short_start["run_id"], "timeout": 10})

    def test_partial_handoff_status_and_toggle_exclude_orchestrator(self) -> None:
        partial_py = self.root / "fake_partial.py"
        partial_py.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "from pathlib import Path",
                    "args = sys.argv[1:]",
                    "out_file = next((Path(args[i+1]) for i, a in enumerate(args) if a == '-o' and i + 1 < len(args)), None)",
                    'payload = {"status": "partial", "summary": "only edited 1 of 2 files", "changed_files": ["a.py"], "verification": [], "risks": ["b.py pending"], "next_steps": ["finish b.py"]}',
                    "if out_file: out_file.write_text(json.dumps(payload), encoding='utf-8')",
                    'print(json.dumps({"type": "thread.started", "thread_id": "0199-partial-1"}), flush=True)',
                    'print(json.dumps({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": json.dumps(payload)}}), flush=True)',
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        if os.name == "nt":
            partial_bin = self.root / "fake_partial.cmd"
            partial_bin.write_text(f'@echo off\r\n"{sys.executable}" "{partial_py}" %*\r\n', encoding="utf-8")
        else:
            partial_bin = self.root / "fake_partial.sh"
            partial_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{partial_py}" "$@"\n', encoding="utf-8")
            partial_bin.chmod(0o755)
        os.environ["CODEX_BIN"] = str(partial_bin)

        started = self._call_tool_raw(
            130,
            "start_codex_task",
            {"prompt": "partial check", "cwd": str(self.workdir)},
        )["result"]["structuredContent"]
        waited = self._call_tool_raw(
            131, "wait_codex_task", {"run_id": started["run_id"], "timeout": 10}
        )["result"]["structuredContent"]
        self.assertEqual(waited["status"], "partial")
        self.assertFalse(waited["ok"])
        history = self.manager.get_history(filter_mode="issue")
        self.assertIn(started["run_id"], [r["run_id"] for g in history["groups"] for r in g["runs"]])
        self.assertEqual(history["groups"][0]["issue_count"], 1)
        for index, panel_meta in enumerate(
            (None, {"run_id": "cli-partial-1", "cwd": str(self.workdir), "prompt": "partial"})
        ):
            with self.subTest(panel_meta=panel_meta):
                output = self.root / f"cli-output-{index}.json"
                result = codex_run.run_codex(
                    [sys.executable, str(partial_py), "-o", str(output)], self.root / f"cli-{index}.log", output,
                    0, quiet=True, panel_meta=panel_meta, data_dir=self.data_dir,
                )
                self.assertEqual(result["status"], "partial")
                self.assertFalse(result["ok"])

        self._call_tool_raw(
            132,
            "save_workbench_config",
            {
                "exclude_orchestrator_models": True,
                "defaults": {"model": "demo/custom-keep", "effort": "high"},
                "mapping": {"retention_hours": 48, "max_concurrent_runs": 3},
            },
        )
        toggled = self._call_tool_raw(
            133, "toggle_exclude_orchestrator", {"enabled": False}
        )["result"]["structuredContent"]
        self.assertIs(toggled["exclude_orchestrator_models"], False)

        wb_after = self._call_tool_raw(134, "get_workbench_config", {})["result"]["structuredContent"]
        cfg = wb_after["workbench_config"]
        self.assertIs(cfg["exclude_orchestrator_models"], False)
        self.assertEqual(cfg["defaults"]["model"], "demo/custom-keep")
        self.assertEqual(cfg["defaults"]["effort"], "high")
        self.assertEqual(cfg["mapping"]["retention_hours"], 48)
        self.assertEqual(cfg["mapping"]["max_concurrent_runs"], 3)

        toggled_int = self._call_tool_raw(
            135, "toggle_intercept_native_subagents", {"enabled": False}
        )["result"]["structuredContent"]
        self.assertIs(toggled_int["intercept_native_subagents"], False)

        saved_pass = self._call_tool_raw(
            136,
            "save_workbench_config",
            {"mapping": {"native_passthrough_subagents": ["browser", "custom-native"]}},
        )["result"]["structuredContent"]
        self.assertEqual(
            saved_pass["workbench_config"]["mapping"]["native_passthrough_subagents"],
            ["browser", "custom-native"],
        )
        self.assertIs(saved_pass["workbench_config"]["intercept_native_subagents"], False)

    def test_stdio_get_workbench_config_and_doctor_no_deadlock(self) -> None:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        env["CODEX_BIN"] = str(self.fake_bin)
        proc = subprocess.Popen(
            [
                sys.executable,
                str(SERVER_DIR / "mcp_server.py"),
                "--data-dir",
                str(self.data_dir),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None

        def rpc(payload: dict[str, object], timeout_sec: float = 5.0) -> dict[str, object]:
            proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            proc.stdin.flush()
            box: list[bytes] = []
            reader = threading.Thread(
                target=lambda: box.append(proc.stdout.readline()),
                daemon=True,
            )
            reader.start()
            reader.join(timeout_sec)
            self.assertFalse(reader.is_alive(), f"Timed out waiting for stdio reply to {payload.get('method')}")
            self.assertTrue(box and box[0], "Empty stdio response line")
            return json.loads(box[0].decode("utf-8"))

        try:
            init_res = rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                }
            )
            self.assertIn("result", init_res)

            wb_res = rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_workbench_config", "arguments": {"include_hidden": False}},
                }
            )
            wb_sc = wb_res["result"]["structuredContent"]
            self.assertIn("models", wb_sc)
            self.assertIn("workbench_config", wb_sc)
            self.assertIn("checks", wb_sc["doctor"])

            doc_res = rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "run_doctor", "arguments": {}},
                }
            )
            doc_sc = doc_res["result"]["structuredContent"]
            self.assertIn("checks", doc_sc)
            self.assertTrue(doc_sc["checks"]["python"]["ok"])
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.terminate()
            proc.wait(timeout=5)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass

    def test_list_codex_models_concise_brief_and_start_status_briefs(self) -> None:
        resp = self._call_tool_raw(140, "list_codex_models", {})["result"]
        brief_text = resp["content"][0]["text"]
        sc = resp["structuredContent"]
        self.assertTrue(brief_text.startswith("# Agy Codex Dispatch Brief"))
        self.assertIn("demo/model-a", brief_text)
        self.assertIn("Presets", brief_text)
        self.assertIn("Available Models", brief_text)
        self.assertNotIn("\u00b7", brief_text)
        self.assertLess(len(brief_text), 3500)
        self.assertIn("active_runs", sc)
        self.assertEqual(sc.get("recommended_wait_timeout_sec"), 120)

        start_resp = self._call_tool_raw(
            141,
            "start_codex_task",
            {"prompt": "Brief check task", "cwd": str(self.workdir), "preset": "fast"},
        )["result"]
        start_text = start_resp["content"][0]["text"]
        self.assertTrue(start_text.startswith("Started Codex task:"))
        self.assertFalse(start_text.lstrip().startswith("{"))
        rid = start_resp["structuredContent"]["run_id"]

        status_resp = self._call_tool_raw(
            142, "get_codex_task_status", {"run_id": rid}
        )["result"]
        self.assertTrue(status_resp["content"][0]["text"].startswith("Codex Status"))

        wait_resp = self._call_tool_raw(
            143, "wait_codex_task", {"run_id": rid, "timeout": 10}
        )["result"]
        self.assertTrue(wait_resp["content"][0]["text"].startswith("Codex Wait Result"))

        close_resp = self._call_tool_raw(144, "close_codex_task", {"run_id": rid})["result"]
        self.assertTrue(close_resp["content"][0]["text"].startswith("Closed Codex task(s):"))

    def test_wait_codex_task_clamps_timeout_to_120(self) -> None:
        tools_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 150, "method": "tools/list"})
        tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
        self.assertEqual(tools["start_codex_task"]["inputSchema"]["properties"]["timeout"]["default"], 0)
        wait_timeout_schema = tools["wait_codex_task"]["inputSchema"]["properties"]["timeout"]
        self.assertEqual(wait_timeout_schema["default"], 120)
        self.assertEqual(wait_timeout_schema["maximum"], 120)

        recorded_timeouts: list[int] = []
        orig_wait = self.manager.wait_runs

        def fake_wait_runs(run_ids, *, wait_mode="all", timeout=120):
            recorded_timeouts.append(timeout)
            return {
                "run_id": run_ids[0],
                "status": "running",
                "title": "probe",
                "wait_timed_out": True,
                "still_running_ids": list(run_ids),
            }

        self.manager.wait_runs = fake_wait_runs  # type: ignore[method-assign]
        try:
            clamped_res = self._call_tool_raw(
                151, "wait_codex_task", {"run_id": "fake-run", "timeout": 300}
            )["result"]["structuredContent"]
            default_res = self._call_tool_raw(
                152, "wait_codex_task", {"run_id": "fake-run"}
            )["result"]["structuredContent"]
        finally:
            self.manager.wait_runs = orig_wait  # type: ignore[method-assign]

        self.assertEqual(recorded_timeouts, [120, 120])
        self.assertEqual(clamped_res.get("wait_timeout_clamped_sec"), 120)
        self.assertIsNone(default_res.get("wait_timeout_clamped_sec"))

    def test_invalid_run_ids_rejected_by_every_entry_point(self) -> None:
        for rid in ("../evil", str(self.root.resolve()), "bad/id", "bad\\id", "a..b"):
            for operation in (
                lambda: self.manager.get_run_events(rid),
                lambda: self.manager.get_status([rid]),
                lambda: self.manager.wait_runs([rid]),
                lambda: self.manager.cancel_run(rid),
                lambda: self.manager.close_runs([rid], closed_by="test"),
                lambda: self.manager.reopen_run(rid),
                lambda: self.manager.send_followup(rid, "test"),
            ):
                with self.subTest(rid=rid), self.assertRaisesRegex(ValueError, "Invalid run_id"):
                    operation()

    def test_worker_timeout_defaults_to_unlimited_and_explicit_zero_overrides(self) -> None:
        params = {"prompt": "run without an explicit limit", "cwd": str(self.workdir)}
        with mock.patch.object(mcp_server.threading, "Timer") as timer:
            state = self.manager.start_run(params)
            self.assertEqual(state.timeout_sec, 0)
            self.assertEqual(self.manager.wait_runs([state.run_id], timeout=5)["status"], "done")
            mcp_server.codex_catalog.save_workbench_config({"mapping": {"worker_timeout_sec": 1}}, self.data_dir)
            unlimited = self.manager.start_run({**params, "timeout": 0})
            self.assertEqual(unlimited.timeout_sec, 0)
            self.assertEqual(self.manager.wait_runs([unlimited.run_id], timeout=5)["status"], "done")
            timer.assert_not_called()
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.manager.start_run({**params, "timeout": -1})
        state = self.manager.start_run({**params, "prompt": "SLEEP_LONG"})
        self.assertEqual(state.timeout_sec, 1)
        self.assertEqual(self.manager.wait_runs([state.run_id], timeout=5)["exit_code"], 124)

    def _test_state(self, run_id: str = "test-run", **kwargs) -> mcp_server.RunState:
        return mcp_server.RunState(run_id, str(self.workdir), "read-only", "isolated",
                                   None, None, None, None, "test", **kwargs)

    def test_live_health_detects_loop_deduplicates_commands_and_reports_stall(self) -> None:
        state = self._test_state()
        for n in range(4):
            item = {"id": str(n), "type": "command_execution", "command": "pytest",
                    "exit_code": 1, "aggregated_output": "test failed", "status": "failed"}
            self.manager._handle_json_event(state, {"type": "item.started", "item": item})
            self.assertEqual(state.to_summary_dict()["in_flight_command"], "pytest")
            for _ in range(2):
                self.manager._handle_json_event(state, {"type": "item.completed", "item": item})
        summary = state.to_summary_dict()
        self.assertEqual(summary["health"], "loop_warning")
        self.assertIn("连续 4 次", summary["health_reason"])
        self.assertEqual(summary["completed_commands"], 4)
        self.assertIsNone(summary["in_flight_command"])
        self.assertEqual(len(summary["recent_errors"]), 1)
        brief = mcp_server.format_wait_or_status_brief(summary, is_wait=True)
        self.assertIn("health: loop_warning", brief)
        self.assertIn("recent_error:", brief)
        with mock.patch.object(mcp_server.time, "time", return_value=state.last_activity_at + 190):
            summary = state.to_summary_dict()
        self.assertEqual(summary["health"], "stalled")
        self.assertEqual(summary["idle_sec"], 190.0)
        self.assertIn("3m 10s", summary["health_reason"])

    def test_repeated_tool_calls_and_distinct_arguments(self) -> None:
        state = self._test_state()
        for n in range(6):
            args = dict((('b', 2), ('a', 1)) if n % 2 else (('a', 1), ('b', 2)))
            item = {"id": str(n), "type": "mcp_tool_call", "tool": "sample_tool", "arguments": args}
            for event_type in ("item.started", "item.completed"):
                self.manager._handle_json_event(state, {"type": event_type, "item": item})
        self.assertIn("连续 6 次", state.to_summary_dict()["health_reason"])
        item = {**item, "id": "different", "arguments": {"a": 2}}
        self.manager._handle_json_event(state, {"type": "item.started", "item": item})
        self.assertEqual(state.to_summary_dict()["health"], "healthy")

    def test_worker_stderr_root_cause_and_idle_watchdog(self) -> None:
        for code, idle, reason in (
            ("import sys; print('429 rate limit', file=sys.stderr); sys.exit(1)", 0, "429 rate limit"),
            ("import time; time.sleep(10)", 1, "连续无输出卡死超时 (1s)"),
        ):
            with self.subTest(idle=idle):
                state = self._test_state(f"worker-{idle}", idle_timeout_sec=idle)
                folder = self.manager._state_path(state.run_id).parent
                folder.mkdir()
                self.manager._execute_worker(state, [sys.executable, "-c", code], folder / "codex.log",
                                             folder / "output.json", 0)
                out = state.to_summary_dict()
                self.assertEqual(out["health"], "error")
                self.assertEqual(out["error"], reason)
                self.assertIsNone(out["idle_sec"])
                if not idle:
                    self.assertIn(reason, out["recent_errors"])

    def test_wait_snapshot_is_consistent_and_persisted_event_seq_is_monotonic(self) -> None:
        running = {"run_id": "sample", "status": "running"}
        done = {**running, "status": "done"}
        with mock.patch.object(self.manager, "_get_summary", side_effect=[running, done]) as getter:
            with mock.patch.object(mcp_server.time, "monotonic", side_effect=[0.0, 1.0]):
                result = self.manager.wait_runs(["sample"], timeout=1)
            self.assertEqual(getter.call_count, 1)
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["wait_timed_out"])
        state_path = self.manager._state_path("sample")
        state_path.parent.mkdir()
        state_path.write_text(json.dumps({**done, "seq": 2}), encoding="utf-8")
        self.manager._events_path("sample").write_text('{"seq":9}\n{"seq":7}\n', encoding="utf-8")
        self.assertEqual(self.manager.get_run_events("sample")["latest_seq"], 9)

    def test_worker_timeout_reports_completed_commands_without_preamble_result(self) -> None:
        timeout_py = self.root / "fake_timeout_cmds.py"
        timeout_py.write_text(
            "\n".join(
                [
                    "import json, sys, time",
                    'print(json.dumps({"type": "thread.started", "thread_id": "0199-timeout-1"}), flush=True)',
                    'print(json.dumps({"type": "item.completed", "item": {"id": "m0", "type": "agent_message", "text": "Opening preamble before scanning..."}}), flush=True)',
                    'print(json.dumps({"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "echo scan1", "status": "completed", "exit_code": 0, "aggregated_output": "ok1"}}), flush=True)',
                    'print(json.dumps({"type": "item.completed", "item": {"id": "c2", "type": "command_execution", "command": "echo scan2", "status": "completed", "exit_code": 0, "aggregated_output": "ok2"}}), flush=True)',
                    "time.sleep(5)",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        if os.name == "nt":
            timeout_bin = self.root / "fake_timeout.cmd"
            timeout_bin.write_text(f'@echo off\r\n"{sys.executable}" "{timeout_py}" %*\r\n', encoding="utf-8")
        else:
            timeout_bin = self.root / "fake_timeout.sh"
            timeout_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{timeout_py}" "$@"\n', encoding="utf-8")
            timeout_bin.chmod(0o755)
        os.environ["CODEX_BIN"] = str(timeout_bin)

        started = self._call_tool_raw(
            160,
            "start_codex_task",
            {"prompt": "scan workspace", "cwd": str(self.workdir), "mode": "read-only", "timeout": 1},
        )["result"]["structuredContent"]
        waited = self._call_tool_raw(
            161,
            "wait_codex_task",
            {"run_id": started["run_id"], "timeout": 10},
        )["result"]["structuredContent"]

        self.assertEqual(waited["status"], "error")
        self.assertFalse(waited["ok"])
        self.assertIsNone(waited["result"])
        self.assertEqual(waited.get("completed_commands"), 2)
        self.assertIn("2 条命令", waited.get("error") or "")
        self.assertIn("codex.log", waited.get("error") or "")
        self.assertIn("2 条命令", waited.get("work_digest") or "")

    def test_conversation_isolation_in_panel_list_close_and_user_task(self) -> None:
        conv_a = "conv-isolation-a"
        conv_b = "conv-isolation-b"
        run_a = self._call_tool_raw(
            170,
            "start_codex_task",
            {
                "prompt": "Task in conversation A",
                "cwd": str(self.workdir),
                "mode": "read-only",
                "conversation_id": conv_a,
                "conversation_title": "Conversation A",
            },
        )["result"]["structuredContent"]
        run_b = self._call_tool_raw(
            171,
            "start_codex_task",
            {
                "prompt": "Task in conversation B",
                "cwd": str(self.workdir),
                "mode": "read-only",
                "conversation_id": conv_b,
                "conversation_title": "Conversation B",
            },
        )["result"]["structuredContent"]
        self._call_tool_raw(
            172, "wait_codex_task", {"run_ids": [run_a["run_id"], run_b["run_id"]], "timeout": 10}
        )

        # Opening panel with run_id from conv A automatically isolates Active Monitor to conv A
        panel_a = self._call_tool_raw(
            173, "open_codex_panel", {"tab": "subagents", "run_id": run_a["run_id"]}
        )["result"]["structuredContent"]
        self.assertEqual(panel_a["scope_session_id"], conv_a)
        self.assertEqual(panel_a["scope_mode"], "session")
        self.assertEqual([r["run_id"] for r in panel_a["runs"]], [run_a["run_id"]])
        self.assertGreaterEqual(panel_a["other_sessions_runs"], 1)
        self.assertGreaterEqual(panel_a["total_unclosed_runs"], 2)

        # Opening panel with no run_id/conversation_id and no env var (global left sidebar) shows both
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CONVERSATION_ID": ""}):
            panel_all = self._call_tool_raw(
                174, "open_codex_panel", {"tab": "subagents"}
            )["result"]["structuredContent"]
        self.assertIsNone(panel_all["scope_session_id"])
        self.assertEqual(panel_all["scope_mode"], "all")
        all_ids = {r["run_id"] for r in panel_all["runs"]}
        self.assertTrue({run_a["run_id"], run_b["run_id"]}.issubset(all_ids))

        # Opening panel when ANTIGRAVITY_CONVERSATION_ID env var is set auto-scopes to that conversation
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CONVERSATION_ID": conv_b}):
            panel_env = self._call_tool_raw(
                180, "open_codex_panel", {"tab": "subagents"}
            )["result"]["structuredContent"]
        self.assertEqual(panel_env["scope_session_id"], conv_b)
        self.assertEqual(panel_env["scope_mode"], "session")
        self.assertEqual([r["run_id"] for r in panel_env["runs"]], [run_b["run_id"]])

        # Direct user chat launched from scoped conv A stays in conv A and never leaks to conv B
        user_a = self._call_tool_raw(
            175,
            "start_user_task",
            {
                "prompt": "Direct follow-up in conv A",
                "cwd": str(self.workdir),
                "mode": "read-only",
                "conversation_id": conv_a,
                "conversation_title": "Conversation A",
            },
        )["result"]["structuredContent"]
        self._call_tool_raw(176, "wait_codex_task", {"run_id": user_a["run_id"], "timeout": 10})
        self.assertEqual(user_a["parent_session_id"], conv_a)
        self.assertEqual(user_a["origin"], "user")
        list_b = self._call_tool_raw(
            177, "list_runs", {"parent_session_id": conv_b}
        )["result"]["structuredContent"]
        self.assertEqual([r["run_id"] for r in list_b["runs"]], [run_b["run_id"]])
        self.assertGreaterEqual(list_b["other_sessions_runs"], 2)

        # Batch archive scoped to conv A must NOT archive conv B's finished run
        closed_a = self._call_tool_raw(
            178, "close_run", {"close_all_finished": True, "parent_session_id": conv_a}
        )["result"]["structuredContent"]
        self.assertIn(run_a["run_id"], closed_a["closed_ids"])
        self.assertIn(user_a["run_id"], closed_a["closed_ids"])
        self.assertNotIn(run_b["run_id"], closed_a["closed_ids"])
        self.assertEqual(closed_a["runs"], [])
        self.assertGreaterEqual(closed_a["other_sessions_runs"], 1)

        # Verify conv B's run is still open and unarchived
        list_b_after = self._call_tool_raw(
            179, "list_runs", {"parent_session_id": conv_b}
        )["result"]["structuredContent"]
        self.assertEqual([r["run_id"] for r in list_b_after["runs"]], [run_b["run_id"]])

        # Verify infer_default_cwd prefers worktree.base_repo over plugin_data/worktrees/*
        fake_wt_dir = self.data_dir / "worktrees" / "ephemeral-wt"
        fake_wt_dir.mkdir(parents=True, exist_ok=True)
        wt_run_dir = self.data_dir / "runs" / "20990101-000000-wt0001"
        wt_run_dir.mkdir(parents=True, exist_ok=True)
        (wt_run_dir / "state.json").write_text(
            json.dumps({
                "run_id": "20990101-000000-wt0001",
                "status": "done",
                "started_at": time.time() + 1000,
                "cwd": str(fake_wt_dir),
                "parent_session_id": "conv-wt",
                "worktree": {"path": str(fake_wt_dir), "base_repo": str(self.workdir)},
            }),
            encoding="utf-8",
        )
        inferred = self.manager.infer_default_cwd(parent_session_id="conv-wt")
        self.assertEqual(Path(inferred).resolve(), self.workdir.resolve())


if __name__ == "__main__":
    unittest.main()
