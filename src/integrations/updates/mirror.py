# 拥有 Calc 更新的 HTTPS 请求、私有临时下载、文件凭据和一次安装器派发。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from time import monotonic
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from src.app.version import version_order_key

UPDATE_FAILURE_MESSAGE = "Mirror 酱更新服务请求失败，请稍后重试。"
UPDATE_CHECK_TIMEOUT_SECONDS = 5
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 30
_CHUNK_SIZE = 128 * 1024
_MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024


class UpdateDownloadCancelled(RuntimeError):
    """本次授权已撤销；不是可自动重试的网络错误。"""


def is_newer_version(remote, current) -> bool:
    return version_order_key(remote) > version_order_key(current)


def mirror_update_request_url(api_url: str, app_version: str, cdk: str = "") -> str:
    params = {"current_version": str(app_version).strip()}
    if str(cdk).strip():
        params["cdk"] = str(cdk).strip()
    return f"{api_url}?{urlencode(params)}"


def _require_https(url):
    parsed = urlsplit(str(url))
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("更新地址必须是 HTTPS，且不含用户信息。")


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_https(newurl)  # 在发出重定向请求之前拒绝降级，不把短期地址发到明文连接。
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request, timeout):
    _require_https(request.full_url)
    return urllib.request.build_opener(_HttpsRedirect()).open(request, timeout=timeout)


def _checkpoint(cancel_check, deadline):
    if cancel_check and cancel_check():
        raise UpdateDownloadCancelled("已取消更新下载安装包。")
    if monotonic() > deadline:
        raise TimeoutError("更新请求超过总时限。")


def _read(response, size):
    # HTTPResponse.read1 每次只消费一次底层读，避免低速连续数据把 read(size) 长期占住。
    return response.read1(size)


def fetch_update_info(api_url, app_version, *, cdk="", timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
                      cancel_check=None) -> dict:
    deadline = monotonic() + timeout
    _checkpoint(cancel_check, deadline)
    request = urllib.request.Request(mirror_update_request_url(api_url, app_version, cdk),
        headers={"User-Agent": f"NTE-Drive-Calc/{app_version}", "Accept": "application/json"})
    try:
        with _open(request, timeout) as response:
            chunks, size = [], 0
            while True:
                _checkpoint(cancel_check, deadline)
                block = _read(response, min(_CHUNK_SIZE, _MAX_METADATA_BYTES + 1 - size))
                _checkpoint(cancel_check, deadline)
                if not block:
                    break
                size += len(block)
                if size > _MAX_METADATA_BYTES:
                    raise ValueError("更新响应超过大小限制。")
                chunks.append(block)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("更新响应不是对象。")
        if payload.get("code") != 0:
            return {"has_release": False, "newer": False, "url": "",
                    "message": UPDATE_FAILURE_MESSAGE, "error": "更新服务返回错误状态。"}
        data = payload.get("data")
        if not isinstance(data, dict) or not str(data.get("version_name") or "").strip():
            raise ValueError("更新响应缺少版本。")
        latest = str(data["version_name"]).strip()
        return {"has_release": True, "latest": latest, "newer": is_newer_version(latest, app_version),
                "url": str(data.get("url") or "").strip(), "release_url": "",
                "message": str(data.get("release_note") or "").strip(), "name": latest}
    except UpdateDownloadCancelled:
        raise
    except (ValueError, UnicodeDecodeError, urllib.error.URLError, TimeoutError, OSError) as exc:
        # 异常可能含 CDK/短期地址；此边界只返回类型，不传播网络原文到日志或弹窗。
        return {"has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
                "error": f"更新请求失败（{type(exc).__name__}）。"}


