# 将开启同步前的环境事实投影为可操作的下一步，不执行组件写入。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.work_mode import CheckState, WorkMode, WorkModeProbe, WorkModeSettings


@dataclass(frozen=True)
class SyncEnableDecision:
    ready: bool
    detail: str
    target: str = "deployment"
    action_label: str | None = None


def decide_sync_enable(
    settings: WorkModeSettings, record: dict, probe: WorkModeProbe,
) -> SyncEnableDecision:
    # 只投影开启同步的准备条件；组件文件就绪不等于业务库存完整。
    if settings.mode == WorkMode.OFFLINE:
        return SyncEnableDecision(False, "当前是离线模式；请先确认可使用的工作模式。",
                                  "mode", "前往工作模式设置")
    if not settings.risk_confirmed:
        return SyncEnableDecision(False, "当前工作模式尚未确认；请先完成模式确认。",
                                  "mode", "前往工作模式设置")
    if settings.mode == WorkMode.LOW:
        if probe.core_available is not True:
            return SyncEnableDecision(False, "抓包 Core 尚未通过核对，请检查安装组件。",
                                      "core", "前往环境设置" if probe.core_available is False else None)
        if probe.npcap_available is not True:
            return SyncEnableDecision(False, "Npcap 尚未确认可用；请在环境设置中安装或重新检测。", "npcap")
        return SyncEnableDecision(True, "抓包条件已核对；开启监听后等待游戏与登录数据。")
    if probe.game_path_valid is not True:
        return SyncEnableDecision(False, "游戏路径尚未确认；请在环境设置中选择 HTGame.exe。",
                                  "game_path", "前往确认游戏路径" if probe.game_path_valid is False else None)
    if probe.core_available is not True or probe.component_update_state == CheckState.MISSING:
        return SyncEnableDecision(False, "原生组件包尚未通过核对，请检查安装组件。",
                                  "deployment", "前往环境设置" if probe.core_available is False or
                                  probe.component_update_state == CheckState.MISSING else None)
    if probe.component_update_state == CheckState.FAULT:
        return SyncEnableDecision(False, "组件核对中断；请查看检测详情中的原因。",
                                  "deployment", "前往环境设置")
    if settings.pending_cleanup:
        detail = ("请先完全退出游戏，再清理旧组件。" if probe.game_running else
                  "旧组件尚待清理；请先清理游戏目录。")
        return SyncEnableDecision(False, detail, "deployment", "前往清理游戏目录")
    if probe.native_load.files is False:
        if probe.game_running:
            detail = ("请先关闭启动器并完全退出游戏，再部署 Loader。" if
                      record.get("loading_method") == "loader" else
                      "请先完全退出游戏，再部署当前组件。")
            return SyncEnableDecision(False, detail, "deployment", "前往部署组件")
        if record.get("loading_method") == "loader":
            if probe.launcher_probe_error:
                return SyncEnableDecision(False, "启动器状态核对中断；请关闭启动器和游戏后重新检测。",
                                          "deployment", "前往部署组件")
            if probe.launcher_running is None:
                return SyncEnableDecision(False, "启动器进程状态尚未核对，请重新检测。")
            if probe.launcher_running:
                return SyncEnableDecision(False, "请先关闭启动器和游戏，再部署 Loader。",
                                          "deployment", "前往部署组件")
        detail = "当前组件尚未部署；请先部署，再返回开启同步。"
        return SyncEnableDecision(False, detail,
                                  "deployment", "前往部署组件")
    if probe.native_load.files is not True:
        return SyncEnableDecision(False, "组件文件状态尚未确认，请重新检测。")
    return SyncEnableDecision(
        True,
        ("当前组件文件已核对；开启同步时恢复此前清理留下的暂停，业务数据仍需等待游戏就绪。"
         if settings.paused else "当前组件文件已核对；业务数据仍需等待游戏就绪。"),
    )
