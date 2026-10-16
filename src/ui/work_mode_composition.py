# 在组合根注入唯一模式策略、原生会话和后台观察所有者。
from src.integrations.nte_core import NteCoreClient
from src.integrations.native_plugin_bundle import resolve_bundled_native_core
from src.services.native_session.session import NativeGameSession
from src.services.work_modes.service import WorkModeService
from src.services.work_modes.runtime import WorkModeRuntime
from src.ui.controllers.work_mode_controller import WorkModeController
from src.ui.controllers.auto_sync_controller import AutoSyncController
from src.integrations.components.environment import game_process_running
from src.integrations.plugin_settings_store import PluginSettingsStore
from src.services.plugins.hud import PluginService


def initialize_mode_policy(window):
    path = window.app_context.paths.config_dir / "work_mode.json"
    window._migrate_legacy_component_facts = not path.exists()
    from src.integrations.distribution.machine_revalidation import MigratedMachineGate
    window.work_mode_service = WorkModeService(
        path, machine_gate=MigratedMachineGate(window.app_context.paths.data_root),
    )
    def require_operation(capability, *, automatic=False):
        # 模式暂停只限制自动服务；账号补偿失败则同时拒绝新的手动指令。
        # 停止/清理沿用会话的原 owner 出口，不经过新操作授权。
        window.app_context.require_account_ready()
        window.work_mode_service.require(capability, automatic=automatic)

    window.operation_guard = require_operation
    window.operation_generation = lambda: (window.work_mode_service.operation_revision, window.app_context.generation)
    window.native_game_session = NativeGameSession(
        factory=lambda: NteCoreClient(
            executable=resolve_bundled_native_core(window.app_context.paths.root),
            cwd=window.app_context.paths.app_dir, required_source="native",
            data_dir=window.app_context.account.log_dir / "nte_core" / "raw_capture",
        ), guard=window.operation_guard,
        context_key=lambda: (window.app_context.account.active_account_id, window.app_context.generation),
        diagnostics_enabled=lambda: (window.work_mode_service.allowed("diagnostics")
                                     and bool(window._get_sync_settings().get("raw_capture_enabled"))),
    )


