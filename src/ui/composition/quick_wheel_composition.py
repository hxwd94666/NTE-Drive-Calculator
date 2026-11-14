# 在组合根注入已有战报与 HUD 的公开动作，轮盘不另建采集或账号上下文。
from src.features.plugins.quick_wheel_controller import QuickWheelController
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.app.context import CallbackAccountLifecycle


def build_quick_wheel(window):
    def hud_settings():
        return window.plugin_service.settings.payload(), {
            key: window.plugin_service.status_for(key) for key in ('cooldown', 'enemy_bars')}

    def change_hud(key, enabled):
        window.plugin_service.update(**{key: enabled})
        window.work_mode_controller.refresh_plugins()

    controller = QuickWheelController(
        session=window.native_game_session,
        context_key=lambda: (window.app_context.account.active_account_id, window.app_context.generation),
        policy=window.work_mode_service,
        battle_state=window.battle_report_controller.quick_capture_state,
        start_battle=window.battle_report_controller.start,
        stop_battle=window.battle_report_controller.stop,
        hud_settings=hud_settings, change_hud=change_hud,
        performance_state=window.performance_controller.snapshot,
        change_performance=window.performance_controller.set_enabled,
        preferences=LocalPreferencesStore(window.app_context.paths.config_dir / 'quick_wheel.json'),
        runtime_allowed=lambda: (window.work_mode_service.allowed('native_load')
                                and not window.work_mode_service.settings.paused
                                and not window.native_game_session.maintenance_active),
        restore_window=window.tray_controller.restore, parent=window,
    )
    unregister = window.app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: controller.enabled or controller.is_running(),
        stop=controller.stop_for_account, rebuild=controller.rebuild_account,
        start=controller.resume_account,
    ))
    controller.destroyed.connect(lambda: unregister())
    return controller
