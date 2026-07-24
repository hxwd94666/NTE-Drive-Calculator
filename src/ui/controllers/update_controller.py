# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from src.app.constants import (
    APP_VERSION,
    BILIBILI_HOME_URL,
    GITHUB_HOME_URL,
    GITHUB_LATEST_RELEASE_API,
    GITHUB_RELEASES_URL,
    QUARK_NETDISK_URL,
)
from src.app.workers import WorkerThread
from src.features.settings.updates import (
    fetch_update_info,
    is_newer_version,
    should_show_startup_update,
    show_update_dialog,
)
from src.ui.main_window_method_install import install_methods as _install_main_window_methods
from src.utils.logger import logger

_METHOD_NAMES = ["_maybe_check_updates_on_startup","_check_updates","_fetch_update_info","_on_update_checked","_on_update_error","_should_show_startup_update","_show_update_dialog","_show_update_failure_netdisk_prompt","_open_update_homepage","_open_bilibili_homepage","_show_netdisk_download_dialog","_open_url","_is_newer_version"]


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _maybe_check_updates_on_startup(self):
    if self._update_config.get("never_remind"):
        return
    QTimer.singleShot(1200, lambda: self._check_updates(manual=False))

def _check_updates(self, manual=True):
    if hasattr(self,"_update_worker") and self._update_worker.isRunning():
        if manual:
            self._update_status.setText("正在检查更新...")
        return
    self._update_check_manual=manual
    if manual:
        self._check_update_btn.setEnabled(False)
        self._update_status.setText("正在检查更新...")
    self._update_worker=WorkerThread(target=self._fetch_update_info,parent=self)
    self._update_worker.result_ready.connect(self._on_update_checked)
    self._update_worker.error.connect(self._on_update_error)
    self._update_worker.start()

def _fetch_update_info(self):
    return fetch_update_info(
        GITHUB_LATEST_RELEASE_API,
        GITHUB_RELEASES_URL,
        APP_VERSION,
    )

def _on_update_checked(self,info):
    manual=getattr(self,"_update_check_manual",True)
    if manual:
        self._check_update_btn.setEnabled(True)
    if not info.get("has_release"):
        if info.get("error"):
            self._update_status.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
            if manual:
                self._show_update_failure_netdisk_prompt(info.get("error", ""))
            return
        self._update_status.setText(f"当前版本: {APP_VERSION}。{info.get('message','')}")
        if manual:
            QMessageBox.information(self,"检查更新","当前仓库还没有发布 Release。")
        return

    latest=info.get("latest") or "未知"
    if info.get("newer"):
        self._update_status.setText(f"发现新版本: {latest}（当前 {APP_VERSION}）")
        if manual or self._should_show_startup_update(info):
            self._show_update_dialog(info, manual=manual)
    else:
        self._update_status.setText(f"当前已是最新版本: {APP_VERSION}")
        if manual:
            QMessageBox.information(self,"检查更新",f"当前已是最新版本。\n当前版本: {APP_VERSION}\n最新版本: {latest}")

def _on_update_error(self,err):
    manual=getattr(self,"_update_check_manual",True)
    if manual:
        self._check_update_btn.setEnabled(True)
        self._update_status.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
        self._show_update_failure_netdisk_prompt(err)
        return
    else:
        if hasattr(self, "_update_status"):
            self._update_status.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
        logger.warning(f"启动自动检查更新失败: {err}")

def _should_show_startup_update(self, info):
    return should_show_startup_update(self._update_config,info)

def _show_update_dialog(self, info, manual=False):
    result=show_update_dialog(self,self._current_style_sheet(),info,APP_VERSION)
    if result.get("never_remind"):
        self._update_config["never_remind"]=True
    if result.get("ignored_version"):
        self._update_config["ignored_version"]=result["ignored_version"]
    if result.get("changed"):
        self._save_update_config()

def _show_update_failure_netdisk_prompt(self, detail=""):
    box=QMessageBox(self)
    box.setWindowTitle("检查更新失败")
    box.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
    if detail:
        box.setInformativeText(str(detail))
    go_btn=box.addButton("前往", QMessageBox.AcceptRole)
    box.addButton("取消", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is go_btn:
        self._open_url(QUARK_NETDISK_URL)

def _open_update_homepage(self):
    self._open_url(GITHUB_HOME_URL)

def _open_bilibili_homepage(self):
    self._open_url(BILIBILI_HOME_URL)

def _show_netdisk_download_dialog(self, links):
    links=tuple((str(name),str(url)) for name,url in links if name and url)
    if not links:
        return
    box=QMessageBox(self)
    box.setWindowTitle("网盘下载")
    box.setText("请选择下载网盘")
    box.setInformativeText("\n\n".join(f"{name}：\n{url}" for name,url in links))
    box.setMinimumSize(620, 300)
    box.setStyleSheet(box.styleSheet()+"\nQLabel{min-width:560px;}")
    buttons=[]
    for name,url in links:
        button=box.addButton(f"打开{name}", QMessageBox.AcceptRole)
        buttons.append((button,url))
    box.addButton("取消", QMessageBox.RejectRole)
    box.exec()
    clicked=box.clickedButton()
    for button,url in buttons:
        if clicked is button:
            self._open_url(url)
            break

def _open_url(self,url):
    try:
        os.startfile(url)
    except Exception:
        import webbrowser
        webbrowser.open(url)

def _is_newer_version(self,remote,current):
    return is_newer_version(remote,current)

