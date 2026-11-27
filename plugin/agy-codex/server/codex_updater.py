"""GitHub update checks and safe installer orchestration (stdlib only)."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

DEFAULT_REPO = "Chino0w0/agy-codex"
DEFAULT_CHECK_TTL_SEC = 12 * 3600
STALE_LOCK_SEC = 180
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})

_CHECK_FILE = "update-check.json"
_STATE_FILE = "update-state.json"
_LOCK_FILE = "update.lock"
_CHANNELS = ("auto", "release", "main")
_ACTIVE = frozenset({"checking", "downloading", "verifying", "installing"})
_PHASE_MESSAGES = {
    "idle": "尚未开始更新",
    "checking": "正在检查最新版本...",
    "downloading": "正在从 GitHub 下载更新包...",
    "verifying": "正在校验并解压更新包...",
    "installing": "正在原子热同步安装插件文件...",
    "completed": "更新完成",
    "failed": "更新失败",
}
_DEV_REASON = "当前运行在本地开发源码目录中，请使用 git pull 与 install.ps1/install.sh 更新"
_INTERRUPTED = "上次更新进程异常中断，请重试"
_MAX_EXTRACT_BYTES = 128 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
_SEMVER = re.compile(
    r"[vV]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_REPO_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_WINDOWS_DEVICE = re.compile(r"(?i)(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])")
_REQUIRED_ARCHIVE_FILES = ("install.py", "plugin/agy-codex/plugin.json", "plugin/agy-codex/assets/app.html")
Fetcher = Callable[..., tuple[int, bytes, dict[str, str]]]


def _version_key(version: str) -> tuple[Any, ...]:
    match = _SEMVER.fullmatch(version.strip())
    if not match:
        raise ValueError(f"无效的语义版本: {version!r}")
    major, minor, patch, prerelease = match.groups()
    parts = []
    for part in prerelease.split(".") if prerelease else ():
        if part.isdecimal():
            if len(part) > 1 and part.startswith("0"):
                raise ValueError(f"无效的预发布版本: {version!r}")
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return int(major), int(minor), int(patch), prerelease is None, tuple(parts)


def compare_versions(a: str, b: str) -> int:
    left, right = _version_key(a), _version_key(b)
    return (left > right) - (left < right)


def _as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _as_object(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8", newline="\n") as writer:
        temp = Path(writer.name)
        try:
            json.dump(payload, writer, ensure_ascii=False, indent=2)
            writer.write("\n")
            writer.close()
            os.replace(temp, path)
        finally:
            writer.close()
            temp.unlink(missing_ok=True)


def read_local_install_info(plugin_root: Path) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    metadata = _read_json(plugin_root / "plugin.json")
    receipt_path = plugin_root / ".agy-codex-install.json"
    receipt = _read_json(receipt_path)
    reason = None
    if (plugin_root.parent.parent / ".git").exists() and not receipt_path.is_file():
        reason = _DEV_REASON
    elif not plugin_root.parent.is_dir() or not os.access(plugin_root.parent, os.W_OK | os.X_OK):
        reason = "插件安装目录不可写，请检查目录权限或手动安装更新"
    commit = str(receipt.get("commitSha") or "")
    return {
        "current_version": str(metadata.get("version") or "0.0.0"),
        "current_commit": commit.lower() if _COMMIT.fullmatch(commit) else None,
        "repository": metadata.get("repository"),
        "homepage": metadata.get("homepage"),
        "installed_at": receipt.get("installedAt"),
        "source_root": receipt.get("sourceRoot"),
        "can_self_update": reason is None,
        "self_update_blocked_reason": reason,
    }


def parse_github_repo(plugin_meta: dict[str, Any]) -> str:
    for value in (os.environ.get("AGY_CODEX_UPDATE_REPO"), plugin_meta.get("repository"), plugin_meta.get("homepage")):
        if isinstance(value, dict):
            value = value.get("url")
        if not isinstance(value, str):
            continue
        candidate = value.strip().removeprefix("git+")
        if "://" in candidate:
            try:
                url = urllib.parse.urlsplit(candidate)
                if url.hostname not in {"github.com", "www.github.com"}:
                    continue
                candidate = url.path.strip("/")
            except ValueError:
                continue
        candidate = candidate.removesuffix(".git")
        if _REPO_SLUG.fullmatch(candidate):
            return candidate
    return DEFAULT_REPO


def _validate_download_url(url: str) -> None:
    if "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("拒绝格式不安全的更新 URL")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
        or parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None
    ):
        raise ValueError("更新 URL 必须使用白名单内的 HTTPS GitHub 主机")


class _GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _validate_download_url(newurl)
        except ValueError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(response) -> bytes:
    length = response.headers.get("Content-Length", "")
    if length.isdecimal() and int(length) > MAX_DOWNLOAD_BYTES:
        raise ValueError("下载大小超过 32 MB 限制")
    deadline = time.monotonic() + 90
    data = bytearray()
    while chunk := response.read(min(_CHUNK_SIZE, MAX_DOWNLOAD_BYTES + 1 - len(data))):
        data.extend(chunk)
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError("下载大小超过 32 MB 限制")
        if time.monotonic() > deadline:
            raise TimeoutError("下载更新包超时")
    return bytes(data)


def _fetch(url: str) -> tuple[int, bytes, dict[str, str]]:
    _validate_download_url(url)
    opener = urllib.request.build_opener(_GitHubRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "agy-codex-updater"})
    try:
        with opener.open(request, timeout=15) as response:
            _validate_download_url(response.geturl())
            return response.status, _read_limited(response), dict(response.headers)
    except urllib.error.HTTPError as exc:
        with exc:
            if exc.code < 400:
                raise ValueError("GitHub 返回了无法安全处理的重定向") from exc
            return exc.code, b"", dict(exc.headers or {})


def _request(url: str, fetcher: Fetcher | None) -> tuple[int, bytes, dict[str, str]]:
    _validate_download_url(url)
    status, body, headers = (fetcher or _fetch)(url)
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError("下载大小超过 32 MB 限制")
    return status, body, headers


def _require_success(status: int) -> None:
    if status in (403, 429):
        raise ValueError(f"GitHub 请求受限 (HTTP {status})，请稍后重试")
    if status != 200:
        raise ValueError(f"GitHub 请求失败 (HTTP {status})")


def _request_json(url: str, fetcher: Fetcher | None) -> dict[str, Any]:
    status, body, _ = _request(url, fetcher)
    _require_success(status)
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("GitHub 返回了无效的 JSON 对象")
    return result


def _download_zip(url: str, dest: Path, *, fetcher: Fetcher | None = None,
                  expected_sha256: str | None = None) -> None:
    status, body, _ = _request(url, fetcher)
    _require_success(status)
    if expected_sha256 and hashlib.sha256(body).hexdigest() != expected_sha256.lower():
        raise ValueError("更新包 SHA-256 校验失败")
    dest.write_bytes(body)


def _archive_member_path(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.orig_filename
    parts = tuple(name.removesuffix("/").split("/"))
    if any(
        not part or part in {".", ".."} or part.endswith((".", " "))
        or any(c in r'\:<>"|?*' or ord(c) < 32 for c in part)
        or _WINDOWS_DEVICE.fullmatch(part.partition(".")[0].rstrip(" "))
        for part in parts
    ):
        raise ValueError(f"不安全的 ZIP 路径: {name!r}")
    if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
        raise ValueError(f"更新包不允许包含符号链接: {name!r}")
    return parts


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.is_symlink() or any(dest_dir.iterdir()):
        raise ValueError("解压目标必须是空目录")
    dest_dir = dest_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > 20000 or sum(info.file_size for info in members) > _MAX_EXTRACT_BYTES:
            raise ValueError("更新包解压后超过安全限制")
        files: set[Path] = set()
        paths = []
        for info in members:
            target = dest_dir.joinpath(*_archive_member_path(info))
            if not target.resolve().is_relative_to(dest_dir):
                raise ValueError(f"ZIP 路径超出解压目录: {info.filename!r}")
            paths.append((info, target))
            if not info.is_dir():
                files.add(target)
        roots = {
            install.parent for install in files if install.name == "install.py"
            and all(install.parent / name in files for name in _REQUIRED_ARCHIVE_FILES)
        }
        if len(roots) != 1:
            raise ValueError("更新包缺少唯一的插件安装根目录、清单或 UI 构建产物")
        extracted_bytes = 0
        for info, target in paths:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, target.open("xb") as writer:
                while chunk := reader.read(_CHUNK_SIZE):
                    extracted_bytes += len(chunk)
                    if extracted_bytes > _MAX_EXTRACT_BYTES:
                        raise ValueError("更新包解压后超过安全限制")
                    writer.write(chunk)
        return roots.pop()


def _release_checksum(release: dict[str, Any], repo: str, tag: str, fetcher: Fetcher | None) -> str | None:
    # 不能用校验文件自身或其他构建产物的 digest 校验源码 ZIP。
    basename = repo.rsplit("/", 1)[1]
    names = {f"{basename}-{tag}.zip", f"{basename}-{tag.lstrip('vV')}.zip"}
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    digests = set()
    for item in map(_as_object, assets):
        name = item.get("name", "")
        if not isinstance(name, str):
            continue
        digest = None
        if name.endswith(".sha256") and name.removesuffix(".sha256") in names:
            status, content, _ = _request(str(item.get("browser_download_url") or ""), fetcher)
            _require_success(status)
            fields = content.decode("utf-8-sig").strip().split(maxsplit=1)
            if len(fields) == 1 or (len(fields) == 2 and fields[1].lstrip("*") == name.removesuffix(".sha256")):
                digest = fields[0]
            if not digest or not _SHA256.fullmatch(digest):
                raise ValueError("GitHub Release 提供的 SHA-256 校验文件无效")
        elif name in names:
            value = item.get("digest")
            if isinstance(value, str) and value.startswith("sha256:"):
                digest = value.removeprefix("sha256:")
                if not _SHA256.fullmatch(digest):
                    raise ValueError("GitHub Release 提供的 SHA-256 摘要无效")
        if digest:
            digests.add(digest.lower())
    if len(digests) > 1:
        raise ValueError("GitHub Release 提供了互相冲突的 SHA-256 摘要")
    return next(iter(digests), None)


def _remote_update(repo: str, channel: str, fetcher: Fetcher | None) -> tuple[dict[str, Any], str, str | None]:
    api_url = f"https://api.github.com/repos/{repo}"
    archive_url = f"https://codeload.github.com/{repo}/zip"
    if channel != "main":
        status, raw, _ = _request(f"{api_url}/releases/latest", fetcher)
        if status != 404:
            _require_success(status)
            release = _as_object(json.loads(raw))
            tag = str(release.get("tag_name") or "")
            _version_key(tag)
            version = tag[1:] if tag.startswith(("v", "V")) else tag
            return {
                "channel": "release",
                "remote_version": version,
                "latest_id": version,
                "release_url": release.get("html_url"),
                "notes": str(release.get("body") or "")[:1200],
                "published_at": release.get("published_at"),
            }, f"{archive_url}/refs/tags/{urllib.parse.quote(tag, safe='')}", _release_checksum(release, repo, tag, fetcher)

    head = _request_json(f"{api_url}/commits/main", fetcher)
    sha = str(head.get("sha") or "").lower()
    if not _COMMIT.fullmatch(sha):
        raise ValueError("GitHub main 未返回有效的提交 SHA")
    manifest = _request_json(f"https://raw.githubusercontent.com/{repo}/{sha}/plugin/agy-codex/plugin.json", fetcher)
    commit = _as_object(head.get("commit"))
    version = str(manifest.get("version") or "")
    return {
        "channel": "main",
        "remote_version": version,
        "remote_commit": sha,
        "remote_short_commit": sha[:7],
        "latest_id": f"{version}@{sha[:7]}",
        "release_url": f"https://github.com/{repo}/commit/{sha}",
        "notes": str(commit.get("message") or "")[:1200],
        "published_at": _as_object(commit.get("committer")).get("date") or _as_object(commit.get("author")).get("date"),
    }, f"{archive_url}/{sha}", None


def _validate_channel(channel: str) -> None:
    if channel not in _CHANNELS:
        raise ValueError("channel 必须是 auto、release 或 main")


def _check_for_updates(plugin_root: Path, data_dir: Path, *, force: bool, channel: str,
                       dismiss_version: str | None, fetcher: Fetcher | None
                       ) -> tuple[dict[str, Any], str | None, str | None]:
    _validate_channel(channel)
    local = read_local_install_info(plugin_root)
    repo = parse_github_repo(local)
    cache_path = data_dir / _CHECK_FILE
    cache = _read_json(cache_path)
    now = time.time()
    context = {name: local[name] for name in (
        "current_version", "current_commit", "can_self_update", "self_update_blocked_reason",
    )}
    result = {
        **context, "ok": True, "repo": repo, "channel": channel, "requested_channel": channel, "remote_version": None,
        "remote_commit": None, "remote_short_commit": None, "latest_id": None, "update_available": False,
        "dismissed_version": cache.get("dismissed_version"), "is_dismissed": False, "release_url": None,
        "notes": "", "published_at": None, "checked_at": None, "from_cache": False, "error": None,
    }
    if dismiss_version is not None:
        with _lock_transition(data_dir):
            cache = _read_json(cache_path)
            result.update(cache, dismissed_version=dismiss_version)
            result.update(is_dismissed=bool(dismiss_version and dismiss_version == result["latest_id"]))
            _write_json(cache_path, result)
        return {**result, **context, "from_cache": bool(cache)}, None, None

    checked_at = cache.get("checked_at")
    cached_req_channel = cache.get("requested_channel") or cache.get("channel")
    if (
        not force and cache.get("ok") and isinstance(checked_at, (int, float))
        and 0 <= now - checked_at < DEFAULT_CHECK_TTL_SEC
        and cached_req_channel == channel
        and all(cache.get(k) == result[k] for k in ("repo", "current_version", "current_commit"))
    ):
        return {**result, **cache, **context, "from_cache": True}, None, None

    result["checked_at"] = now
    try:
        remote, zip_url, checksum = _remote_update(repo, channel, fetcher)
        version_delta = compare_versions(remote["remote_version"], result["current_version"])
        remote_commit, current_commit = remote.get("remote_commit"), result["current_commit"]
        result.update(remote, update_available=version_delta > 0 or bool(
            version_delta == 0 and remote_commit and current_commit and current_commit != remote_commit
        ))
        # 请求可能在忽略操作之前开始，保存时以最新的忽略记录为准。
        with _lock_transition(data_dir):
            result["dismissed_version"] = _read_json(cache_path).get("dismissed_version")
            result["is_dismissed"] = bool(result["dismissed_version"] and result["dismissed_version"] == result["latest_id"])
            _write_json(cache_path, result)
        return result, zip_url, checksum
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        result.update(ok=False, error=f"检查更新失败: {exc}", update_available=False)
        return result, None, None


def check_for_updates(plugin_root: Path, data_dir: Path, *, force: bool = False, channel: str = "auto",
                      dismiss_version: str | None = None, fetcher: Fetcher | None = None) -> dict[str, Any]:
    result, _, _ = _check_for_updates(
        plugin_root, data_dir, force=force, channel=channel, dismiss_version=dismiss_version, fetcher=fetcher,
    )
    return result


def _pid_alive(pid: Any) -> bool:
    if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # Windows 上 os.kill(pid, 0) 可能终止目标进程；改用只读进程句柄。
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: still running
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@contextmanager
def _lock_transition(data_dir: Path) -> Iterator[None]:
    # 将判断过期、移除旧锁和原子新建串行化，避免不同 MCP 进程误删互相的新锁。
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "update-lock.guard").open("a+b") as guard:
        if os.name == "nt":
            import msvcrt

            if guard.tell() == 0:
                guard.write(b"\0")
                guard.flush()
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)


def _with_phase(state: dict[str, Any], phase: str, *, error: str | None = None,
                message: str | None = None) -> dict[str, Any]:
    return {
        **state, "status": phase, "phase_label": _PHASE_MESSAGES[phase], "updated_at": time.time(),
        "message": message or error or _PHASE_MESSAGES[phase], "error": error,
    }


def _read_state_and_recover(data_dir: Path) -> tuple[dict[str, Any], bool]:
    """调用方须先获取状态转换锁。"""
    state_path = data_dir / _STATE_FILE
    lock = data_dir / _LOCK_FILE
    state = _read_json(state_path)
    try:
        live = time.time() - lock.stat().st_mtime <= STALE_LOCK_SEC and _pid_alive(_read_json(lock).get("pid"))
        if not live:
            lock.unlink(missing_ok=True)
    except FileNotFoundError:
        live = False
    if not live and state.get("status") in _ACTIVE:
        state = _with_phase(state, "failed", error=_INTERRUPTED)
        _write_json(state_path, state)
    return state, live


def _status_payload(plugin_root: Path, data_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    local = read_local_install_info(plugin_root)
    return {
        "status": "idle", "phase_label": _PHASE_MESSAGES["idle"], "target_version": None,
        "target_commit": None, "channel": "auto", "started_at": None, "updated_at": None,
        "completed_at": None, "restart_required": False, "message": _PHASE_MESSAGES["idle"], "error": None,
        **state,
        **{name: local[name] for name in (
            "current_version", "current_commit", "can_self_update", "self_update_blocked_reason",
        )},
        "check": _read_json(data_dir / _CHECK_FILE) or None,
    }


def get_update_status(plugin_root: Path, data_dir: Path) -> dict[str, Any]:
    if not (data_dir / _STATE_FILE).exists() and not (data_dir / _LOCK_FILE).exists():
        return _status_payload(plugin_root, data_dir, {})
    with _lock_transition(data_dir):
        state, _ = _read_state_and_recover(data_dir)
    return _status_payload(plugin_root, data_dir, state)


def _plugin_install(source: Path, plugins_dir: Path, codex_home: Path, data_dir: Path,
                    commit: str | None) -> None:
    script = source / "install.py"
    args = [sys.executable, str(script), "--skip-doctor", "--plugins-dir", str(plugins_dir),
            "--codex-home", str(codex_home), "--data-dir", str(data_dir)]
    if commit and "--commit-sha" in script.read_text(encoding="utf-8"):
        args += ["--commit-sha", commit]
    result = subprocess.run(
        args, cwd=source, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"安装程序失败 (退出码 {result.returncode}): {detail}")


def start_plugin_update(
    plugin_root: Path, data_dir: Path, *, channel: str = "auto", expected_version: str | None = None,
    codex_home: Path | None = None, plugins_dir_override: Path | None = None,
    fetcher: Fetcher | None = None, sync: bool = False,
) -> dict[str, Any]:
    _validate_channel(channel)
    plugin_root, data_dir = plugin_root.resolve(), data_dir.resolve()
    initial = _status_payload(plugin_root, data_dir, {})
    if not initial["can_self_update"] and plugins_dir_override is None:
        return _with_phase(initial, "failed", error=initial["self_update_blocked_reason"])
    target_plugins = (plugins_dir_override if plugins_dir_override is not None else plugin_root.parent).resolve()
    target_home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    if data_dir.is_relative_to(target_plugins / "agy-codex"):
        return _with_phase(initial, "failed", error="更新数据目录不能位于目标插件目录内")

    lock = data_dir / _LOCK_FILE
    state_path = data_dir / _STATE_FILE
    job_id = uuid.uuid4().hex
    state = _with_phase({"channel": channel, "started_at": time.time()}, "checking")
    with _lock_transition(data_dir):
        previous, live = _read_state_and_recover(data_dir)
        if live:
            return _status_payload(plugin_root, data_dir, previous)
        try:
            with lock.open("x", encoding="utf-8") as writer:
                json.dump({"pid": os.getpid(), "job_id": job_id}, writer)
            _write_json(state_path, state)
        except OSError:
            lock.unlink(missing_ok=True)
            raise

    def owns_lock() -> bool:
        claim = _read_json(lock)
        return claim.get("pid") == os.getpid() and claim.get("job_id") == job_id

    def advance(phase: str, *, error: str | None = None, message: str | None = None, **fields: Any) -> None:
        nonlocal state
        with _lock_transition(data_dir):
            if not owns_lock():
                raise RuntimeError(_INTERRUPTED)
            lock.touch()
            state = _with_phase({**state, **fields}, phase, error=error, message=message)
            _write_json(state_path, state)

    def release() -> None:
        with _lock_transition(data_dir):
            if owns_lock():
                lock.unlink(missing_ok=True)

    def perform() -> None:
        try:
            check, url, checksum = _check_for_updates(
                plugin_root, data_dir, force=True, channel=channel, dismiss_version=None, fetcher=fetcher,
            )
            if not check["ok"]:
                raise RuntimeError(check["error"])
            version = check["remote_version"]
            commit = check["remote_commit"]
            if expected_version is not None and expected_version not in (version, f"v{version}", check["latest_id"]):
                raise RuntimeError("最新版本已变化，请重新检查后再更新")
            if not check["update_available"] or not url:
                raise RuntimeError("没有可安装的更新")
            advance("downloading", target_version=version, target_commit=commit, channel="main" if commit else "release")
            jobs = data_dir / "update-jobs"
            if jobs.resolve() != jobs:
                raise ValueError("更新临时目录不能是链接")
            jobs.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"{job_id}-", dir=jobs) as job_path:
                job = Path(job_path)
                archive = job / "update.zip"
                _download_zip(url, archive, fetcher=fetcher)
                advance("verifying")
                if checksum:
                    digest = hashlib.sha256()
                    with archive.open("rb") as reader:
                        while chunk := reader.read(_CHUNK_SIZE):
                            digest.update(chunk)
                    if digest.hexdigest() != checksum:
                        raise ValueError("更新包 SHA-256 校验失败")
                source = _safe_extract_zip(archive, job / "source")
                manifest = _read_json(source / "plugin" / "agy-codex" / "plugin.json")
                if compare_versions(str(manifest.get("version") or ""), version) != 0:
                    raise ValueError("更新包清单版本与 GitHub 版本不一致")
                advance("installing")
                _plugin_install(source, target_plugins, target_home, data_dir, commit)
            cache_path = data_dir / _CHECK_FILE
            with _lock_transition(data_dir):
                cache = _read_json(cache_path)
                if owns_lock() and cache.get("repo") == check["repo"] and cache.get("latest_id") == check["latest_id"]:
                    local = read_local_install_info(plugin_root)
                    _write_json(cache_path, {
                        **cache, "update_available": False, "current_version": local["current_version"],
                        "current_commit": local["current_commit"],
                    })
            label = version if version.startswith(("v", "V")) else f"v{version}"
            advance(
                "completed", completed_at=time.time(), restart_required=True,
                message=f"已同步至 {label}，请在 Antigravity 设置 -> MCP Servers 中刷新 agy-codex 或重启客户端生效",
            )
        except Exception as exc:
            # 后台边界必须为 ZIP、网络或子进程异常写入终态，不能只在 stderr 退出。
            try:
                advance("failed", error=str(exc) or type(exc).__name__)
            except (OSError, RuntimeError):
                # 其他进程已接管过期锁时，不覆盖新任务的状态。
                pass
        finally:
            release()

    if sync:
        perform()
        return get_update_status(plugin_root, data_dir)
    try:
        threading.Thread(target=perform, name="agy-codex-update", daemon=True).start()
    except RuntimeError as exc:
        try:
            advance("failed", error=str(exc))
        finally:
            release()
    return _status_payload(plugin_root, data_dir, state)
