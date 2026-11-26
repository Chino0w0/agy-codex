#!/usr/bin/env python3
"""Unit tests for `codex_catalog.py`."""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "plugin" / "agy-codex" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_catalog  # noqa: E402


class TestCatalogAndWhitelist(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.codex_home = self.root / ".codex"
        self.data_dir = self.root / "plugin_data"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_official_native_without_catalog(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'model = "gpt-5.2-codex"\nmodel_reasoning_effort = "high"\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.codex_home / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.2-codex",
                            "display_name": "gpt-5.2-codex",
                            "visibility": "list",
                            "default_reasoning_level": "medium",
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "medium"},
                                {"effort": "high"},
                                {"effort": "xhigh"},
                            ],
                            "context_window": 272000,
                        }
                    ]
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        disc = codex_catalog.discover_models_and_config(self.codex_home, self.data_dir)
        self.assertTrue(disc["models_cache_loaded"])
        self.assertFalse(disc["catalog_loaded"])
        self.assertEqual(disc["providers"][0]["id"], "openai")
        self.assertEqual(disc["effective_defaults"]["model"], "gpt-5.2-codex")
        self.assertEqual(disc["effective_defaults"]["effort"], "high")

        # Arbitrary custom model without catalog passes through verbatim
        overrides, meta = codex_catalog.collect_isolated_overrides(
            self.codex_home,
            self.data_dir,
            cli_model="my-unlisted-model",
            cli_effort="xhigh",
        )
        ov_dict = dict(overrides)
        self.assertEqual(ov_dict["model"], "my-unlisted-model")
        self.assertEqual(ov_dict["model_reasoning_effort"], "xhigh")
        self.assertNotIn("model_catalog_json", ov_dict)
        self.assertEqual(meta["warnings"], [])

    def test_custom_relay_provider_and_secret_redaction(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "relay/gpt-custom"',
                    'model_provider = "relay"',
                    'model_reasoning_effort = "xhigh"',
                    "",
                    "[model_providers.relay]",
                    'name = "Relay Gateway"',
                    'base_url = "https://relay.example.com/v1"',
                    'wire_api = "responses"',
                    'experimental_bearer_token = "sk-super-secret-token-123"',
                    "",
                    "[model_providers.relay.http_headers]",
                    'X-Custom-Auth = "header-secret-456"',
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        codex_catalog.save_workbench_config(
            {
                "custom_models": [
                    {
                        "slug": "relay/fast-coder",
                        "display_name": "Fast Coder",
                        "provider": "relay",
                        "default_reasoning_level": "medium",
                        "supported_reasoning_levels": ["low", "medium", "high", "xhigh"],
                        "context_window": 256000,
                    }
                ]
            },
            self.data_dir,
        )

        disc = codex_catalog.discover_models_and_config(self.codex_home, self.data_dir)
        serialized_disc = json.dumps(disc)
        self.assertNotIn("sk-super-secret-token-123", serialized_disc)
        self.assertNotIn("header-secret-456", serialized_disc)
        self.assertTrue(any(p["id"] == "relay" and p["has_auth_token"] for p in disc["providers"]))
        self.assertTrue(any(m["slug"] == "relay/fast-coder" and m["source"] == "custom" for m in disc["models"]))

        overrides, meta = codex_catalog.collect_isolated_overrides(
            self.codex_home, self.data_dir, cli_model="relay/fast-coder", cli_effort="xhigh"
        )
        ov_dict = dict(overrides)
        # Real command gets real token
        self.assertEqual(ov_dict["model_providers.relay.experimental_bearer_token"], "sk-super-secret-token-123")
        self.assertEqual(ov_dict["model_providers.relay.http_headers.X-Custom-Auth"], "header-secret-456")
        # Metadata redacts secrets completely
        serialized_meta = json.dumps(meta)
        self.assertNotIn("sk-super-secret-token-123", serialized_meta)
        self.assertNotIn("header-secret-456", serialized_meta)
        self.assertEqual(
            meta["redacted_overrides"]["model_providers.relay.experimental_bearer_token"], "<redacted>"
        )

    def test_catalog_json_relative_path_visibility_effort_clamp_and_context_window(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "axon/gpt-6.2-sol"',
                    'model_provider = "custom"',
                    'model_reasoning_effort = "xhigh"',
                    'model_catalog_json = "models-catelog.local.json"',
                    "model_context_window = 922000",
                    "model_auto_compact_token_limit = 750000",
                    'custom_extra_flag = "enabled-val"',
                    "",
                    "[model_providers.custom]",
                    'name = "Local LiteLLM"',
                    'base_url = "http://127.0.0.1:4002/v1"',
                    'wire_api = "responses"',
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        catalog_path = self.codex_home / "models-catelog.local.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "axon/gpt-6.2-sol",
                            "display_name": "[Axon]GPT-6.2-sol",
                            "visibility": "list",
                            "default_reasoning_level": "medium",
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "medium"},
                                {"effort": "high"},
                                {"effort": "xhigh"},
                                {"effort": "max"},
                            ],
                            "context_window": 922000,
                        },
                        {
                            "slug": "axon/gemini-3.8-flash",
                            "display_name": "[Axon]Gemini-3.8-flash",
                            "visibility": "list",
                            "default_reasoning_level": "medium",
                            "supported_reasoning_levels": [
                                {"effort": "none"},
                                {"effort": "low"},
                                {"effort": "medium"},
                                {"effort": "high"},
                            ],
                            "context_window": 1048576,
                        },
                        {
                            "slug": "axon/hidden-legacy",
                            "display_name": "Hidden Legacy",
                            "visibility": "hide",
                            "supported_reasoning_levels": ["low", "medium"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
            newline="\n",
        )

        # Visibility filtering in discovery
        disc_default = codex_catalog.discover_models_and_config(self.codex_home, self.data_dir)
        self.assertEqual(disc_default["visible_models_count"], 2)
        self.assertEqual(disc_default["hidden_models_count"], 1)
        self.assertEqual(len(disc_default["models"]), 2)

        disc_all = codex_catalog.discover_models_and_config(
            self.codex_home, self.data_dir, include_hidden=True
        )
        self.assertEqual(len(disc_all["models"]), 3)

        # Same model forwards context window and resolves relative catalog path cleanly as valid TOML
        ov_same, meta_same = codex_catalog.collect_isolated_overrides(
            self.codex_home, self.data_dir
        )
        ov_same_dict = dict(ov_same)
        self.assertEqual(ov_same_dict["model_catalog_json"], str(catalog_path.resolve()))
        self.assertEqual(ov_same_dict["model_context_window"], 922000)
        self.assertEqual(ov_same_dict["model_auto_compact_token_limit"], 750000)
        self.assertEqual(ov_same_dict["agents.enabled"], False)
        self.assertEqual(ov_same_dict["features.multi_agent"], False)
        self.assertIn("SUBAGENT ROLE DECLARATION", ov_same_dict["developer_instructions"])
        # Ensure TOML literal round-trip parses cleanly (important for Windows backslashes)
        toml_expr = f"model_catalog_json = {codex_catalog.to_toml_literal(ov_same_dict['model_catalog_json'])}"
        self.assertEqual(tomllib.loads(toml_expr)["model_catalog_json"], str(catalog_path.resolve()))

        # Overriding model omits config.toml context_window (same_model_only) and clamps debug xhigh -> high
        ov_flash, meta_flash = codex_catalog.collect_isolated_overrides(
            self.codex_home,
            self.data_dir,
            cli_model="axon/gemini-3.8-flash",
            preset="debug",
        )
        ov_flash_dict = dict(ov_flash)
        self.assertEqual(ov_flash_dict["model_reasoning_effort"], "high")
        self.assertEqual(meta_flash["original_effort"], "xhigh")
        self.assertTrue(any("effort clamped: xhigh -> high" in w for w in meta_flash["warnings"]))
        self.assertNotIn("model_context_window", ov_flash_dict)

        # Passthrough effort_policy + always_forward context_window + extra_whitelist_keys + allow_nested_subagents + catalog_mode off
        codex_catalog.save_workbench_config(
            {
                "mapping": {
                    "effort_policy": "passthrough",
                    "context_window_policy": "always_forward",
                    "extra_whitelist_keys": ["custom_extra_flag"],
                    "allow_nested_subagents": True,
                    "catalog_mode": "off",
                },
                "excluded_models": ["axon/hidden-legacy"],
            },
            self.data_dir,
        )
        ov_custom, meta_custom = codex_catalog.collect_isolated_overrides(
            self.codex_home,
            self.data_dir,
            cli_model="axon/hidden-legacy",
            cli_effort="xhigh",
        )
        ov_custom_dict = dict(ov_custom)
        self.assertEqual(ov_custom_dict["model_reasoning_effort"], "xhigh")
        self.assertEqual(ov_custom_dict["model_context_window"], 922000)
        self.assertEqual(ov_custom_dict["custom_extra_flag"], "enabled-val")
        self.assertNotIn("model_catalog_json", ov_custom_dict)
        self.assertNotIn("agents.enabled", ov_custom_dict)
        self.assertNotIn("features.multi_agent", ov_custom_dict)
        self.assertIn("SUBAGENT ROLE DECLARATION", ov_custom_dict["developer_instructions"])
        self.assertTrue(any("excluded_models" in w for w in meta_custom["warnings"]))

    def test_precedence_layers_and_custom_catalog_mode(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'model = "base-m"\nmodel_reasoning_effort = "low"\n',
            encoding="utf-8",
            newline="\n",
        )
        (self.codex_home / "worker.config.toml").write_text(
            'model = "worker-m"\n',
            encoding="utf-8",
            newline="\n",
        )
        custom_cat = self.root / "extra-cat.json"
        custom_cat.write_text(
            json.dumps([{"id": "extra/m1", "provider": "p2", "supported_reasoning_levels": ["medium"]}]),
            encoding="utf-8",
            newline="\n",
        )
        codex_catalog.save_workbench_config(
            {
                "defaults": {"model": "wb-m", "effort": "medium"},
                "mapping": {"catalog_mode": "custom", "catalog_path_override": str(custom_cat)},
            },
            self.data_dir,
        )
        res = codex_catalog.resolve_effective_run_config(
            self.codex_home, self.data_dir, cli_model="extra/m1"
        )
        self.assertEqual(res["effective"]["model"], "extra/m1")
        # Auto-switches provider when model declares provider and caller did not specify one
        self.assertEqual(res["effective"]["provider"], "p2")
        self.assertEqual(res["catalog_path"], str(custom_cat.resolve()))

    def test_windows_sandbox_and_allow_login_shell_forwarded(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "gpt-5.2-codex"',
                    "allow_login_shell = true",
                    "",
                    "[windows]",
                    'sandbox = "unelevated"',
                    "",
                    "[sandbox_workspace_write]",
                    "network_access = false",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        overrides, meta = codex_catalog.collect_isolated_overrides(self.codex_home, self.data_dir)
        ov_dict = dict(overrides)
        self.assertEqual(ov_dict["allow_login_shell"], True)
        self.assertEqual(ov_dict["windows.sandbox"], "unelevated")
        self.assertEqual(ov_dict["sandbox_workspace_write.network_access"], False)
        self.assertEqual(meta["redacted_overrides"]["windows.sandbox"], "unelevated")

    def test_forwarded_mcp_servers_and_secret_redaction(self) -> None:
        (self.codex_home / "config.toml").write_text(
            '\n'.join(
                [
                    'model = "gpt-5.2-codex"',
                    "",
                    "[mcp_servers.fastctx]",
                    'command = "C:/bin/fastctx.exe"',
                    'args = ["serve", "--enable-shell"]',
                    "",
                    "[mcp_servers.fastctx.env]",
                    'FASTCTX_BASH = "D:/Git/bin/bash.exe"',
                    'API_KEY = "super-secret-fastctx-key"',
                    "",
                    "[mcp_servers.node_repl]",
                    'command = "C:/bin/node_repl.exe"',
                    "args = []",
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        # Default: no MCP servers forwarded in isolated mode, but discovery lists them without env secrets
        disc = codex_catalog.discover_models_and_config(self.codex_home, self.data_dir)
        mcp_ids = {m["id"] for m in disc["mcp_servers"]}
        self.assertEqual(mcp_ids, {"fastctx", "node_repl"})
        self.assertNotIn("super-secret-fastctx-key", json.dumps(disc))

        ov_def, _ = codex_catalog.collect_isolated_overrides(self.codex_home, self.data_dir)
        self.assertFalse(any(k.startswith("mcp_servers.") for k, _ in ov_def))

        # Opt-in forward fastctx only
        codex_catalog.save_workbench_config(
            {"mapping": {"forwarded_mcp_servers": ["fastctx"]}}, self.data_dir
        )
        ov_fwd, meta_fwd = codex_catalog.collect_isolated_overrides(self.codex_home, self.data_dir)
        ov_dict = dict(ov_fwd)
        self.assertEqual(ov_dict["mcp_servers.fastctx.command"], "C:/bin/fastctx.exe")
        self.assertEqual(ov_dict["mcp_servers.fastctx.args"], ["serve", "--enable-shell"])
        self.assertEqual(
            codex_catalog.to_toml_literal(ov_dict["mcp_servers.fastctx.args"]),
            '["serve", "--enable-shell"]',
        )
        self.assertEqual(ov_dict["mcp_servers.fastctx.env.FASTCTX_BASH"], "D:/Git/bin/bash.exe")
        self.assertEqual(ov_dict["mcp_servers.fastctx.env.API_KEY"], "super-secret-fastctx-key")
        self.assertFalse(any(k.startswith("mcp_servers.node_repl") for k in ov_dict))
        self.assertNotIn("super-secret-fastctx-key", json.dumps(meta_fwd))
        self.assertEqual(
            meta_fwd["redacted_overrides"]["mcp_servers.fastctx.env.API_KEY"], "<redacted>"
        )


if __name__ == "__main__":
    unittest.main()
