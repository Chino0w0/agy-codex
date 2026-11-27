#!/usr/bin/env python3
"""Unit tests for `codex_telemetry.py`."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "plugin" / "agy-codex" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_telemetry  # noqa: E402


class TestTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.codex_home = self.root / ".codex"
        self.data_dir = self.root / "plugin_data"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_sqlite_graceful_degradation(self) -> None:
        now = time.time()
        plugin_runs = [
            {
                "run_id": "r1",
                "status": "done",
                "started_at": now - 30,
                "duration_sec": 12.5,
                "model": "m1",
                "provider": "p1",
                "effort": "high",
                "tokens_used": 1200,
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 400,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 50,
                },
            }
        ]
        stats = codex_telemetry.compute_stats(
            plugin_runs, self.codex_home, self.data_dir, scope="all", time_range="7d"
        )
        self.assertEqual(stats["overview"]["total_items"], 1)
        self.assertEqual(stats["overview"]["total_tokens"], 1200)
        self.assertEqual(stats["overview"]["cache_hit_rate"], 40.0)
        self.assertEqual(stats["overview"]["success_rate"], 100.0)
        self.assertTrue(len(stats["warnings"]) >= 1)

    def test_sqlite_jsonl_enrichment_dedup_and_detail(self) -> None:
        now = time.time()
        now_ms = int(now * 1000)

        rollout_user = self.codex_home / "sessions" / "rollout-user.jsonl"
        rollout_user.parent.mkdir(parents=True, exist_ok=True)
        rollout_lines = [
            {"type": "session_meta", "payload": {"id": "thr-user-1"}},
            {"type": "turn_context", "payload": {"model": "gpt-6.2-sol", "effort": "high", "cwd": "E:\\Proj"}},
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Inspect telemetry pipeline."},
            },
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "Checked pipeline and found no issues."},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 10000,
                            "cached_input_tokens": 5000,
                            "output_tokens": 500,
                            "reasoning_output_tokens": 100,
                            "total_tokens": 10500,
                        },
                        "last_token_usage": {
                            "input_tokens": 4000,
                            "cached_input_tokens": 2000,
                            "output_tokens": 200,
                            "reasoning_output_tokens": 50,
                            "total_tokens": 4200,
                        },
                        "model_context_window": 100000,
                    },
                },
            },
        ]
        rollout_user.write_text(
            "\n".join(json.dumps(x) for x in rollout_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        db_path = self.codex_home / "state_5.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                created_at_ms INTEGER,
                updated_at_ms INTEGER,
                source TEXT,
                model_provider TEXT,
                cwd TEXT,
                title TEXT,
                tokens_used INTEGER,
                model TEXT,
                reasoning_effort TEXT,
                thread_source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT,
                child_thread_id TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thr-user-1",
                str(rollout_user),
                int(now - 60),
                int(now - 10),
                now_ms - 60000,
                now_ms - 10000,
                "cli",
                "custom",
                "\\\\?\\E:\\AgentProject\\Demo",
                "User session",
                999,  # overridden by rollout total_token_usage
                "gpt-6.2-sol",
                "high",
                "user",
            ),
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thr-sub-1",
                "",
                int(now - 50),
                int(now - 20),
                now_ms - 50000,
                now_ms - 20000,
                '{"subagent":{"thread_spawn":{"parent_thread_id":"thr-user-1"}}}',
                "custom",
                "\\\\?\\E:\\AgentProject\\Demo",
                "Subagent task",
                2000,
                "gpt-6.2-sol",
                "low",
                "subagent",
            ),
        )
        # Thread belonging to a plugin run (must be deduplicated out of codex_local!)
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thr-plugin-dup",
                "",
                int(now - 40),
                int(now - 5),
                now_ms - 40000,
                now_ms - 5000,
                "exec",
                "custom",
                "E:\\AgentProject\\Demo",
                "Plugin dup",
                3000,
                "gpt-6.2-sol",
                "medium",
                "user",
            ),
        )
        conn.execute(
            "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
            ("thr-user-1", "thr-sub-1", "closed"),
        )
        conn.commit()
        conn.close()

        # Verify session listing & Windows \\?\ normalization
        local = codex_telemetry.read_local_codex_sessions(self.codex_home, self.data_dir)
        self.assertEqual(len(local["sessions"]), 3)
        u_sess = next(s for s in local["sessions"] if s["thread_id"] == "thr-user-1")
        self.assertEqual(u_sess["cwd"], "E:\\AgentProject\\Demo")
        self.assertEqual(u_sess["tokens_used"], 10500)
        # Cache file created
        self.assertTrue((self.data_dir / "telemetry-cache.json").is_file())

        # Detail view: context_percent from last_token_usage (4000 / 100000 = 4.0%) + spawn tree + snippets
        detail = codex_telemetry.get_local_session_detail(self.codex_home, self.data_dir, "thr-user-1")
        self.assertEqual(detail["context_percent"], 4.0)
        self.assertEqual(len(detail["child_details"]), 1)
        self.assertEqual(detail["child_details"][0]["thread_id"], "thr-sub-1")
        self.assertEqual(len(detail["snippets"]), 2)

        # Stats with dedup against plugin run sharing session_id="thr-plugin-dup"
        plugin_runs = [
            {
                "run_id": "run-1",
                "session_id": "thr-plugin-dup",
                "status": "done",
                "started_at": now - 40,
                "duration_sec": 10.0,
                "model": "gpt-6.2-sol",
                "provider": "custom",
                "effort": "medium",
                "tokens_used": 3000,
                "usage": {
                    "input_tokens": 2500,
                    "cached_input_tokens": 1000,
                    "output_tokens": 500,
                    "reasoning_output_tokens": 0,
                },
            },
            {
                "run_id": "run-2",
                "status": "error",
                "started_at": now - 20,
                "duration_sec": 4.0,
                "model": "other-m",
                "provider": "custom",
                "effort": "low",
                "tokens_used": 500,
                "error": "boom",
            },
        ]
        stats_all = codex_telemetry.compute_stats(
            plugin_runs, self.codex_home, self.data_dir, scope="all", time_range="7d"
        )
        # 2 plugin runs + 2 non-dup local threads (thr-user-1, thr-sub-1) = 4
        self.assertEqual(stats_all["overview"]["total_items"], 4)
        self.assertEqual(stats_all["overview"]["plugin_run_count"], 2)
        self.assertEqual(stats_all["overview"]["local_user_session_count"], 1)
        self.assertEqual(stats_all["overview"]["local_subagent_count"], 1)
        # Total tokens = 3000 + 500 + 10500 + 2000 = 16000
        self.assertEqual(stats_all["overview"]["total_tokens"], 16000)
        # Success rate & avg duration strictly over plugin runs: 1/2 = 50.0%, (10+4)/2 = 7.0
        self.assertEqual(stats_all["overview"]["success_rate"], 50.0)
        self.assertEqual(stats_all["overview"]["avg_duration_sec"], 7.0)
        # Cache hit rate over items with detailed input_tokens: (1000+5000)/(2500+10000) = 6000/12500 = 48.0%
        self.assertEqual(stats_all["overview"]["cache_hit_rate"], 48.0)

        # Scope codex_local strictly excludes thr-plugin-dup
        stats_local = codex_telemetry.compute_stats(
            plugin_runs, self.codex_home, self.data_dir, scope="codex_local", time_range="7d"
        )
        self.assertEqual(stats_local["overview"]["total_items"], 2)
        self.assertIsNone(stats_local["overview"]["success_rate"])
        self.assertIsNone(stats_local["overview"]["avg_duration_sec"])

    def test_default_antigravity_scope_ignores_sqlite_and_tracks_sessions_roles_archive(self) -> None:
        now = time.time()
        # Seed a local SQLite DB with a large token count; default scope="antigravity" must ignore it
        db_path = self.codex_home / "state_5.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                created_at_ms INTEGER,
                updated_at_ms INTEGER,
                source TEXT,
                model_provider TEXT,
                cwd TEXT,
                title TEXT,
                tokens_used INTEGER,
                model TEXT,
                reasoning_effort TEXT,
                thread_source TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thr-local-ignored",
                "",
                int(now - 30),
                int(now - 10),
                int((now - 30) * 1000),
                int((now - 10) * 1000),
                "cli",
                "openai",
                "E:\\Demo",
                "Local CLI session",
                99999,
                "gpt-5.2-codex",
                "high",
                "user",
            ),
        )
        conn.commit()
        conn.close()

        runs_dir = self.data_dir / "runs"
        plugin_runs = [
            {
                "run_id": "r-a1",
                "status": "done",
                "started_at": now - 120,
                "finished_at": now - 100,
                "closed_at": now - 90,
                "duration_sec": 20.0,
                "model": "m1",
                "provider": "p1",
                "effort": "high",
                "role": "coding",
                "parent_session_id": "conv-a",
                "parent_session_title": "Session A",
                "tokens_used": 1000,
                "usage": {
                    "input_tokens": 800,
                    "cached_input_tokens": 400,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 30,
                },
            },
            {
                "run_id": "r-a2",
                "status": "done",
                "started_at": now - 80,
                "finished_at": now - 60,
                "closed_at": now - 50,
                "duration_sec": 20.0,
                "model": "m1",
                "provider": "p1",
                "effort": "medium",
                "role": "review",
                "parent_session_id": "conv-a",
                "parent_session_title": "Session A",
                "tokens_used": 600,
                "usage": {
                    "input_tokens": 500,
                    "cached_input_tokens": 250,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 10,
                },
            },
            {
                "run_id": "r-b1",
                "status": "done",
                "started_at": now - 40,
                "finished_at": now - 20,
                "duration_sec": 20.0,
                "model": "m2",
                "provider": "p1",
                "effort": "low",
                "role": "coding",
                "parent_session_id": "conv-b",
                "parent_session_title": "Session B",
                "tokens_used": 500,
            },
        ]
        for run in plugin_runs:
            rdir = runs_dir / run["run_id"]
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "state.json").write_text(json.dumps(run), encoding="utf-8", newline="\n")
            (rdir / "events.jsonl").write_text('{"seq":1,"kind":"status"}\n', encoding="utf-8", newline="\n")

        # Default scope must be "antigravity"
        stats = codex_telemetry.compute_stats(
            plugin_runs, self.codex_home, self.data_dir, time_range="7d"
        )
        self.assertEqual(stats["scope"], "antigravity")
        ov = stats["overview"]
        self.assertEqual(ov["plugin_run_count"], 3)
        self.assertEqual(ov["local_user_session_count"], 0)
        self.assertEqual(ov["local_subagent_count"], 0)
        self.assertEqual(ov["total_tokens"], 2100)
        self.assertEqual(ov["session_count"], 2)
        self.assertGreaterEqual(ov["archive_24h_count"], 2)
        self.assertGreater(ov["archive_storage_bytes"], 0)

        by_role = {row["name"]: row for row in stats["by_role"]}
        self.assertIn("coding", by_role)
        self.assertIn("review", by_role)
        self.assertEqual(by_role["coding"]["count"], 2)
        self.assertEqual(by_role["coding"]["tokens"], 1500)
        self.assertEqual(by_role["review"]["count"], 1)
        self.assertEqual(by_role["review"]["tokens"], 600)

        # Ensure zero cost/price fields anywhere in stats keys
        def collect_keys(obj: object) -> set[str]:
            keys: set[str] = set()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    keys.add(str(k).lower())
                    keys.update(collect_keys(v))
            elif isinstance(obj, list):
                for item in obj:
                    keys.update(collect_keys(item))
            return keys

        all_keys = collect_keys(stats)
        for forbidden in ("cost", "price", "usd", "amount"):
            self.assertFalse(any(forbidden in k for k in all_keys), f"Unexpected cost key matching {forbidden}")

    def test_ledger_append_and_read_dedup_by_run_id(self) -> None:
        now = time.time()
        codex_telemetry.append_ledger_record(
            self.data_dir,
            {
                "run_id": "run-dup",
                "status": "done",
                "started_at": now - 100,
                "tokens_used": 1000,
                "role": "coding",
            },
        )
        codex_telemetry.append_ledger_record(
            self.data_dir,
            {
                "run_id": "run-dup",
                "status": "done",
                "started_at": now - 100,
                "tokens_used": 2200,
                "role": "coding",
            },
        )
        codex_telemetry.append_ledger_record(
            self.data_dir,
            {
                "run_id": "run-other",
                "status": "error",
                "started_at": now - 50,
                "tokens_used": 300,
                "role": "debug",
            },
        )

        records = codex_telemetry.read_ledger_records(self.data_dir)
        self.assertEqual(len(records), 2)
        by_id = {r["run_id"]: r for r in records}
        self.assertEqual(by_id["run-dup"]["tokens_used"], 2200)
        self.assertEqual(by_id["run-other"]["status"], "error")

    def test_prune_expired_runs_archives_to_ledger_and_compute_stats_includes_it(self) -> None:
        now = time.time()
        runs_dir = self.data_dir / "runs"

        expired_run = {
            "run_id": "run-expired",
            "status": "done",
            "started_at": now - 30 * 3600,
            "finished_at": now - 29 * 3600,
            "duration_sec": 15.0,
            "model": "m-old",
            "provider": "p1",
            "effort": "medium",
            "role": "coding",
            "parent_session_id": "conv-old",
            "tokens_used": 1500,
        }
        fresh_run = {
            "run_id": "run-fresh",
            "status": "done",
            "started_at": now - 2 * 3600,
            "finished_at": now - 1 * 3600,
            "duration_sec": 8.0,
            "model": "m-new",
            "provider": "p1",
            "effort": "high",
            "role": "review",
            "parent_session_id": "conv-new",
            "tokens_used": 500,
        }
        running_old_run = {
            "run_id": "run-running-old",
            "status": "running",
            "started_at": now - 48 * 3600,
            "model": "m-new",
            "provider": "p1",
            "effort": "high",
            "role": "coding",
            "parent_session_id": "conv-new",
            "tokens_used": 200,
        }

        for r in (expired_run, fresh_run, running_old_run):
            rdir = runs_dir / r["run_id"]
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "state.json").write_text(json.dumps(r), encoding="utf-8", newline="\n")
            (rdir / "events.jsonl").write_text('{"seq":1,"kind":"status"}\n', encoding="utf-8", newline="\n")

        codex_telemetry.prune_expired_runs(self.data_dir, retention_hours=24)

        self.assertFalse((runs_dir / "run-expired").exists())
        self.assertTrue((runs_dir / "run-fresh").is_dir())
        self.assertTrue((runs_dir / "run-running-old").is_dir())
        self.assertTrue((self.data_dir / "stats-ledger.jsonl").is_file())

        ledger = codex_telemetry.read_ledger_records(self.data_dir)
        self.assertTrue(any(r.get("run_id") == "run-expired" for r in ledger))

        stats = codex_telemetry.compute_stats(
            [fresh_run, running_old_run], self.codex_home, self.data_dir, time_range="7d"
        )
        self.assertEqual(stats["overview"]["plugin_run_count"], 3)
        self.assertEqual(stats["overview"]["total_tokens"], 2200)

    def test_partial_status_counted_as_non_done_issue(self) -> None:
        now = time.time()
        plugin_runs = [
            {
                "run_id": "run-partial-1",
                "status": "partial",
                "started_at": now - 30,
                "duration_sec": 9.5,
                "model": "m1",
                "provider": "p1",
                "effort": "high",
                "tokens_used": 900,
                "error": "Only updated backend; tests pending",
            }
        ]
        stats = codex_telemetry.compute_stats(
            plugin_runs, self.codex_home, self.data_dir, scope="antigravity", time_range="7d"
        )
        self.assertEqual(stats["overview"]["done_count"], 0)
        self.assertEqual(stats["overview"]["error_count"], 1)
        self.assertEqual(stats["overview"]["success_rate"], 0.0)
        self.assertTrue(
            any(err.get("run_id") == "run-partial-1" for err in stats["recent_errors"]),
            f"Expected run-partial-1 in recent_errors, got: {stats['recent_errors']!r}",
        )


if __name__ == "__main__":
    unittest.main()
