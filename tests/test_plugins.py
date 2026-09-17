# 验证插件偏好、风险模式和共享原生会话的独立生命周期。
from types import SimpleNamespace

import pytest

from src.domain.plugin_settings import PluginSettings
from src.integrations.plugin_settings_store import PluginSettingsStore
from src.services.plugin_service import PluginService
from src.services.native_game_session import NativeGameSession
from tests.test_native_game_session import FakeNativeCore


class Policy:
    def __init__(self):
        self.enabled = True
        self.settings = SimpleNamespace(paused=False)

    def allowed(self, _):
        return self.enabled


def make_service(tmp_path):
    policy = Policy()
    core = FakeNativeCore()
    core.hello_result["capabilities"].append("native_hud_v1")
    session = NativeGameSession(factory=lambda: core, guard=lambda _: None)
    service = PluginService(store=PluginSettingsStore(tmp_path / "plugins.json"), policy=policy, session=session)
    return service, session, core, policy


def test_defaults_do_not_start_native_client(tmp_path):
    service, _, core, _ = make_service(tmp_path)
    service.observe(SimpleNamespace(game_running=True))
    assert not core.is_running
    assert not core.calls


def test_global_preferences_and_independent_switches(tmp_path):
    service, _, _, _ = make_service(tmp_path)
    service.update(cooldown=True, enemy_bars=True, hp=False)
    service.update(cooldown=False)
    saved = service.store.load()
    assert saved == PluginSettings(cooldown=False, enemy_bars=True, hp=False)
    service.observe(SimpleNamespace(game_running=False))
    assert service.status == "等待游戏"


def test_plugin_apply_status_only_changes_the_affected_card(tmp_path):
    service, _, _, _ = make_service(tmp_path)
    service.update(cooldown=True, enemy_bars=True)
    service.observe(SimpleNamespace(game_running=False))
    assert service.status_for("cooldown") == "等待游戏"
    assert service.status_for("enemy_bars") == "等待游戏"
    service.update(hp=False)
    assert service.status_for("cooldown") == "等待游戏"
    assert service.status_for("enemy_bars") == "设置已保存，等待应用"


def test_low_mode_disables_live_hud_and_persists_switches_without_losing_options(tmp_path):
    service, _, core, policy = make_service(tmp_path)
    service.update(cooldown=True, enemy_bars=True, ready_cue=False, hp=False)
    service.observe(SimpleNamespace(game_running=True))
    assert core.calls[-1][1]["cooldown"]
    policy.enabled = False
    service.observe(SimpleNamespace(game_running=True))
    assert not core.calls[-1][1]["cooldown"]
    assert service.settings == PluginSettings(ready_cue=False, hp=False)
    assert service.store.load() == service.settings
    with pytest.raises(PermissionError):
        service.update(enemy_bars=True)
    policy.enabled = True
    service.observe(SimpleNamespace(game_running=True))
    assert not core.calls[-1][1]["cooldown"]
    assert not core.calls[-1][1]["enemy_bars"]


def test_pause_does_not_clear_saved_plugin_switches(tmp_path):
    service, _, core, policy = make_service(tmp_path)
    service.update(cooldown=True)
    service.observe(SimpleNamespace(game_running=True))
    policy.settings.paused = True
    service.observe(SimpleNamespace(game_running=True))
    assert not core.calls[-1][1]["cooldown"]
    assert service.store.load().cooldown


@pytest.mark.parametrize('mode', ['offline', 'low'])
def test_downgrade_disables_even_enemy_bars_with_no_selected_content(tmp_path, mode):
    from src.services.work_mode_service import WorkModeService
    service, _, core, _ = make_service(tmp_path)
    policy = WorkModeService(tmp_path / 'mode.json')
    policy.select_mode('medium', risk_confirmed=True)
    service.policy = policy
    service.update(enemy_bars=True, hp=False, unbalance=False)
    policy.select_mode(mode, risk_confirmed=mode == 'low')
    service.apply_mode_policy()
    assert service.store.load() == PluginSettings(hp=False, unbalance=False)
    assert not core.calls


@pytest.mark.parametrize('mode', ['offline', 'low', 'medium', 'developer'])
def test_plugins_page_is_viewable_in_every_mode(tmp_path, monkeypatch, mode):
    from unittest.mock import Mock
    from src.services.work_mode_service import WorkModeService
    from src.ui import operation_guidance
    from src.ui.main_window_navigation_mixin import MainWindowNavigationMixin
    from src.ui.navigation import nav_index_map
    policy = WorkModeService(tmp_path / 'mode.json')
    policy.select_mode(mode, risk_confirmed=mode != 'offline')
    prompts = []
    monkeypatch.setattr(operation_guidance, 'prompt_operation_settings',
                        lambda *args, **kwargs: prompts.append(kwargs))
    window = SimpleNamespace(
        stack=SimpleNamespace(currentIndex=lambda: 0, setCurrentIndex=Mock()),
        _nav_key_for_index=lambda _: 'home', topbar_title=Mock(), topbar_catalog_return=Mock(),
        topbar_source_label=Mock(), topbar_equipment_modes=Mock(), _nav_buttons={},
        _refresh_navigation_item=Mock(),
    )
    window.operation_entry = lambda cap, feature: operation_guidance.allow_operation_entry(
        None, policy, cap, feature, lambda _: None)
    MainWindowNavigationMixin._go(window, 'plugins')
    assert not prompts
    window.stack.setCurrentIndex.assert_called_once_with(nav_index_map()['plugins'])


