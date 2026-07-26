# 检查 Mirror 酱资源更新并显示版本更新弹窗。
"""MirrorChyan update API integration and update-dialog helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.theme import themed_style

UPDATE_FAILURE_MESSAGE = "Mirror 酱更新服务请求失败，请稍后重试。"
UPDATE_CHECK_TIMEOUT_SECONDS = 5
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 30
_UPDATE_DOWNLOAD_CHUNK_SIZE = 128 * 1024


class UpdateDownloadCancelled(RuntimeError):
    """Raised when the user cancels an in-app installer download."""


def is_newer_version(remote, current) -> bool:
    def nums(value):
        parts = [int(item) for item in re.findall(r"\d+", str(value))]
        return (parts + [0, 0, 0])[:3]

    return nums(remote) > nums(current)


def mirror_update_request_url(api_url: str, app_version: str, cdk: str = "") -> str:
    """Build the documented Mirror API request without logging the CDK."""
    params = {"current_version": str(app_version).strip()}
    if str(cdk).strip():
        params["cdk"] = str(cdk).strip()
    return f"{api_url}?{urlencode(params)}"


def fetch_update_info(
    api_url: str,
    app_version: str,
    *,
    cdk: str = "",
    timeout: int = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict:
    """Read one Mirror resource response.

    ``data.url`` is a short-lived download URL.  It is intentionally kept out
    of account settings and logs; it is only opened when the user explicitly
    requests a download.
    """
    request = urllib.request.Request(
        mirror_update_request_url(api_url, app_version, cdk),
        headers={"User-Agent": f"NTE-Drive-Calc/{app_version}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": f"Mirror 酱响应不是有效 JSON：{exc}",
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": "Mirror 酱响应不是对象。",
        }
    code = payload.get("code")
    if code != 0:
        return {
            "has_release": False,
            "newer": False,
            "url": "",
            "message": str(payload.get("msg") or "Mirror 酱未返回可用更新信息。"),
            "error": f"Mirror 酱错误码：{code}",
        }
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    latest = str(data.get("version_name") or "").strip()
    if not latest:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": "Mirror 酱响应缺少 version_name。",
        }
    return {
        "has_release": True,
        "latest": latest,
        "newer": is_newer_version(latest, app_version),
        "url": str(data.get("url") or "").strip(),
        "release_url": "",
        "message": str(data.get("release_note") or "").strip(),
        "name": latest,
    }


def download_update_installer(
    url: str,
    *,
    destination_dir: str | os.PathLike[str] | None = None,
    timeout: int = UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
    progress_callback=None,
    cancel_check=None,
) -> dict:
    """Download a Mirror installer to a temporary directory.

    The URL returned by Mirror is short-lived, so the file is deliberately not
    stored in account settings.  A partial file is never launched and is
    removed if downloading fails or is cancelled.
    """
    if not str(url).strip().lower().startswith(("https://", "http://")):
        raise ValueError("Mirror 下载地址无效。")
    if cancel_check and cancel_check():
        raise UpdateDownloadCancelled("已取消更新下载安装包。")

    target_dir = Path(destination_dir) if destination_dir else Path(tempfile.mkdtemp(prefix="NTE-Drive-Calc-update-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / "NTE-Drive-Calc-update.exe"
    partial_path = final_path.with_suffix(".exe.part")
    downloaded = 0
    expected = 0
    try:
        request = urllib.request.Request(
            str(url).strip(),
            headers={"User-Agent": "NTE-Drive-Calc updater", "Accept": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            try:
                expected = max(0, int(response.headers.get("Content-Length") or 0))
            except (TypeError, ValueError):
                expected = 0
            if progress_callback:
                progress_callback(0, expected)
            with partial_path.open("wb") as stream:
                while True:
                    if cancel_check and cancel_check():
                        raise UpdateDownloadCancelled("已取消更新下载安装包。")
                    block = response.read(_UPDATE_DOWNLOAD_CHUNK_SIZE)
                    if not block:
                        break
                    if downloaded == 0 and not block.startswith(b"MZ"):
                        raise RuntimeError("下载内容不是有效的 Windows 安装程序。")
                    stream.write(block)
                    downloaded += len(block)
                    if progress_callback:
                        progress_callback(downloaded, expected)
        if downloaded < 2:
            raise RuntimeError("下载的安装程序为空或不完整。")
        if expected and downloaded != expected:
            raise RuntimeError("安装程序下载不完整，请重新下载。")
        os.replace(partial_path, final_path)
        return {"path": str(final_path), "downloaded": downloaded, "total": expected}
    except BaseException:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise


def update_dialog_link_url(info: dict) -> str:
    return str(info.get("url") or info.get("release_url") or "")


def should_show_startup_update(update_config: dict, info: dict) -> bool:
    latest = str(info.get("latest") or "")
    if update_config.get("never_remind"):
        return False
    if latest and update_config.get("ignored_version") == latest:
        return False
    return True


def show_update_dialog(parent, style_sheet: str, info: dict, app_version: str) -> dict:
    latest = str(info.get("latest") or "未知")
    dialog = QDialog(parent)
    dialog.setWindowTitle("发现更新")
    dialog.setMinimumSize(560, 420)
    dialog.setStyleSheet(style_sheet)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    title = QLabel(f"发现新版本 {latest}")
    title.setStyleSheet("font-size:18px;font-weight:700;color:#58a6ff")
    layout.addWidget(title)
    subtitle = QLabel(f"当前版本: {app_version}")
    subtitle.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(subtitle)
    notes = QTextBrowser()
    notes.setReadOnly(True)
    notes.setMinimumHeight(220)
    notes.setOpenExternalLinks(True)
    notes.setTextInteractionFlags(Qt.TextBrowserInteraction)
    notes.setMarkdown(
        (info.get("message") or "").strip() or "此版本没有填写更新说明。"
    )
    layout.addWidget(notes, 1)
    release_url = str(info.get("release_url") or "").strip()
    if release_url:
        link = QLabel(f'GitHub Release: <a href="{release_url}">{release_url}</a>')
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(link)
    never_cb = QCheckBox("永不提醒")
    ignore_cb = QCheckBox("当前版本不再提醒")
    layout.addWidget(never_cb)
    layout.addWidget(ignore_cb)
    footer = QHBoxLayout()
    footer.addStretch()
    netdisk_button = QPushButton("网盘下载")
    mirror_button = QPushButton("Mirror 下载")
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    def open_netdisk_download():
        dialog.accept()
        getattr(parent, "_show_netdisk_download_dialog")(NETDISK_DOWNLOAD_LINKS)

    def start_mirror_download():
        dialog.accept()
        getattr(parent, "_start_mirror_download")()

    netdisk_button.clicked.connect(open_netdisk_download)
    mirror_button.clicked.connect(start_mirror_download)
    footer.addWidget(netdisk_button)
    footer.addWidget(mirror_button)
    footer.addWidget(buttons)
    layout.addLayout(footer)
    dialog.exec()
    result = {"changed": False}
    if never_cb.isChecked():
        result["never_remind"] = True
        result["changed"] = True
    if ignore_cb.isChecked():
        result["ignored_version"] = latest
        result["changed"] = True
    return result
