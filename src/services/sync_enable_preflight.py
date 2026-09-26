# 将开启同步前的环境事实投影为可操作的下一步，不执行组件写入。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.work_mode import CheckState, WorkMode, WorkModeProbe, WorkModeSettings


@dataclass(frozen=True)
class SyncEnableDecision:
    ready: bool
    detail: str
    target: str = "deployment"


def decide_sync_enable(
    settings: WorkModeSettings, record: dict, probe: WorkModeProbe,
) -> SyncEnableDecision:
    if settings.paused:
        return SyncEnableDecision(False, "连接处于安全暂停；请在设置中重新确认工作模式。", "mode")
    if settings.mode == WorkMode.OFFLINE or not settings.risk_confirmed:
        return SyncEnableDecision(False, "当前是离线模式；请先确认可使用的工作模式。", "mode")
    if settings.mode == WorkMode.LOW:
        if probe.core_available is not True:
            return SyncEnableDecision(False, "抓包 Core 尚未通过核对，请检查安装组件。", "core")
        if probe.npcap_available is not True:
            return SyncEnableDecision(False, "Npcap 尚未确认可用；请在环境设置中安装或重新检测。", "npcap")
        return SyncEnableDecision(True, "抓包条件已核对。确认后开启监听，等待游戏与登录数据。")
    if probe.game_path_valid is not True:
        return SyncEnableDecision(False, "游戏路径尚未确认；请在环境设置中选择 HTGame.exe。", "game_path")
    if probe.core_available is not True or probe.component_update_state == CheckState.MISSING:
        return SyncEnableDecision(False, "原生组件包尚未通过核对，请检查安装组件。")
    if probe.component_update_state == CheckState.FAULT:
        return SyncEnableDecision(False, probe.component_update_detail or "组件检测出现故障，请查看详情。")
    needs_write = settings.pending_cleanup or probe.native_load.files is not True
    if needs_write and probe.game_running:
        if record.get("loading_method") == "loader":
            return SyncEnableDecision(False, "游戏仍在运行；请关闭启动器并完全退出游戏，再准备 Loader。", "loading_method")
        return SyncEnableDecision(False, "游戏仍在运行；请完全退出游戏，再清理或部署组件。")
    if record.get("loading_method") == "loader" and needs_write:
        if probe.launcher_probe_error:
            return SyncEnableDecision(False, probe.launcher_probe_error, "loading_method")
        if probe.launcher_running is None:
            return SyncEnableDecision(False, "启动器进程状态尚未核对，请重新检测。", "loading_method")
        if probe.launcher_running:
            return SyncEnableDecision(False, "启动器仍在运行；请关闭启动器和游戏后再部署 Loader。", "loading_method")
    if needs_write:
        return SyncEnableDecision(True, "确认后将按现有归属记录处理待清理项、准备组件，再开启同步；不会关闭游戏或启动器。")
    return SyncEnableDecision(True, "当前组件文件已核对。确认后开启同步，业务数据仍需等待游戏就绪。")


def decide_sync_activation(settings: WorkModeSettings, probe: WorkModeProbe) -> SyncEnableDecision:
    """Confirm the post-action result before persisting synchronization on."""
    if settings.mode == WorkMode.LOW:
        if probe.core_available is True and probe.npcap_available is True:
            return SyncEnableDecision(True, "抓包条件已就绪；开启后等待游戏和登录数据。")
        return SyncEnableDecision(False, "抓包条件尚未就绪，请重新检测。", "npcap")
    if settings.pending_cleanup:
        return SyncEnableDecision(False, probe.cleanup_detail or "组件清理尚未完成，请查看检测详情。")
    if probe.component_update_state == CheckState.FAULT:
        return SyncEnableDecision(False, probe.component_update_detail or "组件处理出现故障，请查看检测详情。")
    if probe.native_load.files is not True:
        return SyncEnableDecision(False, probe.component_update_detail or "组件尚未部署完成，请重新检测。")
    return SyncEnableDecision(True, "组件文件已核对；开启后仍需等待游戏连接及完整业务数据。")