def test_display_changes_reuse_capture_client_without_start_or_stop(tmp_path):
    service, session, core, _ = make_service(tmp_path)
    service.update(cooldown=True)
    service.observe(SimpleNamespace(game_running=True))
    service.update(enemy_bars=True)
    service.observe(SimpleNamespace(game_running=True))
    assert all(method == "native.hud.configure" for method, _ in core.calls)
    session.close()
    disabled = [params for method, params in core.calls if method == "native.hud.configure"][-1]
    assert not any(disabled.values())
    assert core.closed


def test_unsupported_components_show_update_reason(tmp_path):
    service, _, core, _ = make_service(tmp_path)
    core.hello_result["capabilities"].remove("native_hud_v1")
    service.update(cooldown=True)
    service.observe(SimpleNamespace(game_running=True))
    assert "更新配套" in service.status
    assert not core.calls


def test_plugin_cards_remain_editable_across_modes_and_themes(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.app.theme import apply_app_theme
    from src.features.plugins.page import PluginsPage
    app = QApplication.instance() or QApplication([])
    service, _, _, policy = make_service(tmp_path)
    applied = []
    routes = []
    page = PluginsPage(
        service=service, request_apply=lambda: applied.append(True),
        open_settings=routes.append,
    )
    page.resize(820, 540)
    for theme in ("black", "dark", "light"):
        apply_app_theme(app, theme)
        page.show()
        app.processEvents()
        assert page.cards["cooldown"][0].isEnabled()
        assert page.grab().width() == 820
    page.cards["cooldown"][0].click()
    assert service.settings.cooldown and applied
    policy.enabled = False
    page.refresh()
    assert page.cards["cooldown"][0].isEnabled()  # Can still turn the saved selection off.
    assert not page.cards["enemy_bars"][0].isEnabled()
    page.environment_button.click()
    assert routes == ["mode"]
    page.close()


def test_plugin_page_uses_inline_options_and_clear_status_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    from src.features.plugins.page import PluginsPage
    app = QApplication.instance() or QApplication([])
    service, _, _, _ = make_service(tmp_path)
    service.update(cooldown=True, enemy_bars=True)
    service.observe(SimpleNamespace(game_running=False))
    applied = []
    page = PluginsPage(
        service=service, request_apply=lambda: applied.append(True),
        open_settings=lambda _target: None,
    )
    labels = {label.text() for label in page.findChildren(QLabel)}
    buttons = {button.text() for button in page.findChildren(QPushButton)}
    assert {"技能冷却", "敌人状态", "等待启动游戏"} <= labels
    assert {"刷新状态", "检测与部署"} <= buttons
    assert "设置" not in buttons
    assert set(page.option_boxes["cooldown"]) == {"ready_cue"}
    assert set(page.option_boxes["enemy_bars"]) == {"hp", "unbalance"}
    page.option_boxes["enemy_bars"]["hp"].click()
    assert not service.settings.hp and applied
    assert page.cards["cooldown"][1].text() == "等待启动游戏"
    assert page.cards["enemy_bars"][1].text() == "正在应用"
    page.close()


def test_refresh_button_reports_progress_until_observation_arrives(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.features.plugins.page import PluginsPage
    app = QApplication.instance() or QApplication([])
    service, _, _, _ = make_service(tmp_path)
    requested = []
    page = PluginsPage(
        service=service, request_apply=lambda: requested.append(True),
        open_settings=lambda _target: None,
    )
    page.refresh_button.click()
    assert requested == [True]
    assert page.refresh_button.text() == "正在刷新…"
    assert not page.refresh_button.isEnabled()
    page.refresh(SimpleNamespace())
    assert page.refresh_button.text() == "刷新状态"
    assert page.refresh_button.isEnabled()
    page.close()


def test_enemy_display_never_stays_enabled_without_content(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.features.plugins.page import PluginsPage
    app = QApplication.instance() or QApplication([])
    service, _, _, _ = make_service(tmp_path)
    service.update(hp=False, unbalance=False)
    page = PluginsPage(
        service=service, request_apply=lambda: None,
        open_settings=lambda _target: None,
    )
    page.cards["enemy_bars"][0].click()
    assert service.settings.enemy_bars and service.settings.hp
    page.option_boxes["enemy_bars"]["hp"].click()
    assert not service.settings.enemy_bars
    page.close()