def _plain_stat(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError("更新临时路径类型或所有权已变化。")
    return info


def _parents(directory):
    return tuple((path, info.st_dev, info.st_ino)
                 for path in (*reversed(directory.parents), directory)
                 for info in (_plain_stat(path, directory=True),))


def _version(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@dataclass(frozen=True)
class InstallerReceipt:
    """本地下载字节凭据用于防替换，不代表服务器签名或发行真实性证明。"""
    path: Path
    parents: tuple
    file_version: tuple[int, int, int, int]
    sha256: str
    downloaded: int


def _require_parents(parents):
    if _parents(parents[-1][0]) != parents:
        raise ValueError("更新临时目录身份已变化。")


def _digest(path, cancel_check=None, deadline=float("inf")):
    info = _plain_stat(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if _version(os.fstat(stream.fileno())) != _version(info):
            raise ValueError("安装程序已被替换。")
        while True:
            _checkpoint(cancel_check, deadline)
            block = stream.read(_CHUNK_SIZE)
            _checkpoint(cancel_check, deadline)
            if not block:
                break
            digest.update(block)
        if _version(os.fstat(stream.fileno())) != _version(info):
            raise ValueError("安装程序读取期间已变化。")
    if _version(_plain_stat(path)) != _version(info):
        raise ValueError("安装程序已被替换。")
    return _version(info), digest.hexdigest()


def discard_installer(receipt: InstallerReceipt) -> None:
    """只清理本次已核对文件与空私有目录；替换者和未知成员留给显式诊断。"""
    try:
        _require_parents(receipt.parents)
        if _version(_plain_stat(receipt.path)) != receipt.file_version:
            return
        receipt.path.unlink()
        receipt.path.parent.rmdir()
    except (OSError, ValueError):
        pass  # 保留异常路径，不扩大清理或用清理失败替换原终态。


def validate_installer(receipt: InstallerReceipt, *, cancel_check=None, deadline=float("inf")) -> Path:
    _require_parents(receipt.parents)
    if _digest(receipt.path, cancel_check, deadline) != (receipt.file_version, receipt.sha256):
        raise ValueError("安装程序与本次下载的文件身份或字节不符，请重新下载。")
    return receipt.path


def launch_installer(receipt: InstallerReceipt, *, cancel_check=None):
    _checkpoint(cancel_check, float("inf"))
    path = validate_installer(receipt, cancel_check=cancel_check, deadline=monotonic() + 120)
    _checkpoint(cancel_check, float("inf"))
    # 私有目录及最终校验缩小替换窗口，不宣称消除非协作进程的 stat/派发竞态。
    # 接受后不重发：owner 在进入此函数前消耗本次派发权限。
    return subprocess.Popen([str(path)], cwd=str(path.parent))


def download_update_installer(url, *, destination_dir=None, timeout=UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
                              progress_callback=None, cancel_check=None) -> InstallerReceipt:
    _require_https(url)
    deadline = monotonic() + 15 * 60
    _checkpoint(cancel_check, deadline)
    base = Path(destination_dir or tempfile.gettempdir()).absolute()
    base.mkdir(parents=True, exist_ok=True)
    _parents(base)
    # destination_dir 是父目录，不是可覆盖的固定文件名；每个任务独占新目录。
    directory = Path(tempfile.mkdtemp(prefix="NTE-Drive-Calc-update-", dir=base)).absolute()
    parents = _parents(directory)
    final = directory / "NTE-Drive-Calc-update.exe"
    partial = directory / "NTE-Drive-Calc-update.exe.part"
    owned = {}
    try:
        request = urllib.request.Request(str(url).strip(),
            headers={"User-Agent": "NTE-Drive-Calc updater", "Accept": "application/octet-stream"})
        with _open(request, timeout) as response:
            length = response.headers.get("Content-Length")
            expected = int(length) if length is not None else None
            if expected is not None and not 2 <= expected <= _MAX_INSTALLER_BYTES:
                raise ValueError("安装程序声明长度无效或超过限制。")
            downloaded, prefix = 0, b""
            digest = hashlib.sha256()
            _require_parents(parents)
            with partial.open("xb") as stream:
                owned[partial] = (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino)
                if progress_callback:
                    progress_callback(0, expected or 0)
                while True:
                    _checkpoint(cancel_check, deadline)
                    block = _read(response, _CHUNK_SIZE)
                    _checkpoint(cancel_check, deadline)  # 包含 EOF 与最后一块期间的取消。
                    if not block:
                        break
                    prefix = (prefix + block)[:2]
                    if len(prefix) == 2 and prefix != b"MZ":
                        raise ValueError("下载内容不是 Windows 安装程序。")
                    downloaded += len(block)
                    if downloaded > _MAX_INSTALLER_BYTES or (expected is not None and downloaded > expected):
                        raise ValueError("安装程序超过声明长度或大小限制。")
                    stream.write(block)
                    digest.update(block)
                    if progress_callback:
                        progress_callback(downloaded, expected or 0)
                stream.flush()
            if downloaded < 2 or (expected is not None and downloaded != expected):
                raise ValueError("安装程序下载不完整，请重新下载。")
        _checkpoint(cancel_check, deadline)
        _require_parents(parents)
        if (_plain_stat(partial).st_dev, _plain_stat(partial).st_ino) != owned[partial]:
            raise ValueError("下载暂存文件身份已变化。")
        # 本次唯一目录里 final 尚不存在；不覆盖同名的外来文件。
        partial.rename(final)
        owned[final] = owned.pop(partial)
        receipt = InstallerReceipt(final, parents, _version(_plain_stat(final)), digest.hexdigest(), downloaded)
        _checkpoint(cancel_check, deadline)
        validate_installer(receipt, cancel_check=cancel_check, deadline=deadline)
        _checkpoint(cancel_check, deadline)
        return receipt
    except BaseException:
        try:
            _require_parents(parents)
            for path, identity in owned.items():
                info = _plain_stat(path)
                if (info.st_dev, info.st_ino) == identity:
                    path.unlink()
            directory.rmdir()
        except (OSError, ValueError):
            pass
        raise
