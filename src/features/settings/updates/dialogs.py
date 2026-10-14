# 展示更新说明、旧版确认和下载提示，所有动作由注入的 owner 回调决定。
from __future__ import annotations
from html import escape
import re
from typing import Any
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout)
from src.app.constants import NETDISK_DOWNLOAD_LINKS, MIRROR_PROJECT_URL
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen

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


def show_update_dialog(parent, style_sheet: str, info: dict, app_version: str, *,
                       show_group, show_netdisk, start_download) -> dict:
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
    group_button.clicked.connect(show_group)
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
        show_netdisk(NETDISK_DOWNLOAD_LINKS)

    def request_mirror_download():
        dialog.accept()
        start_download()

    netdisk_button.clicked.connect(open_netdisk_download)
    mirror_button.clicked.connect(request_mirror_download)
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

def _show_mirror_cdk_required_dialog(self):
    dialog = QDialog(self)
    dialog.setWindowTitle("Mirror 下载")
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel("请先填写 Mirror CDK 后再下载。<br><br>" + _mirror_project_link_text("获取 CDK"))
    message.setWordWrap(True)
    message.setTextFormat(Qt.TextFormat.RichText)
    message.setOpenExternalLinks(True)
    message.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction
    )
    layout.addWidget(message)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _mirror_project_link_text(action: str) -> str:
    """复用公共项目页链接，只提供浏览器入口，不自动执行安装。"""
    return (
        f"可前往 Mirror 项目页面{action}："
        f'<a href="{MIRROR_PROJECT_URL}">{MIRROR_PROJECT_URL}</a>'
    )


def _confirm_mirror_older_version_download(self: Any, current: str, latest: str) -> bool:
    dialog = QMessageBox(self)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("Mirror 下载较旧版本")
    dialog.setText("Mirror 提供的版本低于当前安装版本。")
    dialog.setInformativeText(
        f"当前版本：{current}\nMirror 版本：{latest}\n"
        "下载完成后会自动启动安装程序，可能覆盖当前版本。是否继续下载？"
    )
    download = dialog.addButton("下载", QMessageBox.ButtonRole.AcceptRole)
    cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    fit_dialog_to_available_screen(dialog, QSize(490, 210))
    QApplication.beep()
    dialog.exec()
    return dialog.clickedButton() is download


def _show_mirror_project_download_dialog(self: Any, summary: str) -> None:
    """下载失败保留可重试状态，并提供显式项目页入口。"""
    dialog = QDialog(self)
    dialog.setWindowTitle("Mirror 下载")
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel(summary)
    message.setWordWrap(True)
    layout.addWidget(message)
    link = QLabel(_mirror_project_link_text("尝试下载"))
    link.setWordWrap(True)
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    layout.addWidget(link)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


