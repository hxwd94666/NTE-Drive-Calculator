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


def test_low_mode_disables_live_hud_but_keeps_preferences(tmp_path):
    service, _, core, policy = make_service(tmp_path)
    service.update(cooldown=True)
    service.observe(SimpleNamespace(game_running=True))
    assert core.calls[-1][1]["cooldown"]
    policy.enabled = False
    service.observe(SimpleNamespace(game_running=True))
    assert not core.calls[-1][1]["cooldown"]
    assert service.settings.cooldown
    with pytest.raises(PermissionError):
        service.update(enemy_bars=True)


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
    page = PluginsPage(service=service, request_apply=lambda: applied.append(True), open_settings=lambda: None)
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
    page.close()
