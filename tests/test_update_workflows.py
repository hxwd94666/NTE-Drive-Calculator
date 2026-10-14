# 覆盖更新传输及可见选择，不固化像素、装饰或重复公共链接字面值。
from __future__ import annotations

from io import BytesIO
import json
from threading import Event
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QMessageBox, QPushButton, QWidget

from auto_sync_ui_fixture import application
from src.features.settings import support
from src.features.settings.updates import dialogs
from src.integrations.updates import mirror


class Response(BytesIO):
    def __init__(self, body=b"MZtestok", length="8"):
        super().__init__(body)
        self.headers = {} if length is None else {"Content-Length": length}


@pytest.fixture
def qt():
    return application()


def test_version_response_freezes_request_and_omits_raw_network_errors(monkeypatch):
    seen = []
    payload = {"code": 0, "data": {"version_name": "v2.1.0", "url": "https://synthetic.invalid/package",
                                   "release_note": "changes"}}
    def response(request, timeout):
        seen.append((request.full_url, timeout))
        return Response(json.dumps(payload).encode(), None)
    monkeypatch.setattr(mirror, "_open", response)
    info = mirror.fetch_update_info("https://synthetic.invalid/latest", "2.0.0", cdk="fixture cdk")
    assert info["newer"] and info["latest"] == "v2.1.0" and info["message"] == "changes"
    assert "cdk=fixture+cdk" in seen[0][0] and seen[0][1] <= 5
    mirror.fetch_update_info("https://synthetic.invalid/latest", "2.0.0")
    assert "cdk=" not in seen[1][0]
    monkeypatch.setattr(mirror, "_open", lambda *_args: (_ for _ in ()).throw(
        OSError("https://synthetic.invalid/?token=fixture-secret")))
    failure = mirror.fetch_update_info("https://synthetic.invalid/latest", "2.0.0")
    assert not failure["has_release"] and not failure["newer"] and failure["url"] == ""
    assert "fixture-secret" not in repr(failure) and "OSError" in failure["error"]


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{"code":4001,"msg":"CDK fixture-secret"}',
                                      b'{"code":0,"data":{}}'])
def test_invalid_update_metadata_does_not_become_latest(monkeypatch, payload):
    monkeypatch.setattr(mirror, "_open", lambda *_args: Response(payload, None))
    result = mirror.fetch_update_info("https://synthetic.invalid/latest", "2.0.0")
    assert not result["has_release"] and not result["newer"] and not result["url"]
    assert "fixture-secret" not in repr(result)


@pytest.mark.parametrize("case", ["valid", "unknown_length", "bad_length", "oversized", "non_exe", "cancel_eof"])
def test_download_publication_and_old_files_are_isolated(tmp_path, monkeypatch, case):
    old = tmp_path / "NTE-Drive-Calc-update.exe"
    old.write_bytes(b"old installer retained")
    cancel = Event()
    class FinalResponse(Response):
        def read1(self, size):
            block = super().read1(size)
            if not block and case == "cancel_eof":
                cancel.set()
            return block
    response = FinalResponse(b"not an exe" if case == "non_exe" else b"MZtestok",
        None if case == "unknown_length" else "12" if case == "bad_length"
        else str(mirror._MAX_INSTALLER_BYTES + 1) if case == "oversized" else "8")
    monkeypatch.setattr(mirror, "_open", lambda *_args: response)
    progress = []
    if case in {"valid", "unknown_length"}:
        receipt = mirror.download_update_installer("https://synthetic.invalid/setup", destination_dir=tmp_path,
            progress_callback=lambda *values: progress.append(values), cancel_check=cancel.is_set)
        assert mirror.validate_installer(receipt).read_bytes() == b"MZtestok"
        assert progress[-1][0] == receipt.downloaded == 8
        mirror.discard_installer(receipt)
    else:
        with pytest.raises((ValueError, mirror.UpdateDownloadCancelled)):
            mirror.download_update_installer("https://synthetic.invalid/setup", destination_dir=tmp_path,
                                            cancel_check=cancel.is_set)
    assert old.read_bytes() == b"old installer retained"
    assert list(tmp_path.iterdir()) == [old]


