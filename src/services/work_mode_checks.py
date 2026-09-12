# 将独立运行事实投影为逐功能检测结果，不执行安装或加载。
from src.domain.work_mode import (
    Capability, CheckState, FeatureCheck, NativeFeatureProbe, WorkModeProbe,
    WorkModeReport, WorkModeSettings, allowed_capabilities,
)
from src.integrations.nte_core_protocol import (
    NATIVE_CAPTURE_TRANSIENT_REASONS, NteCoreRpcError, native_capture_readiness_message,
)


def _native_check(
    feature: str, label: str, native: NativeFeatureProbe,
    probe: WorkModeProbe, *, needs_snapshot: bool,
) -> FeatureCheck:
    facts = tuple((name, getattr(native, name)) for name in (
        "files", "pipe", "handshake", "supported", "snapshot", "ready",
        "complete", "source_coverage", "projection_complete", "profile_projection_supported",
    ))

    def result(state: CheckState, detail: str, *actions: str) -> FeatureCheck:
        return FeatureCheck(feature, label, state, detail, actions, facts)

    if native.fault:
        return result(CheckState.FAULT, native.fault, "recheck")
    if not probe.game_path_valid:
        return result(CheckState.MISSING, "尚未确认游戏目录。", "detect_game_path")
    if native.files is not True:
        return result(CheckState.MISSING, "尚未核对兼容的游戏内组件。", "manual_deploy", "recheck")
    if feature == "native_load":
        return result(CheckState.AVAILABLE, "已核对组件文件；手动管理可用。")
    if native.reason == "packaged_capability_missing":
        return result(CheckState.MISSING, "当前配套原生插件未提供此项功能所需的完整能力；其他已支持功能可单独使用。", "recheck")
    if probe.core_available is not True:
        return result(CheckState.MISSING, "采集组件缺失或尚未核对兼容性。", "recheck")
    if not probe.game_running:
        return result(CheckState.WAITING, "等待启动游戏。", "recheck")
    if native.pipe is not True:
        return result(CheckState.WAITING, "等待游戏内组件建立管道。", "recheck")
    if native.handshake is False:
        return result(CheckState.FAULT, "管道存在，但握手未通过兼容性检查。", "manual_deploy", "recheck")
    if native.handshake is None:
        return result(CheckState.WAITING, "管道存在，等待实际握手核对。", "recheck")
    if native.supported is not True:
        return result(CheckState.MISSING, "握手已通过，尚未确认此项业务能力。", "manual_deploy", "recheck")
    if native.ready is not True and native.reason in {"not_ready", "source_changed"}:
        return result(CheckState.WAITING, "此域尚未就绪或刷新期间来源发生变化，请重新检测。", "recheck")
    if native.ready is not True and native.reason in (
        NATIVE_CAPTURE_TRANSIENT_REASONS | {"sdk_unavailable", "hook_unavailable"}
    ):
        message = native_capture_readiness_message(NteCoreRpcError({
            "code": -32001, "message": "not_ready", "data": {"reason": native.reason},
        }))
        state = CheckState.WAITING if native.reason in NATIVE_CAPTURE_TRANSIENT_REASONS else CheckState.FAULT
        return result(state, message, "recheck")
    if feature == "native_inventory" and native.projection_complete is True:
        return result(CheckState.AVAILABLE, "本次完整背包字段已校验；保存进度见首页背包同步。")
    if feature == "native_equipment" and native.projection_complete is not None:
        if native.reason == "equipment_check_required":
            return result(CheckState.WAITING, "后台同步不反复检测装配接口；点击重新检测可核对，实际装配前仍会检查。", "recheck")
        if native.ready is True and native.projection_complete is True:
            return result(CheckState.AVAILABLE, "原生装备接口与本次完整背包已就绪；操作后仍由后续背包确认。")
        return result(CheckState.WAITING, "等待原生装备接口和本次完整背包；已保存的历史背包不能替代。", "recheck")
    if feature == "native_character" and native.profile_projection_supported is True and native.ready is True:
        return result(CheckState.AVAILABLE, "可同步组件已支持的角色状态字段；未观测字段保持原值。")
    if needs_snapshot and (native.complete is False or (
        native.snapshot is True and native.source_coverage != "complete"
    )):
        return result(CheckState.MISSING, native.reason or "当前仅有部分观察，来源覆盖尚未证明完整，不能用于正式同步。", "recheck")
    if needs_snapshot and native.snapshot is not True:
        return result(CheckState.WAITING, "等待完整业务快照；已有增量不能替代完整快照。", "recheck")
    if needs_snapshot and native.complete is not True:
        return result(CheckState.MISSING, native.reason or "已收到数据，但尚未确认业务完整性。", "recheck")
    if native.ready is not True:
        return result(CheckState.MISSING, native.reason or "组件声明了此项能力，但业务尚未就绪。", "recheck")
    return result(CheckState.AVAILABLE, "此项业务所需条件已就绪。")


