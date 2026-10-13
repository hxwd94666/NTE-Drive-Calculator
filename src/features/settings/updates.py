# 检查 Mirror 酱资源更新并显示版本更新弹窗。
"""更新请求、临时下载和弹窗适配；检查与用户显式下载分别执行。"""

from __future__ import annotations

import json
from html import escape
import os
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.version import version_order_key
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen

UPDATE_FAILURE_MESSAGE = "Mirror 酱更新服务请求失败，请稍后重试。"
UPDATE_CHECK_TIMEOUT_SECONDS = 5
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 30
_UPDATE_DOWNLOAD_CHUNK_SIZE = 128 * 1024


class UpdateDownloadCancelled(RuntimeError):
    """用户取消本次安装器下载，保留为独立终态。"""


def is_newer_version(remote, current) -> bool:
    return version_order_key(remote) > version_order_key(current)


def mirror_update_request_url(api_url: str, app_version: str, cdk: str = "") -> str:
    """构造本次请求；CDK 只作为参数传递，不在此记录日志。"""
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
    """读取本次更新响应；短期下载地址仅返回给显式下载流程，不写配置或日志。"""
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
    """下载到本次临时目录，MZ 与已知长度通过后原子改名；失败/取消清除残件。"""
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


def update_notes_markdown(message: object) -> str:
    """只规范更新说明的 Markdown 显示，不解释为可执行动作。"""

    text = str(message or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "此版本没有填写更新说明。"
    normalized_lines: list[str] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        # GitHub users often write ``1.修复内容``.  GitHub renders it
        # leniently, while Qt requires a space after the marker to create a
        # separate list item instead of one long paragraph.
        line = re.sub(r"^(\s*\d+)\.(?=\S)", r"\1. ", line)
        line = re.sub(r"^(\s*[-*+])(?=\S)", r"\1 ", line)
        # Qt merges adjacent quote lines into one paragraph.  Keep each
        # support/download call-to-action visibly separate.
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if line.lstrip().startswith(">") and next_line.lstrip().startswith(">"):
            line += "  "
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


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
    dialog.setMinimumSize(620, 480)
    dialog.setStyleSheet(style_sheet)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)
    header = QHBoxLayout()
    header.setSpacing(12)
    heading = QVBoxLayout()
    heading.setSpacing(6)
    title = QLabel(f"发现新版本 {latest}")
    title.setStyleSheet(themed_style("font-size:21px;font-weight:600;color:#f0f6fc"))
    heading.addWidget(title)
    subtitle = QLabel(f"当前版本 {app_version}  →  最新版本 {latest}")
    subtitle.setStyleSheet(themed_style("color:#8b949e"))
    heading.addWidget(subtitle)
    header.addLayout(heading)
    header.addStretch()
    release_url = str(info.get("release_url") or "").strip()
    if release_url.lower().startswith(("https://", "http://")):
        link = QLabel(
            f'<a href="{escape(release_url, quote=True)}">查看 GitHub 发布页 ↗</a>'
        )
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setStyleSheet(themed_style("color:#58a6ff"))
        header.addWidget(link, 0, Qt.AlignTop)
    layout.addLayout(header)

    notes_card = QFrame()
    notes_card.setObjectName("updateNotesCard")
    notes_card.setStyleSheet(themed_style(
        "QFrame#updateNotesCard{background:#161b22;border:1px solid #30363d;border-radius:8px}"
    ))
    notes_layout = QVBoxLayout(notes_card)
    notes_layout.setContentsMargins(18, 14, 18, 14)
    notes_layout.setSpacing(8)
    notes_heading = QLabel("更新内容")
    notes_heading.setStyleSheet(themed_style("font-size:15px;font-weight:600;color:#f0f6fc"))
    notes_layout.addWidget(notes_heading)
    notes = QTextBrowser()
    notes.setObjectName("updateReleaseNotes")
    notes.setReadOnly(True)
    notes.setMinimumHeight(180)
    notes.setFrameShape(QFrame.NoFrame)
    notes.setStyleSheet(themed_style("background:#161b22;border:none;color:#c9d1d9"))
    notes.document().setDocumentMargin(2)
    notes.setOpenExternalLinks(True)
    notes.setTextInteractionFlags(Qt.TextBrowserInteraction)
    notes.setMarkdown(update_notes_markdown(info.get("message")))
    notes_layout.addWidget(notes, 1)
    layout.addWidget(notes_card, 1)

    preferences = QHBoxLayout()
    preferences.setSpacing(24)
    ignore_cb = QCheckBox("此版本不再提醒")
    never_cb = QCheckBox("关闭自动更新提醒")
    preferences.addWidget(ignore_cb)
    preferences.addWidget(never_cb)
    preferences.addStretch()
    layout.addLayout(preferences)

    separator = QFrame()
    separator.setFrameShape(QFrame.HLine)
    separator.setStyleSheet(themed_style("color:#30363d"))
    layout.addWidget(separator)
    footer = QHBoxLayout()
    footer.setSpacing(10)
    group_button = QPushButton("加入群聊")
    group_button.setObjectName("updateGroupChatButton")
    group_button.setStyleSheet(themed_style(
        "QPushButton#updateGroupChatButton{background:transparent;border:none;color:#8b949e;padding:0 4px}"
        "QPushButton#updateGroupChatButton:hover{color:#58a6ff}"
    ))
    group_button.clicked.connect(getattr(parent, "_show_group_chat_notice"))
    footer.addWidget(group_button)
    footer.addStretch()
    later_button = QPushButton("暂不更新")
    later_button.clicked.connect(dialog.accept)
    later_button.setDefault(True)
    netdisk_button = QPushButton("网盘下载")
    netdisk_button.setObjectName("btnNew")
    mirror_button = QPushButton("Mirror 下载")

    def open_netdisk_download():
        dialog.accept()
        getattr(parent, "_show_netdisk_download_dialog")(NETDISK_DOWNLOAD_LINKS)

    def start_mirror_download():
        dialog.accept()
        getattr(parent, "_start_mirror_download")()

    netdisk_button.clicked.connect(open_netdisk_download)
    mirror_button.clicked.connect(start_mirror_download)
    footer.addWidget(later_button)
    footer.addWidget(mirror_button)
    footer.addWidget(netdisk_button)
    layout.addLayout(footer)
    fitted = fit_dialog_to_available_screen(dialog, QSize(780, 600))
    notes.setMinimumHeight(min(300, max(140, fitted.height() - 285)))
    dialog.exec()
    result = {"changed": False}
    if never_cb.isChecked():
        result["never_remind"] = True
        result["changed"] = True
    if ignore_cb.isChecked():
        result["ignored_version"] = latest
        result["changed"] = True
    return result
