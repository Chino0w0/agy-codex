#!/usr/bin/env python3
"""End-to-end unit tests for `codex_run.py` and `mcp_server.py`."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_protocol_and_visibility_separation(self) -> None:
        init = self.server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "agy-codex")

        tools_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
        for model_tool in (
            "list_codex_models",
            "start_codex_task",
            "wait_codex_task",
            "get_codex_task_status",
            "stop_codex_task",
            "open_codex_panel",
        ):
            self.assertIn(model_tool, tools)
            self.assertNotEqual(
                ((tools[model_tool].get("_meta") or {}).get("ui") or {}).get("visibility"),
                ["app"],
            )

        for app_tool in (
            "get_run_events",
            "list_runs",
            "list_local_sessions",
            "get_local_session_detail",
            "get_stats",
            "get_workbench_config",
            "save_workbench_config",
            "preview_resolved_command",
            "run_doctor",
            "cancel_run",
        ):
            self.assertIn(app_tool, tools)
            self.assertEqual(tools[app_tool]["_meta"]["ui"]["visibility"], ["app"])

        panel_meta = tools["open_codex_panel"]["_meta"]["com.google.antigravity/ui"]["entrypoints"]
        self.assertEqual({e["type"] for e in panel_meta}, {"aux_pane", "global"})

        res_read = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": mcp_server.APP_URI}}
        )
        self.assertEqual(res_read["result"]["contents"][0]["mimeType"], "text/html;profile=mcp-app")

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


if __name__ == "__main__":
    unittest.main()
