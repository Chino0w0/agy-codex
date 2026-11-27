"""Offline tests of GitHub update checks, download safety, and installation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.response
import zipfile
from email.message import Message
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "plugin" / "agy-codex" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import codex_updater as updater  # noqa: E402


class TestUpdater(unittest.TestCase):
    repo = "ExampleOrg/example-plugin"
    latest_url = f"https://api.github.com/repos/{repo}/releases/latest"
    commit_url = f"https://api.github.com/repos/{repo}/commits/main"
    commit = "b" * 40
    manifest_url = f"https://raw.githubusercontent.com/{repo}/{commit}/plugin/agy-codex/plugin.json"
    main_zip_url = f"https://codeload.github.com/{repo}/zip/{commit}"

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.plugin = self.root / "plugins" / "agy-codex"
        self.plugin.mkdir(parents=True)
        self.data = self.root / "data"
        self.data.mkdir()
        self.metadata = {"name": "agy-codex", "version": "1.5.0", "repository": f"https://github.com/{self.repo}"}
        self.write_json(self.plugin / "plugin.json", self.metadata)
        env = mock.patch.dict(os.environ, {"AGY_CODEX_UPDATE_REPO": ""})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def fetcher(responses: dict) -> mock.Mock:
        def fetch(url: str) -> tuple[int, bytes, dict[str, str]]:
            status, body = responses[url]
            return status, body if isinstance(body, bytes) else json.dumps(body).encode(), {}
        return mock.Mock(side_effect=fetch)

    def test_semver_and_repository(self) -> None:
        ordered = ["1.4.9", "1.5.0-alpha", "1.5.0-alpha.1", "1.5.0-alpha.beta", "1.5.0-beta.2",
                   "1.5.0-beta.11", "1.5.0-rc.1", "v1.5.0", "1.10.0", "2.0.0"]
        for old, new in zip(ordered, ordered[1:]):
            self.assertEqual(updater.compare_versions(old, new), -1)
            self.assertEqual(updater.compare_versions(new, old), 1)
        self.assertEqual(updater.compare_versions("v1.5.0+build.1", "1.5.0+build.2"), 0)
        self.assertEqual(updater.parse_github_repo(self.metadata), self.repo)
        self.assertEqual(updater.parse_github_repo({"repository": "https://github.com.example/a/b"}), updater.DEFAULT_REPO)
        with mock.patch.dict(os.environ, {"AGY_CODEX_UPDATE_REPO": "OtherOwner/alt"}):
            self.assertEqual(updater.parse_github_repo(self.metadata), "OtherOwner/alt")

    def test_release_cache_force_and_dismiss(self) -> None:
        release = {"tag_name": "v1.6.0", "html_url": f"https://github.com/{self.repo}/releases/tag/v1.6.0",
                   "body": "note\n" * 300, "published_at": "2025-01-01T00:00:00Z"}
        fetch = self.fetcher({self.latest_url: (200, release)})
        with mock.patch.object(updater.time, "time", return_value=100000) as clock:
            result = updater.check_for_updates(self.plugin, self.data, fetcher=fetch)
            self.assertTrue(result["ok"])
            self.assertEqual(result["channel"], "release")
            self.assertTrue(result["update_available"])
            self.assertEqual(result["latest_id"], "1.6.0")
            self.assertEqual(result["remote_version"], "1.6.0")
            self.assertEqual(result["release_url"], release["html_url"])
            self.assertEqual(result["published_at"], release["published_at"])
            self.assertEqual(len(result["notes"]), 1200)
            self.assertFalse(result["from_cache"])
            clock.return_value += updater.DEFAULT_CHECK_TTL_SEC - 1
            cached = updater.check_for_updates(self.plugin, self.data, fetcher=fetch)
            self.assertTrue(cached["from_cache"])
            fetch.assert_called_once_with(self.latest_url)
            clock.return_value += 1
            self.assertFalse(updater.check_for_updates(self.plugin, self.data, fetcher=fetch)["from_cache"])
            self.assertEqual(fetch.call_count, 2)
            dismissed = updater.check_for_updates(self.plugin, self.data, force=True, dismiss_version=cached["latest_id"], fetcher=fetch)
            self.assertTrue(dismissed["is_dismissed"])
            self.assertEqual(fetch.call_count, 2)
            forced = updater.check_for_updates(self.plugin, self.data, force=True, fetcher=fetch)
            self.assertFalse(forced["from_cache"])
            self.assertTrue(forced["is_dismissed"])
            self.assertEqual(fetch.call_count, 3)
            release["tag_name"] = "v1.6.1"
            new_version = updater.check_for_updates(self.plugin, self.data, force=True, fetcher=fetch)
            self.assertFalse(new_version["is_dismissed"])
            self.assertEqual(new_version["dismissed_version"], cached["latest_id"])

    def test_release_404_falls_back_to_main_and_compares_same_version_commits(self) -> None:
        head = {"sha": self.commit, "commit": {"message": "Changes", "committer": {"date": "2025-01-01T00:00:00Z"}}}
        fetch = self.fetcher({self.latest_url: (404, {}), self.commit_url: (200, head),
                              self.manifest_url: (200, {"version": "1.5.0"})})
        unknown = updater.check_for_updates(self.plugin, self.data, fetcher=fetch)
        self.assertTrue(unknown["ok"])
        self.assertEqual(unknown["channel"], "main")
        self.assertFalse(unknown["update_available"])
        self.assertEqual(fetch.call_args_list, [mock.call(url) for url in (self.latest_url, self.commit_url, self.manifest_url)])
        self.write_json(self.plugin / ".agy-codex-install.json", {"commitSha": "a" * 40})
        fallback = updater.check_for_updates(self.plugin, self.data, fetcher=fetch)
        self.assertTrue(fallback["update_available"])
        self.assertEqual(fallback["remote_commit"], self.commit)
        self.assertEqual(fallback["latest_id"], f"1.5.0@{self.commit[:7]}")
        self.assertEqual(fallback["notes"], "Changes")
        self.assertEqual(fallback["published_at"], head["commit"]["committer"]["date"])
        fetch.reset_mock()
        main = updater.check_for_updates(self.plugin, self.data, channel="main", fetcher=fetch)
        self.assertFalse(main["from_cache"])
        self.assertEqual(fetch.call_args_list, [mock.call(url) for url in (self.commit_url, self.manifest_url)])

    def test_check_handles_network_and_rate_limit(self) -> None:
        for fetch in (mock.Mock(side_effect=TimeoutError("timed out")), self.fetcher({self.latest_url: (403, {})}),
                      self.fetcher({self.latest_url: (429, {})})):
            result = updater.check_for_updates(self.plugin, self.data, force=True, fetcher=fetch)
            self.assertFalse(result["ok"])
            self.assertFalse(result["update_available"])
            self.assertFalse(result["from_cache"])
            self.assertTrue(result["error"])
        with self.assertRaises(ValueError):
            updater.check_for_updates(self.plugin, self.data, channel="nightly")

    def test_api_rate_limit_falls_back_to_main_atom_feed(self) -> None:
        atom_url = f"https://github.com/{self.repo}/commits/main.atom"
        atom_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?><feed><entry>'
            b"<id>tag:github.com,2008:Grit::Commit/" + self.commit.encode() + b"</id>"
            b"<title>feat: rate-limit-free sync &amp; tests</title>"
            b"<updated>2026-11-27T09:48:40Z</updated>"
            b"</entry></feed>"
        )
        fetch = self.fetcher({
            self.latest_url: (403, {}),
            self.commit_url: (429, {}),
            atom_url: (200, atom_xml),
            self.manifest_url: (200, {"version": "1.6.0"}),
        })
        res_auto = updater.check_for_updates(self.plugin, self.data, force=True, channel="auto", fetcher=fetch)
        self.assertTrue(res_auto["ok"])
        self.assertEqual(res_auto["channel"], "main")
        self.assertTrue(res_auto["update_available"])
        self.assertEqual(res_auto["remote_version"], "1.6.0")
        self.assertEqual(res_auto["remote_commit"], self.commit)
        self.assertEqual(res_auto["notes"], "feat: rate-limit-free sync & tests")

        res_main = updater.check_for_updates(self.plugin, self.data, force=True, channel="main", fetcher=fetch)
        self.assertTrue(res_main["ok"])
        self.assertEqual(res_main["channel"], "main")
        self.assertEqual(res_main["remote_commit"], self.commit)

    def test_download_blocks_non_https_and_off_domain_redirects(self) -> None:
        url = "https://github.com/example/source"
        second = "https://objects.githubusercontent.com/example/source"
        for unsafe in ("http://github.com/example/source", "https://github.com.example/source", "https://example.com/source"):
            with self.subTest(unsafe=unsafe):
                fetch = mock.Mock()
                with self.assertRaises(ValueError):
                    updater._download_zip(unsafe, self.root / "download.zip", fetcher=fetch)
                fetch.assert_not_called()
                seen = []

                class FakeHTTPS(urllib.request.HTTPSHandler):
                    def https_open(self, request):
                        seen.append(request.full_url)
                        headers = Message()
                        headers["Location"] = {url: second, second: unsafe}[request.full_url]
                        response = urllib.response.addinfourl(io.BytesIO(), headers, request.full_url, 302)
                        response.msg = "Found"
                        return response

                opener = urllib.request.build_opener(updater._GitHubRedirectHandler(), FakeHTTPS())
                with mock.patch.object(updater.urllib.request, "build_opener", return_value=opener), \
                     mock.patch.object(urllib.request.HTTPHandler, "http_open", side_effect=AssertionError("HTTP requested")):
                    with self.assertRaises(ValueError):
                        updater._fetch(url)
                self.assertEqual(seen, [url, second])

    def test_download_rejects_oversize_stream_and_sha_mismatch(self) -> None:
        url = "https://github.com/example/source"
        target = self.root / "download.zip"
        self.assertEqual(updater.MAX_DOWNLOAD_BYTES, 32 * 1024 * 1024)
        for content_length in (None, str(updater.MAX_DOWNLOAD_BYTES + 1)):
            with self.subTest(content_length=content_length):
                response = io.BytesIO(b"x" * 33)
                response.headers = {} if content_length is None else {"Content-Length": content_length}
                with mock.patch.object(updater, "MAX_DOWNLOAD_BYTES", 32), self.assertRaisesRegex(ValueError, "32 MB"):
                    updater._read_limited(response)
        body = b"example"
        fetch = self.fetcher({url: (200, body)})
        with mock.patch.object(updater, "MAX_DOWNLOAD_BYTES", len(body) - 1), self.assertRaisesRegex(ValueError, "32 MB"):
            updater._download_zip(url, target, fetcher=fetch)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            updater._download_zip(url, target, fetcher=fetch, expected_sha256="0" * 64)
        self.assertFalse(target.exists())
        updater._download_zip(url, target, fetcher=fetch, expected_sha256=hashlib.sha256(body).hexdigest())
        self.assertEqual(target.read_bytes(), body)

    def test_extract_rejects_zip_slip_absolute_windows_and_symlinks(self) -> None:
        archive = self.root / "malicious.zip"
        dest = self.root / "extracted"
        link = zipfile.ZipInfo("folder/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entry in ("../escaped.txt", "folder/../../escaped.txt", "/absolute.txt", "C:/drive.txt", "C:drive.txt",
                      "folder" + chr(92) + ".." + chr(92) + "escaped.txt", "folder/normal.txt:alternate", link):
            with self.subTest(entry=entry):
                with zipfile.ZipFile(archive, "w") as out:
                    out.writestr("folder/install.py", "# not executable")
                    out.writestr("folder/plugin/agy-codex/plugin.json", "{}")
                    out.writestr("folder/plugin/agy-codex/assets/app.html", "<html></html>")
                    out.writestr(entry, "../escaped.txt")
                with self.assertRaises(ValueError):
                    updater._safe_extract_zip(archive, dest)
                self.assertFalse((self.root / "escaped.txt").exists())
                self.assertFalse(any(dest.iterdir()))

    def test_dev_checkout_is_not_overwritten(self) -> None:
        repo = self.root / "source"
        dev_plugin = repo / "plugin" / "agy-codex"
        dev_plugin.mkdir(parents=True)
        (repo / ".git").write_text("gitdir: ../worktree", encoding="utf-8")
        self.write_json(dev_plugin / "plugin.json", self.metadata)
        local = updater.read_local_install_info(dev_plugin)
        self.assertFalse(local["can_self_update"])
        self.assertIn("git pull", local["self_update_blocked_reason"])
        fetch = mock.Mock()
        result = updater.start_plugin_update(dev_plugin, self.data, fetcher=fetch, sync=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], local["self_update_blocked_reason"])
        fetch.assert_not_called()
        self.assertFalse((self.data / "update.lock").exists())

    def test_stale_lock_is_recovered_by_age_or_dead_pid(self) -> None:
        state = self.data / "update-state.json"
        lock = self.data / "update.lock"
        for stale_by_age, pid_alive in ((True, True), (False, False)):
            with self.subTest(stale_by_age=stale_by_age):
                self.write_json(state, {"status": "installing"})
                self.write_json(lock, {"pid": os.getpid()})
                if stale_by_age:
                    old = time.time() - updater.STALE_LOCK_SEC - 10
                    os.utime(lock, (old, old))
                with mock.patch.object(updater, "_pid_alive", return_value=pid_alive):
                    result = updater.get_update_status(self.plugin, self.data)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "上次更新进程异常中断，请重试")
                self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "failed")
                self.assertFalse(lock.exists())

    def test_async_update_has_only_one_active_owner(self) -> None:
        entered, ready = threading.Event(), threading.Event()

        def fetch(url: str):
            entered.set()
            ready.wait(5)
            return 429, b"", {}

        result = updater.start_plugin_update(self.plugin, self.data, fetcher=fetch)
        self.assertEqual(result["status"], "checking")
        try:
            self.assertTrue(entered.wait(5))
            duplicate = updater.start_plugin_update(self.plugin, self.data, fetcher=mock.Mock(side_effect=AssertionError), sync=True)
            self.assertEqual(duplicate["started_at"], result["started_at"])
            self.assertEqual(duplicate["status"], "checking")
        finally:
            ready.set()
        deadline = time.monotonic() + 5
        lock = self.data / "update.lock"
        while lock.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(lock.exists())
        self.assertEqual(updater.get_update_status(self.plugin, self.data)["status"], "failed")

    def make_archive(self, version: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("install.py", "profiles/worker.config.toml", "plugin/agy-codex/mcp_config.json"):
                archive.write(REPO_ROOT / name, f"fixture/{name}")
            archive.writestr("fixture/plugin/agy-codex/plugin.json", json.dumps({**self.metadata, "version": version}))
            archive.writestr("fixture/plugin/agy-codex/assets/app.html", "<html>new</html>")
        return buffer.getvalue()

    def test_main_update_invokes_real_installer_and_records_commit(self) -> None:
        codex_home = self.root / "codex-home"
        version = "1.6.0"
        fetch = self.fetcher({
            self.commit_url: (200, {"sha": self.commit, "commit": {"message": "Update fixtures"}}),
            self.manifest_url: (200, {"version": version}),
            self.main_zip_url: (200, self.make_archive(version)),
        })
        result = updater.start_plugin_update(self.plugin, self.data, channel="main", expected_version=f"{version}@{self.commit[:7]}",
                                             codex_home=codex_home, plugins_dir_override=self.plugin.parent,
                                             fetcher=fetch, sync=True)
        self.assertEqual(result["status"], "completed", result["error"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["target_version"], version)
        self.assertEqual(result["target_commit"], self.commit)
        self.assertEqual(result["current_commit"], self.commit)
        manifest = json.loads((self.plugin / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], version)
        receipt = json.loads((self.plugin / ".agy-codex-install.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["commitSha"], self.commit)
        state = json.loads((self.data / "update-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertFalse(result["check"]["update_available"])
        self.assertTrue((codex_home / "worker.config.toml").is_file())
        self.assertFalse((self.data / "update.lock").exists())
        self.assertEqual(list((self.data / "update-jobs").iterdir()), [])

    def test_release_checksum_mismatch_stops_before_install(self) -> None:
        tag = "v1.6.0"
        archive_name = f"example-plugin-{tag}.zip"
        sha_url = f"https://github.com/{self.repo}/releases/download/{tag}/{archive_name}.sha256"
        zip_url = f"https://codeload.github.com/{self.repo}/zip/refs/tags/{tag}"
        release = {"tag_name": tag, "assets": [
            {"name": archive_name, "digest": "sha256:" + "0" * 64},
            {"name": archive_name + ".sha256", "browser_download_url": sha_url, "digest": "sha256:" + "1" * 64},
        ]}
        fetch = self.fetcher({self.latest_url: (200, release), zip_url: (200, self.make_archive("1.6.0")),
                              sha_url: (200, ("0" * 64 + f"  {archive_name}").encode())})
        with mock.patch.object(updater, "_plugin_install") as install:
            result = updater.start_plugin_update(self.plugin, self.data, channel="release", fetcher=fetch, sync=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("SHA-256", result["error"])
        install.assert_not_called()
        self.assertEqual(fetch.call_args_list, [mock.call(url) for url in (self.latest_url, sha_url, zip_url)])