def initialize_mode_runtime(window):
    from src.app.context import CallbackAccountLifecycle
    from src.features.settings.performance.controller import PerformanceController
    from src.features.settings.native_plugin_update_controller import NativePluginUpdateController
    from src.services.components.maintenance import NativePluginMaintenance, active_feature_update_hint
    from src.integrations.performance.presentmon import PresentMon
    from src.integrations.native_capture_process import native_capture_game_pid

    window.performance_controller = PerformanceController(
        context=window.app_context, policy=window.work_mode_service,
        read_status=window.native_game_session.read_performance_status,
        control=window.native_game_session.configure_performance,
        trace_control=window.native_game_session.control_performance_trace,
        frames=PresentMon(window.app_context.paths.root / "third_party/presentmon/PresentMon.exe",
                          native_capture_game_pid), parent=window,
    )
    window.app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: window.performance_controller.snapshot()["enabled"] or window.performance_controller.snapshot()['trace']['running'],
        stop=window.performance_controller.stop,
        rebuild=window.performance_controller.rebuild, start=lambda: None,
    ))

    def game_running():
        # A bound live handle is enough; do not rescan all processes while syncing.
        owner = getattr(window, "auto_sync_controller", None)
        if owner is not None and owner.packet_game_running is True:
            return True
        return game_process_running()

    window.work_mode_runtime = WorkModeRuntime(
        policy=window.work_mode_service, native_session=window.native_game_session,
        loader=window._mod_plugin_loading_service,
        application_root=window.app_context.paths.root,
        config_dir=window.app_context.paths.config_dir,
        game_running=game_running,
    )
    window.plugin_service = PluginService(
        store=PluginSettingsStore(window.app_context.paths.config_dir / "plugins.json"),
        policy=window.work_mode_service, session=window.native_game_session,
    )
    window.work_mode_controller = WorkModeController(
        window=window, policy=window.work_mode_service, runtime=window.work_mode_runtime, navigate=window._go,
        observe_plugins=window.plugin_service.observe,
        apply_plugin_policy=window.plugin_service.apply_mode_policy,
        apply_plugins=window.plugin_service.apply_current,
    )
    window.auto_sync_controller = AutoSyncController(
        window=window, policy=window.work_mode_service,
        request_check=window.work_mode_controller.check,
        request_enable_preflight=window.work_mode_controller.begin_sync_enable,
    )
    window.work_mode_controller.attach_sync_enable_action(lambda: window.auto_sync_controller.set_enabled(True))

    def check_update_blockers():
        if window.work_mode_controller.is_transitioning:
            raise RuntimeError('工作模式正在切换，请完成后更新。')
        if window.battle_report_controller.is_running():
            raise RuntimeError(active_feature_update_hint('battle_report'))
        active_owner = window.global_hotkey_manager.active_owner
        if active_owner is not None:
            raise RuntimeError(active_feature_update_hint(active_owner))

    maintenance = NativePluginMaintenance(
        session=window.native_game_session, sync=window.auto_sync_controller,
        performance=window.performance_controller, check_blockers=check_update_blockers,
        reconnect=window.work_mode_controller.check,
    )
    window.native_plugin_update_controller = NativePluginUpdateController(
        context=window.app_context, policy=window.work_mode_service,
        session=window.native_game_session, loader=window._mod_plugin_loading_service,
        generation=window.operation_generation, maintenance=maintenance, parent=window,
    )
    window.app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: window.native_plugin_update_controller.running,
        stop=window.native_plugin_update_controller.stop_for_account,
        rebuild=lambda _account: None, start=lambda: None,
    ))
    window.operation_entry = window.work_mode_controller.operation_entry
    window.operation_unavailable = window.work_mode_controller.operation_unavailable


def migrate_legacy_component_facts(window):
    if not window._migrate_legacy_component_facts:
        return
    preferences = window._ui_preferences
    path = str(preferences.get("equipment_plugin_game_executable") or "")
    record = {
        "game_executable": path,
        "deployed_sha256": str(preferences.get("equipment_plugin_deployed_sha256") or ""),
        "workspace_path": str(preferences.get("equipment_plugin_workspace") or ""),
    }
    # Only cleanup provenance migrates. No account consent or automatic-start flag does.
    window.work_mode_service.set_game_executable(path)
    window.work_mode_service.update_deployment(record if any(record.values()) else {})
    window._migrate_legacy_component_facts = False


def initialize_character_profile_sync(window):
    from src.app.context import CallbackAccountLifecycle
    from src.features.official_role.dependencies import OfficialRoleDependencies
    from src.features.official_role.page import refresh_official_role_page
    from src.features.official_role.sync_controller import CharacterProfileSyncController

    controller = CharacterProfileSyncController(
        parent=window,
        dependencies_factory=lambda: OfficialRoleDependencies.from_app_context(window.app_context),
        operation_generation=window.operation_generation,
        read_profiles=lambda *, check: window.native_game_session.read_character_profiles(check=check),
        operation_guard=window.operation_guard,
        operation_entry=window.operation_entry,
        operation_unavailable=window.operation_unavailable,
        hotkey_manager=window.global_hotkey_manager,
        connection_paused=lambda: window.work_mode_service.settings.paused,
        sync_ready=lambda: (
            window.work_mode_service.settings.auto_sync_enabled
            and bool(window._inventory_sync_service and window._inventory_sync_service.is_running)
        ),
        refresh=lambda: refresh_official_role_page(window, discard_pending=True),
    )
    window.character_profile_sync_controller = controller
    window._unregister_character_profile_sync = window.app_context.register_account_lifecycle(
        CallbackAccountLifecycle(is_running=controller.is_running, stop=controller.request_stop,
                                 rebuild=lambda _account: None, start=lambda: None),
    )
