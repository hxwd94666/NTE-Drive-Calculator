# 便携更新入口只展示对应渠道说明，任何入口都不下载或派发安装器。
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QMainWindow, QPushButton

from auto_sync_ui_fixture import application
from src.features.settings.updates import controller as ui


@pytest.mark.parametrize("channel", ["manual", "platform"])
def test_portable_public_and_late_entrypoints_never_start_installer(channel, monkeypatch):
    application()
    window = QMainWindow()
    calls = []
    control = ui.CalcUpdateController(identity_provider=lambda: ("fixture", 1), load_settings=lambda: {"mirror_cdk": "fixture"},
        save_settings=lambda _v: calls.append("save"), style_sheet=lambda: "", show_group=lambda: None,
        show_netdisk=lambda _v: None, request_close=lambda: calls.append("close"), parent=window,
        update_channel=channel)
    widgets = QLabel(), QPushButton(), QPushButton(), QLineEdit(), QPushButton(), QPushButton()
    control.bind_view(status=widgets[0], check_button=widgets[1], download_button=widgets[2], cdk_editor=widgets[3])
    control.configure_distribution_links(homepage_button=widgets[4], netdisk_button=widgets[5])
    for method in ("fetch_update_info", "download_update_installer", "launch_installer"):
        monkeypatch.setattr(ui, method, lambda *_a, **_k: pytest.fail("installer side effect"))
    monkeypatch.setattr(control, "_spawn", lambda *_a: pytest.fail("portable spawned update worker"))
    discarded = []
    monkeypatch.setattr(ui, "discard_installer", discarded.append)
    try:
        control.startup()
        control.check(manual=True)
        control.start_download()
        assert not control._startup_timer.isActive()
        assert not control.is_running()
        assert "Mirror 当前仅提供安装器" in widgets[0].text()
        assert ("平台" if channel == "platform" else "便携 ZIP") in widgets[0].text()
        assert widgets[1].text() == "更新说明"
        assert not widgets[2].isEnabled() and not widgets[3].isEnabled()
        assert not widgets[5].isEnabled()
        assert widgets[4].isEnabled() == (channel == "manual")
        assert control.save_cdk() == "" and not calls
        request = control._download = ui._Request(("fixture", 1), "download")
        control._address_ready(request, {"latest": "99.0.0", "url": "https://example.invalid/setup.exe"})
        assert control._download is None
        receipt = SimpleNamespace()
        control._download = request
        control._pending_launch = request, receipt
        control._dispatch()
        assert discarded == [receipt] and not request.dispatched
        control.rebuild_account()
        assert not widgets[2].isEnabled()
    finally:
        control.begin_close()
        window.close()
        window.deleteLater()
        for widget in widgets:
            widget.deleteLater()
        application().processEvents()
