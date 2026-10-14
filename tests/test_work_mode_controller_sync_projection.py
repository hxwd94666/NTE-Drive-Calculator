# 验证工作模式观察结果传递及设置卡的展示边界。
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

pytest_plugins = ("tests.work_modes.test_controller",)

def test_native_saved_inventory_does_not_mark_packet_capture_ready(controller):
    c, window, policy, _events, _popups, probe = controller
    window._inventory_sync_service = SimpleNamespace(
        is_running=True, state=SimpleNamespace(capture_source="native", capturing=True,
                                              source_snapshot_ready=True, error=None),
    )
    seen = []
    original = policy.build_report
    policy.build_report = lambda value: seen.append(value) or original(value)
    c._apply((policy.settings.revision, 1, probe, 0))
    assert not seen[0].packet_snapshot and not seen[0].packet_listening
    assert window.observed_sync_probes == seen
    window._inventory_sync_service = None


def test_native_connection_gaps_are_forwarded_to_automatic_sync_owner(controller):
    from dataclasses import replace
    from src.domain.work_mode import NativeFeatureProbe
    c, window, policy, events, popups, probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    ready = replace(probe, native_inventory=NativeFeatureProbe(handshake=True))
    c._apply((policy.settings.revision, 1, ready, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    unavailable = replace(ready, game_running=False, native_inventory=NativeFeatureProbe(handshake=False))
    c._apply((policy.settings.revision, 1, unavailable, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    assert window.observed_sync_probes == [ready, ready, unavailable, ready]
    policy.set_paused(True)
    c._apply((policy.settings.revision, 1, unavailable, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    assert window.observed_sync_probes == [ready, ready, unavailable, ready, unavailable, ready]
    assert "packet_start" not in events and popups == []


def test_settings_card_keeps_only_compact_status_and_explicit_details(controller):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QVBoxLayout
    from src.features.settings.work_mode_card import build_work_mode_card, report_text
    c, window, policy, _events, popups, probe = controller
    policy.select_mode("developer", risk_confirmed=True)
    window.work_mode_controller, window.work_mode_service = c, policy

    def make_card(_title):
        card = QWidget(window)
        QVBoxLayout(card)
        return card
    window._card = make_card
    card = build_work_mode_card(window)
    combos = card.findChildren(QComboBox)
    assert len(combos) == 1
    assert card.findChildren(QCheckBox) == []
    assert all("开发采集来源" not in label.text() for label in card.findChildren(QLabel))
    combo, status, check = c._controls
    assert status.isHidden()
    labels = {label.text() for label in card.findChildren(QLabel)}
    assert {
        "离线：", "低风险：", "中风险：", "开发：",
        "本地计算、配装、已保存数据与历史战报分析。",
        "以上功能 + 基础同步/抓包 + 鼠标或手柄扫描；不使用游戏组件。",
        "以上功能 + 原生同步、原生战报、极速装配、锁定/弃置及插件。",
        "抓包与原生双线对比，仅供开发人员使用。",
    } <= labels
    low_label = card.findChild(QLabel, "workModeDescription_low")
    developer_label = card.findChild(QLabel, "workModeDescription_developer")
    assert developer_label.property("confirmedMode") is True
    combo.setCurrentIndex(combo.findData("low"))
    assert policy.settings.mode.value == "developer"
    assert developer_label.property("confirmedMode") is True
    assert low_label.property("confirmedMode") is False
    c._apply((policy.settings.revision, 1, probe, 0))
    assert "\n" not in status.text()
    assert status.text() != report_text(policy.build_report(probe))
    assert popups == [] and check.text() == "检测并处理"
    check.click()
    request = c._show_request_id
    c._apply((policy.settings.revision, 1, probe, request))
    assert popups == ["report"]
    policy.set_paused(True)
    c.refresh_controls()
    assert all(button.text() not in {"暂停自动管理", "继续自动管理", "暂停", "继续"}
               for button in card.findChildren(QPushButton))
    card.close()


def test_upgrade_unselected_mode_clears_emphasis_without_changing_confirmed_mode(controller, monkeypatch):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QLabel, QVBoxLayout
    from src.features.settings.work_mode_card import build_work_mode_card, MODE_LABELS
    from src.ui.controllers import work_mode_controller

    c, window, policy, _events, _popups, _probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    window.work_mode_controller, window.work_mode_service = c, policy

    def make_card(_title):
        card = QWidget(window)
        QVBoxLayout(card)
        return card

    window._card = make_card
    card = build_work_mode_card(window)
    labels = {key: card.findChild(QLabel, f"workModeDescription_{key}") for key in MODE_LABELS}
    assert labels["medium"].property("confirmedMode") is True
    policy.set_paused(True)
    frozen = policy.settings
    c.open_settings = Mock()
    c._upgrade_action("mode")
    combo = c._controls[0]
    assert combo.currentIndex() == -1
    assert all(label.property("confirmedMode") is False for label in labels.values())
    assert policy.settings == frozen
    c.refresh_controls()
    assert combo.currentIndex() == -1
    assert all(label.property("confirmedMode") is False for label in labels.values())

    # Cancelling a real selection restores the confirmed mode and its emphasis.
    monkeypatch.setattr(work_mode_controller, "confirm_mode", lambda *_args: False)
    combo.setCurrentIndex(combo.findData("medium"))
    combo.activated.emit(combo.currentIndex())
    assert labels["medium"].property("confirmedMode") is True
    assert all(not label.property("confirmedMode") for key, label in labels.items() if key != "medium")
    assert policy.settings == frozen
    card.close()