def build_work_mode_report(settings: WorkModeSettings, probe: WorkModeProbe) -> WorkModeReport:
    allowed = allowed_capabilities(settings)
    items = [FeatureCheck("local", "本地计算与已保存数据", CheckState.AVAILABLE, "可使用本地计算与已保存数据。")]
    items.append(FeatureCheck(
        "history_analysis", "历史战报分析",
        CheckState.AVAILABLE if probe.analysis_available else CheckState.MISSING,
        "已核对兼容分析组件，可离线分析历史战报。" if probe.analysis_available else
        "缺少或尚未核对兼容分析组件；请安装完整 Calc 组件包后重新检测。", ("recheck",),
    ))
    if Capability.NATIVE_LOAD in allowed:
        items.append(FeatureCheck(
            "component_update", "自动部署与更新",
            probe.component_update_state or CheckState.WAITING,
            probe.component_update_detail or "等待核对配套组件包。", ("recheck",),
        ))
    if settings.pending_cleanup:
        items.append(FeatureCheck(
            "cleanup", "清理游戏目录", CheckState.CLEANUP_PENDING,
            probe.cleanup_detail or (
                "游戏正在运行，退出后重新核对并清理已管理组件。"
                if probe.game_running else "等待核对本程序管理的组件，已不存在视为已清理。"
            ), ("recheck",),
        ))
    if Capability.INTERFACE_INPUT in allowed:
        items.append(FeatureCheck(
            "interface_input", "鼠标与手柄界面操作",
            CheckState.MISSING if probe.input_available is not True else (
                CheckState.AVAILABLE if probe.game_running else CheckState.WAITING
            ), "界面输入已就绪。" if probe.input_available and probe.game_running else (
                "等待启动游戏。" if probe.input_available else "界面输入条件缺失或尚未检查。"
            ), ("recheck",),
        ))
    if Capability.PACKET_CAPTURE in allowed:
        state, detail, actions = CheckState.AVAILABLE, "抓包与完整快照已就绪。", ()
        if probe.packet_fault:
            state, detail, actions = CheckState.FAULT, probe.packet_fault, ("recheck",)
        elif probe.core_available is not True:
            state, detail, actions = CheckState.MISSING, "采集组件缺失或尚未核对。", ("recheck",)
        elif probe.npcap_available is not True:
            state, detail, actions = CheckState.MISSING, "Npcap 缺失或尚未核对。", ("download_npcap", "recheck")
        elif not probe.packet_listening:
            state, detail, actions = CheckState.WAITING, "等待启动抓包监听；应在登录前监听。", ("recheck",)
        elif not probe.game_running or not probe.logged_in:
            state, detail = CheckState.WAITING, "监听已启动，等待游戏登录和完整数据。"
        elif not probe.packet_snapshot:
            state, detail = CheckState.WAITING, "等待完整登录数据；晚启动可能需要重新登录，增量不视为完整。"
        items.append(FeatureCheck("packet_capture", "抓包同步", state, detail, actions, (
            ("npcap", probe.npcap_available), ("listening", probe.packet_listening),
            ("snapshot", probe.packet_snapshot),
        )))
    for capability, label, needs_snapshot in (
        (Capability.NATIVE_LOAD, "游戏内组件文件", False),
        (Capability.NATIVE_BATTLE, "DLL 战报", False),
        (Capability.NATIVE_EQUIPMENT, "DLL 装配", True),
    ):
        if capability in allowed:
            items.append(_native_check(capability.value, label, getattr(probe, capability.value), probe, needs_snapshot=needs_snapshot))
    if Capability.NATIVE_SYNC in allowed:
        for feature, label in (
            ("native_character", "DLL 角色状态（已支持字段）"),
            ("native_inventory", "DLL 完整背包库存"),
            ("native_team", "DLL 队伍"),
            ("native_environment", "DLL 环境"),
        ):
            items.append(_native_check(feature, label, getattr(probe, feature), probe, needs_snapshot=True))
    return WorkModeReport(settings.mode, tuple(items))
