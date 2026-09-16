# 在组合根注入唯一模式策略、原生会话和后台观察所有者。
from src.integrations.nte_core import NteCoreClient
from src.integrations.native_plugin_bundle import resolve_bundled_native_core
from src.services.native_game_session import NativeGameSession
from src.services.work_mode_service import WorkModeService
from src.services.work_mode_runtime import WorkModeRuntime
from src.ui.controllers.work_mode_controller import WorkModeController
from src.ui.controllers.auto_sync_controller import AutoSyncController
from src.services.equipment_plugin_deployment import game_process_running
from src.integrations.plugin_settings_store import PluginSettingsStore
from src.services.plugin_service import PluginService


def initialize_mode_policy(window):
    path = window.app_context.paths.config_dir / "work_mode.json"
    window._migrate_legacy_component_facts = not path.exists()
    window.work_mode_service = WorkModeService(path)
    window.operation_guard = window.work_mode_service.require
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
    )
    window.auto_sync_controller = AutoSyncController(
        window=window, policy=window.work_mode_service,
        request_check=window.work_mode_controller.check,
    )
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
        refresh=lambda: refresh_official_role_page(window, discard_pending=True),
    )
    window.character_profile_sync_controller = controller
    window._unregister_character_profile_sync = window.app_context.register_account_lifecycle(
        CallbackAccountLifecycle(is_running=controller.is_running, stop=controller.request_stop,
                                 rebuild=lambda _account: None, start=lambda: None),
    )