@pytest.mark.parametrize("change", ["replace", "in_place", "cancel", "success"])
def test_receipt_revalidation_precedes_os_dispatch(tmp_path, monkeypatch, change):
    monkeypatch.setattr(mirror, "_open", lambda *_args: Response())
    receipt = mirror.download_update_installer("https://synthetic.invalid/setup", destination_dir=tmp_path)
    if change == "replace":
        replacement = receipt.path.with_suffix(".tmp")
        replacement.write_bytes(b"MZchanged")
        replacement.replace(receipt.path)
    elif change == "in_place":
        receipt.path.write_bytes(b"MZchanged")
    calls = []
    monkeypatch.setattr(mirror.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    if change == "success":
        mirror.launch_installer(receipt)
        assert len(calls) == 1
        mirror.discard_installer(receipt)
    else:
        with pytest.raises((ValueError, mirror.UpdateDownloadCancelled)):
            mirror.launch_installer(receipt, cancel_check=lambda: change == "cancel")
        assert not calls
        mirror.discard_installer(receipt)
        if change in {"replace", "in_place"}:
            assert receipt.path.read_bytes() == b"MZchanged"


def test_https_redirect_rejects_downgrade_before_request():
    request = mirror.urllib.request.Request("https://synthetic.invalid/start")
    with pytest.raises(ValueError):
        mirror._HttpsRedirect().redirect_request(request, None, 302, "", {}, "http://synthetic.invalid/end")


def test_update_dialog_actions_and_preferences(qt):
    owner = QWidget()
    actions = []
    captured = []
    def choose(dialog):
        captured.append(dialog)
        for box in dialog.findChildren(QCheckBox):
            box.setChecked(True)
        for button in dialog.findChildren(QPushButton):
            if button.text() in {"加入群聊", "网盘下载", "Mirror 下载"}:
                button.click()
        return 0
    with patch.object(QDialog, "exec", choose):
        result = dialogs.show_update_dialog(owner, "", {"latest": "2.9.0", "message": "-changes"}, "2.0.0",
            show_group=lambda: actions.append("group"), show_netdisk=lambda _links: actions.append("netdisk"),
            start_download=lambda: actions.append("download"))
    assert actions == ["group", "download", "netdisk"]
    assert result == {"changed": True, "never_remind": True, "ignored_version": "2.9.0"}
    for dialog in captured:
        dialog.deleteLater()
    owner.deleteLater()


def test_older_version_requires_explicit_download_and_defaults_cancel(qt):
    owner = QWidget()
    for selected in ("取消", "下载"):
        def choose(dialog):
            buttons = {button.text(): button for button in dialog.buttons()}
            assert dialog.defaultButton() is buttons["取消"] and dialog.escapeButton() is buttons["取消"]
            assert "2.3.0" in dialog.informativeText() and "2.2.0" in dialog.informativeText()
            buttons[selected].click()
        with patch.object(QMessageBox, "exec", choose), patch.object(QApplication, "beep"):
            assert dialogs._confirm_mirror_older_version_download(owner, "2.3.0", "2.2.0") == (selected == "下载")
    owner.deleteLater()


def test_support_links_keep_explicit_user_actions(qt):
    opened = []
    class Host(QWidget):
        _current_style_sheet = staticmethod(lambda: "")
        _open_url = staticmethod(opened.append)
        _open_discord_group = staticmethod(lambda: opened.append("discord"))
    host = Host()
    with patch.object(QDialog, "exec", return_value=0):
        dialog = support._show_netdisk_download_dialog(host, (("channel", "https://synthetic.invalid/page"),))
        group = support._show_group_chat_notice(host)
    assert opened == []
    dialog.findChild(QPushButton).click()
    next(b for b in group.findChildren(QPushButton) if b.text() == "加入 Discord").click()
    assert opened == ["https://synthetic.invalid/page", "discord"]
    assert any(label.text() == "channel" for label in dialog.findChildren(QLabel))
    dialog.deleteLater(); group.deleteLater(); host.deleteLater()
