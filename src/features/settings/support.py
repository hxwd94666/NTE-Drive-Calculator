# 展示公共支持入口、群聊与下载网页，不拥有更新任务或安装器。
from __future__ import annotations
import os
from urllib.parse import urlsplit
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from src.app.constants import (BILIBILI_HOME_URL, DISCORD_GROUP_URL, GROUP_CHAT_DEVELOPER_HINT,
    GROUP_CHAT_DISCORD_HINT, GITHUB_HOME_URL, GITHUB_LATEST_RELEASE_URL, QQ_GROUP_NUMBER, SUPPORT_US_URL)
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen

def _open_update_homepage(self):
    self._open_url(GITHUB_LATEST_RELEASE_URL)


def _open_bilibili_homepage(self):
    self._open_url(BILIBILI_HOME_URL)


def _open_project_homepage(self):
    self._open_url(GITHUB_HOME_URL)


def _open_support_homepage(self):
    self._open_url(SUPPORT_US_URL)


def _open_discord_group(self):
    self._open_url(DISCORD_GROUP_URL)


def _show_group_chat_notice(self):
    dialog = QDialog(self)
    dialog.setWindowTitle("加入群聊")
    dialog.setMinimumSize(410, 245)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(11)
    intro = QLabel("遇到问题，或想交流计算器的使用体验？")
    intro.setWordWrap(True)
    intro.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(intro)

    def add_option(title: str, detail: str, action: QPushButton, number: str = ""):
        card = QFrame()
        card.setObjectName("groupChatOption")
        card.setStyleSheet(themed_style(
            "QFrame#groupChatOption{background:#161b22;border:1px solid #30363d;border-radius:8px}"
        ))
        row = QHBoxLayout(card)
        row.setContentsMargins(15, 12, 15, 12)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(5)
        title_label = QLabel(title)
        title_label.setStyleSheet(themed_style("font-size:14px;font-weight:600;color:#f0f6fc"))
        text.addWidget(title_label)
        if number:
            number_label = QLabel(number)
            number_label.setObjectName("groupChatNumber")
            number_label.setStyleSheet(themed_style("font-size:18px;font-weight:600;color:#58a6ff"))
            text.addWidget(number_label)
        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(themed_style("color:#8b949e"))
        text.addWidget(detail_label)
        row.addLayout(text, 1)
        row.addWidget(action, 0, Qt.AlignVCenter)
        layout.addWidget(card)

    copy_button = QPushButton("复制群号")

    def copy_group_number():
        QApplication.clipboard().setText(QQ_GROUP_NUMBER)
        copy_button.setText("已复制")

    copy_button.clicked.connect(copy_group_number)
    add_option("QQ 交流群", GROUP_CHAT_DEVELOPER_HINT, copy_button, QQ_GROUP_NUMBER)

    discord_button = QPushButton("加入 Discord")
    discord_button.setObjectName("btnNew")

    def open_discord_group():
        self._open_discord_group()
        dialog.accept()

    discord_button.clicked.connect(open_discord_group)
    add_option("Discord 群组", GROUP_CHAT_DISCORD_HINT, discord_button)
    fit_dialog_to_available_screen(dialog, QSize(490, 285))
    dialog.exec()
    return dialog


def _show_netdisk_download_dialog(self, links):
    links = tuple((str(name), str(url)) for name, url in links if name and url)
    if not links:
        return
    dialog = QDialog(self)
    dialog.setWindowTitle("网盘下载")
    dialog.setMinimumSize(500, 315)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(10)

    for name, url in links:
        card = QFrame()
        card.setObjectName("netdiskOption")
        card.setStyleSheet(themed_style(
            "QFrame#netdiskOption{background:#161b22;border:1px solid #30363d;border-radius:8px}"
        ))
        row = QHBoxLayout(card)
        row.setContentsMargins(15, 11, 15, 11)
        row.setSpacing(12)
        description = QVBoxLayout()
        description.setSpacing(4)
        title = QLabel(name)
        title.setStyleSheet(themed_style("font-size:15px;font-weight:600;color:#f0f6fc"))
        description.addWidget(title)
        domain = QLabel(urlsplit(url).netloc)
        domain.setStyleSheet(themed_style("color:#8b949e"))
        description.addWidget(domain)
        row.addLayout(description, 1)
        open_button = QPushButton("打开网盘")
        open_button.setAccessibleName(f"打开{name}")
        open_button.setStyleSheet(themed_style(
            "QPushButton{background:#161b22;border:1px solid #58a6ff;color:#58a6ff;"
            "border-radius:6px;padding:7px 14px}"
            "QPushButton:hover{background:#30363d}"
        ))

        def open_netdisk(_checked=False, *, target_url=url):
            dialog.accept()
            self._open_url(target_url)

        open_button.clicked.connect(open_netdisk)
        row.addWidget(open_button, 0, Qt.AlignVCenter)
        layout.addWidget(card)

    fit_dialog_to_available_screen(dialog, QSize(650, 390))
    dialog.exec()
    return dialog


def _open_url(self, url):
    try:
        os.startfile(url)
    except Exception:
        import webbrowser
        webbrowser.open(url)


class SupportActionsMixin:
    _open_update_homepage = _open_update_homepage
    _open_bilibili_homepage = _open_bilibili_homepage
    _open_project_homepage = _open_project_homepage
    _open_support_homepage = _open_support_homepage
    _open_discord_group = _open_discord_group
    _show_group_chat_notice = _show_group_chat_notice
    _show_netdisk_download_dialog = _show_netdisk_download_dialog
    _open_url = _open_url
