# 统一工作模式检测报告与故障诊断，分别保留组件文件、协议和业务就绪事实。
from __future__ import annotations

from concurrent.futures import CancelledError
from collections.abc import Collection
import math
from pathlib import Path

from dataclasses import replace
from typing import Any

from src.domain.work_mode import (
    Capability, CheckState, FeatureCheck, NativeFeatureProbe, WorkModeProbe,
    WorkMode, WorkModeReport, WorkModeSettings, allowed_capabilities,
)
from src.services.work_modes.sync_preflight import decide_sync_enable

from src.integrations.nte_core_protocol import (
    NATIVE_CAPTURE_TRANSIENT_REASONS, NteCoreError, NteCoreNotFoundError, NteCoreProcessError,
    NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError,
    native_capture_readiness_message,
)


_START_FAILURES = {
    'peer_identity_mismatch': (
        'Core 与游戏的 Windows 登录身份或权限不一致，请以与游戏相同的用户和权限启动 Calc。',
        '确认 Calc 与游戏使用同一 Windows 用户、相同权限级别后，再重新检测。',
    ),
    'native_resources_unavailable': (
        '采集 Core 缺少必需的内置资源，请更新完整的采集组件。',
        '核对当前安装包是否完整，并使用同一发布包中的 Core 与采集 DLL。',
    ),
    'native_context_capability_required_restart_game': (
        '游戏内的采集 DLL 版本过旧，请更新采集组件并重启游戏。',
        '先核对配套组件版本；完全退出游戏后部署，再启动游戏。重复部署同一旧包不会更新版本。',
    ),
}
_METHODS = frozenset({
    'core.hello', 'core.status', 'equipment.status', 'native.snapshot.status',
    'native.snapshot.refresh', 'native.snapshot.page', 'native.snapshot.changes',
    'native.inventory.page', 'native.character.page',
    'native.diagnostics.configure', 'native.snapshot.open',
})
_BUSINESS_FAILURES = {
    'NATIVE_MAPPING_UNSUPPORTED': '原生数据包含当前 Core 无法转换的角色或装备映射，本次读取已停止，已有数据保持不变。',
    'NATIVE_SNAPSHOT_INCOMPLETE': '本次原生快照未通过完整性校验，不能作为完整背包或角色数据。',
    'NATIVE_CAPABILITY_MISSING': '当前 Core 或 DLL 缺少本次操作所需的原生能力。',
    'NATIVE_SNAPSHOT_UNAVAILABLE': '当前配套组件未提供所请求的原生快照能力。',
    'NATIVE_SNAPSHOT_NOT_FOUND': '请求的原生快照已不可用，需要重新读取。',
    'NATIVE_SNAPSHOT_ARCHIVE_WRITE_FAILED': '原生快照诊断文件写入失败，请检查本地日志目录。',
    'PROTOCOL_VERSION_MISMATCH': 'Calc 与采集 Core 的协议版本不匹配。',
    'HANDSHAKE_REQUIRED': '采集 Core 尚未接受本会话的握手，不能执行本次请求。',
    'REQUEST_IN_PROGRESS': '采集 Core 正在处理另一个请求，本次检测未完成。',
}
_TRANSPORT_FAILURES = {
    'connect_timeout': '连接游戏内采集管道超时；请核对是否有其他工具占用连接。',
    'pipe_open_failed': '无法打开游戏内采集管道；请核对进程权限和连接占用。',
    'pipe_disconnected': '游戏内采集管道已断开，本次检测未完成。',
    'pipe_io_failed': '读写游戏内采集管道失败。',
    'pipe_write_failed': '向游戏内采集管道写入请求失败。',
    'pipe_write_timeout': '向游戏内采集管道写入请求超时。',
    'pipe_server_mismatch': '管道服务端与目标游戏进程不匹配，已拒绝连接。',
    'handshake_rejected': '游戏内采集组件拒绝了握手请求。',
    'unsupported_provider': '游戏内采集组件未通过协议或进程身份校验。',
}
_PROCESS_MESSAGES = frozenset({
    '无法读取游戏进程列表。', '读取游戏进程列表失败。',
    '检测到多个游戏进程，请保留一个后再开始增强采集。',
    '无法核对游戏进程身份，未启动本场采集。',
    '无法核对游戏进程创建时间，未启动本场采集。',
    '无法确定增强采集组件状态，未启动本场采集。',
})
_COPY_HINT = '点击“复制检测结果”发送给维护者；无需仅因这条提示反复重装或重启。'


def detection_failure_state(error: Exception) -> CheckState:
    """Classify typed transient evidence, never infer severity from diagnostic text."""
    if isinstance(error, CancelledError):
        return CheckState.WAITING
    if isinstance(error, NteCoreTimeoutError):
        return CheckState.WARNING
    if isinstance(error, NteCoreRpcError):
        if error.domain_code in _BUSINESS_FAILURES:
            return (CheckState.WARNING if error.domain_code in {
                'REQUEST_IN_PROGRESS', 'NATIVE_SNAPSHOT_NOT_FOUND',
            } else CheckState.FAULT)
        if error.domain_code in {'MODS_PLUGIN_BUSY', 'EQUIPMENT_PLUGIN_BUSY'}:
            return CheckState.WARNING
        if error.code == -32001:
            reason = error.data.get('reason')
            if isinstance(reason, str) and reason in {'sdk_unavailable', 'hook_unavailable'}:
                return CheckState.FAULT
            if isinstance(reason, str) and reason in {
                'world_unavailable', 'controller_unavailable', 'pawn_unavailable',
            }:
                return CheckState.WAITING_LOGIN
            if isinstance(reason, str) and reason in NATIVE_CAPTURE_TRANSIENT_REASONS:
                return CheckState.WAITING
            if error.message in {'not_ready', 'source_changed', 'control_busy', 'control_timeout',
                                 'snapshot_not_found'}:
                return CheckState.WARNING
    return CheckState.FAULT


def _failure_location(error: Exception) -> str:
    """Only expose a repository-relative Python location, never an external path."""
    root = Path(__file__).resolve().parents[3]
    location = ''
    trace = error.__traceback__
    while trace is not None:
        try:
            relative = Path(trace.tb_frame.f_code.co_filename).resolve().relative_to(root)
            if relative.parts[0] == 'src' and relative.suffix == '.py':
                location = f'{relative.as_posix()}:{trace.tb_lineno}'
        except (ValueError, OSError):
            pass
        trace = trace.tb_next
    return location


def detection_failure_detail(error: Exception, *, record: bool = False) -> str:
    """Keep typed evidence and known reasons; never forward raw error text or stderr."""
    evidence = [f'异常类型：{type(error).__name__}']
    context = error.request_context if isinstance(error, NteCoreError) else None
    if context is not None:
        method = context.method if context.method in _METHODS else '未识别接口'
        evidence.append(f'失败接口：{method}')
        digest = context.executable_sha256
        if isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdefABCDEF' for c in digest):
            evidence.append(f'Core SHA-256：{digest}')
        if context.handshake_confirmed:
            evidence.append('本次握手：已确认')
    location = _failure_location(error)
    if location:
        evidence.append(f'代码位置：{location}')
    reason = '检测发生未识别异常，尚不能确定是连接、组件还是业务数据问题。'
    next_step = _COPY_HINT
    cause = error.__cause__ if isinstance(error.__cause__, OSError) else error
    winerror = getattr(cause, 'winerror', None)
    if isinstance(winerror, int):
        evidence.append(f'Windows 错误码：{winerror}')

    if isinstance(error, NteCoreProcessError):
        reason = '采集 Core 启动或运行失败，未能完成本次检测。'
        if isinstance(error.return_code, int):
            evidence.append(f'Core 退出码：{error.return_code}')
        if str(error) in _PROCESS_MESSAGES:
            reason = str(error)
        for code, (message, action) in _START_FAILURES.items():
            if str(error) == message or any(
                line.strip() == f'error: native capture {code}' for line in error.stderr_lines
            ):
                reason, next_step = message, action
                evidence.append(f'原因码：{code}')
                break
        else:
            for code, message in _TRANSPORT_FAILURES.items():
                if any(line.strip() == f'error: native capture {code}' for line in error.stderr_lines):
                    reason = message
                    evidence.append(f'原因码：{code}')
                    break
    elif isinstance(error, NteCoreTimeoutError):
        reason = '等待采集 Core 响应超时，本次检测未完成；超时本身不能证明组件没有加载。'
        method = error.method if error.method in _METHODS else '未识别接口'
        evidence.append(f'超时接口：{method}')
        if isinstance(error.timeout, (int, float)) and math.isfinite(error.timeout):
            evidence.append(f'等待上限：{error.timeout:g} 秒')
        next_step = '确认已进入可操作角色的场景，并结束其他工具的采集连接；若仍失败，' + _COPY_HINT
    elif isinstance(error, NteCoreRpcError):
        reason = '采集 Core 返回业务错误，本次检测未完成。'
        evidence.append(f'RPC 错误码：{error.code}')
        known_reason = error.data.get('reason')
        if isinstance(known_reason, str) and known_reason in (
            NATIVE_CAPTURE_TRANSIENT_REASONS | {'sdk_unavailable', 'hook_unavailable', 'provider_stopping'}
        ):
            reason = native_capture_readiness_message(error)
            evidence.append(f'原因码：{known_reason}')
        elif error.domain_code in _BUSINESS_FAILURES:
            reason = _BUSINESS_FAILURES[error.domain_code]
            evidence.append(f'原因码：{error.domain_code}')
            if error.domain_code in {'NATIVE_MAPPING_UNSUPPORTED', 'NATIVE_CAPABILITY_MISSING',
                                     'NATIVE_SNAPSHOT_UNAVAILABLE', 'PROTOCOL_VERSION_MISMATCH'}:
                next_step = '核对 Calc、Core、DLL 与内嵌角色/装备资源是否配套；' + _COPY_HINT
        elif error.domain_code in {'MODS_PLUGIN_BUSY', 'EQUIPMENT_PLUGIN_BUSY'}:
            reason = '游戏内组件正在处理其他请求。'
            next_step = '结束其他工具的采集或操作任务后重新检测；若仍失败，' + _COPY_HINT
            evidence.append(f'原因码：{error.domain_code}')
        elif error.code == -32001 and error.message in {'control_timeout', 'not_ready'}:
            # 仅展示正式的精确协议错误码，不展示任意 provider 原文。
            reason = {
                'control_timeout': '游戏内采集请求等待执行或完成超时，本次检测未完成。',
                'not_ready': '游戏内采集接口尚未就绪，本次检测未完成。',
            }[error.message]
            evidence.append(f'原因码：{error.message}')
            next_step = '确认已进入可操作角色的场景后重新检测；若持续出现，' + _COPY_HINT
        elif error.code == -32601:
            reason = '采集 Core 不支持本次检测接口，需要核对 Calc、Core 与 DLL 是否配套。'
    elif isinstance(error, NteCoreNotFoundError):
        reason = '未找到可用的采集 Core 执行文件。'
        next_step = '核对 Calc 安装是否完整，以及安全软件是否隔离了 nte-core.exe。'
    elif isinstance(error, NteCoreProtocolError):
        reason = '采集响应未通过协议或数据格式校验，不能作为有效业务结果。'
        next_step = '核对 Calc、Core、DLL 是否来自同一配套发布包，并确认支持当前游戏版本；' + _COPY_HINT
    elif isinstance(error, CancelledError):
        reason = '检测已取消，本次没有完整结果。'
        next_step = '等待当前任务结束后重新检测。'
    elif isinstance(error, PermissionError):
        reason = '检测所需授权或访问权限被拒绝。'
        next_step = '核对已确认的工作模式、暂停状态及 Calc 与游戏的运行权限。'
    if winerror == 5 or (isinstance(cause, OSError) and cause.errno == 13):
        reason = 'Windows 拒绝访问检测所需的进程、文件或连接。'
        next_step = '核对 Calc 与游戏的 Windows 用户和权限，以及文件访问权限。'

    detail = f'原因：{reason}\n下一步：{next_step}\n诊断：' + '；'.join(evidence)
    if record:
        from src.utils.logger import logger
        local_detail = detail.replace('\n', ' | ')
        if context is not None and context.executable_path:
            path = context.executable_path.replace('\r', r'\r').replace('\n', r'\n')
            local_detail += f' | Core 路径：{path}'
        logger.warning('environment.detection_failed | {}', local_detail)
    return detail


_LOGIN_WAIT_DETAIL = "请登录并进入游戏场景；已进入时请等待完整数据同步，已有数据保持不变。"
_NATIVE_DATA_FAULTS = frozenset({
    "NATIVE_MAPPING_UNSUPPORTED", "NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_CAPABILITY_MISSING",
    "NATIVE_SNAPSHOT_UNAVAILABLE", "PROTOCOL_VERSION_MISMATCH", "HANDSHAKE_REQUIRED",
})


def _native_check(
    feature: str, label: str, native: NativeFeatureProbe,
    probe: WorkModeProbe, *, needs_snapshot: bool, external_provider_allowed: bool = False,
) -> FeatureCheck:
    facts = tuple((name, getattr(native, name)) for name in (
        "files", "pipe", "handshake", "supported", "snapshot", "ready",
        "complete", "source_coverage", "projection_complete", "profile_projection_supported",
    ))

    def result(state: CheckState, detail: str, *actions: str) -> FeatureCheck:
        return FeatureCheck(feature, label, state, detail, actions, facts)

    if native.fault:
        return result(CheckState.FAULT, native.fault, "recheck")
    if probe.game_path_valid is None:
        return result(CheckState.WAITING, "正在核对游戏目录。", "recheck")
    if probe.game_path_valid is False:
        return result(CheckState.MISSING, "尚未确认游戏目录。", "detect_game_path")
    external_connection = (external_provider_allowed and feature != "native_load"
                           and native.pipe is True and native.handshake is True)
    if native.reason == "packaged_capability_missing":
        return result(CheckState.MISSING, "当前配套原生插件未提供此项功能所需的完整能力；其他已支持功能可单独使用。", "recheck")
    if native.files is None and not external_connection:
        return result(CheckState.WAITING, "尚未完成游戏内组件文件核对。", "recheck")
    if native.files is False and not external_connection:
        if probe.core_available is not True:
            return result(
                CheckState.MISSING if probe.core_available is False else CheckState.WAITING,
                "配套组件包尚未通过核对，暂不能部署。" if probe.core_available is False else
                "正在核对配套组件包。", "recheck",
            )
        return result(CheckState.MISSING, "游戏内组件未部署或与当前组件包不兼容；请前往部署组件后重新检测。", "manual_deploy", "recheck")
    if feature == "native_load":
        return result(CheckState.AVAILABLE, "已核对组件文件；手动管理可用。")
    if probe.core_available is not True:
        return result(CheckState.MISSING if probe.core_available is False else CheckState.WAITING,
                      "配套采集组件未通过核对，请查看组件包检测详情。" if probe.core_available is False else "正在核对采集组件兼容性。", "recheck")
    if probe.game_running is False:
        return result(CheckState.WAITING, "等待启动游戏。", "recheck")
    if probe.game_running is None:
        return result(CheckState.WAITING, "游戏进程状态尚未确认，请重新检测。", "recheck")
    if probe.native_diagnostic:
        return FeatureCheck(
            feature, label, (
                CheckState.WAITING_LOGIN
                if probe.native_diagnostic_state == CheckState.WAITING_LOGIN else CheckState.WAITING
            ),
            "本次未完成检测；共同失败原因见“原生连接与业务检测”，不代表本项已单独确认故障。",
            ("recheck",), facts + (("inspection_incomplete", True),),
        )
    if native.pipe is not True:
        return result(CheckState.WAITING, "等待游戏内组件建立管道。", "recheck")
    if native.handshake is False:
        return result(CheckState.FAULT, "管道存在，但握手未通过兼容性检查。", "recheck")
    if native.handshake is None:
        return result(CheckState.WAITING, "管道存在，等待实际握手核对。", "recheck")
    if native.supported is None:
        return result(CheckState.WAITING, "握手已通过，正在核对本项业务能力。", "recheck")
    if native.supported is False:
        return result(CheckState.MISSING, "当前组件未提供本项业务能力，请核对配套组件版本。", "recheck")
    if native.reason in _NATIVE_DATA_FAULTS:
        return result(CheckState.FAULT, "本项数据或协议校验失败，请核对配套组件并重新检测。", "recheck")
    if native.ready is not True and native.reason in {"not_ready", "source_changed"}:
        if feature == "native_inventory":
            return result(CheckState.WAITING_LOGIN, _LOGIN_WAIT_DETAIL, "recheck")
        return result(CheckState.WAITING, "此域尚未就绪或刷新期间来源发生变化，请重新检测。", "recheck")
    if native.ready is not True and native.reason in (
        NATIVE_CAPTURE_TRANSIENT_REASONS | {"sdk_unavailable", "hook_unavailable"}
    ):
        message = native_capture_readiness_message(NteCoreRpcError({
            "code": -32001, "message": "not_ready", "data": {"reason": native.reason},
        }))
        state = (CheckState.WAITING_LOGIN if native.reason in {
            "world_unavailable", "controller_unavailable", "pawn_unavailable",
        } else CheckState.WAITING if native.reason in NATIVE_CAPTURE_TRANSIENT_REASONS else CheckState.FAULT)
        if state == CheckState.WAITING_LOGIN:
            message += "；请登录并进入游戏场景后重新检测。"
        return result(state, message, "recheck")
    if feature == "native_inventory" and native.projection_complete is True:
        return result(CheckState.AVAILABLE, "本次完整背包字段已校验；保存进度见首页背包同步。")
    if feature == "native_equipment" and native.projection_complete is not None:
        if native.reason == "equipment_check_required":
            return result(CheckState.WAITING, "后台同步不反复检测装配接口；点击重新检测可核对，实际装配前仍会检查。", "recheck")
        if native.ready is True:
            return result(CheckState.AVAILABLE, "原生装备接口已就绪；装配使用最近已保存的完整背包，操作后确认结果。")
        return result(CheckState.WAITING, "等待原生装备接口就绪。", "recheck")
    if feature == "native_character" and native.profile_projection_supported is True and native.ready is True:
        return result(CheckState.AVAILABLE, "可同步组件已支持的角色状态字段；未观测字段保持原值。")
    observation_details = {
        "native_team": "已取得组件支持的队伍观测；角色对象与装备实例的完整对应关系尚未确认，不能视为完整实际队伍。",
        "native_environment": "已取得组件支持的环境观测；未观测的世界等级、关卡、半场或效果生效状态仍需其他证据补充。",
    }
    if feature in observation_details:
        if native.snapshot is not True:
            return result(CheckState.WAITING, "尚未取得本项观测数据，请重新检测。", "recheck")
        if native.ready is not True:
            return result(CheckState.WAITING, native.reason or "本项观测尚未就绪，请重新检测。", "recheck")
        return result(CheckState.AVAILABLE, observation_details[feature])
    if needs_snapshot and (native.complete is False or (
        native.snapshot is True and native.source_coverage != "complete"
    )):
        return result(CheckState.WAITING_LOGIN, "当前仅有部分观察，尚未取得完整数据。" + _LOGIN_WAIT_DETAIL, "recheck")
    if needs_snapshot and native.snapshot is not True:
        return result(CheckState.WAITING_LOGIN, "等待完整业务快照。" + _LOGIN_WAIT_DETAIL, "recheck")
    if needs_snapshot and native.complete is not True:
        return result(CheckState.WAITING_LOGIN, "已收到数据，完整性尚待确认。" + _LOGIN_WAIT_DETAIL, "recheck")
    if native.ready is not True:
        return result(CheckState.WARNING, "组件已声明本项能力，但业务尚未就绪；请稍候重新检测。", "recheck")
    return result(CheckState.AVAILABLE, "此项业务所需条件已就绪。")


def build_work_mode_report(
    settings: WorkModeSettings, probe: WorkModeProbe, *, deployment_record: dict | None = None,
) -> WorkModeReport:
    allowed = allowed_capabilities(settings)
    items = [FeatureCheck("local", "本地计算与已保存数据", CheckState.AVAILABLE, "可使用本地计算与已保存数据。")]
    items.append(FeatureCheck(
        "history_analysis", "历史战报分析",
        CheckState.AVAILABLE if probe.analysis_available is True else (
            CheckState.MISSING if probe.analysis_available is False else CheckState.WAITING),
        "已核对兼容分析组件，可离线分析历史战报。" if probe.analysis_available else
        "缺少兼容分析组件；请安装完整 Calc 组件包后重新检测。" if probe.analysis_available is False else
        "尚未完成分析组件核对，请重新检测。", ("recheck",),
    ))
    if Capability.NATIVE_LOAD in allowed:
        items.append(FeatureCheck(
            "component_update", "自动部署与更新",
            probe.component_update_state or CheckState.WAITING,
            probe.component_update_detail or "等待核对配套组件包。", ("recheck",),
        ))
    if settings.pending_cleanup:
        items.append(FeatureCheck(
            "cleanup", "确认游戏路径" if probe.game_path_valid is False else "清理游戏目录",
            probe.cleanup_state or CheckState.WAITING,
            probe.cleanup_detail or (
                "游戏正在运行，退出后重新核对并清理已管理组件。"
                if probe.game_running else "等待核对本程序管理的组件，已不存在视为已清理。"
            ), ("detect_game_path", "recheck") if probe.game_path_valid is False else ("recheck",),
        ))
    if Capability.INTERFACE_INPUT in allowed:
        items.append(FeatureCheck(
            "interface_input", "鼠标与手柄界面操作",
            CheckState.WAITING if probe.input_available is None else CheckState.MISSING if probe.input_available is False else (
                CheckState.AVAILABLE if probe.game_running else CheckState.WAITING
            ), "界面输入已就绪。" if probe.input_available and probe.game_running else (
                "等待启动游戏。" if probe.input_available else
                "界面输入依赖缺失。" if probe.input_available is False else "界面输入条件尚未检查。"
            ), ("recheck",),
        ))
    if Capability.PACKET_CAPTURE in allowed:
        npcap_state = (CheckState.AVAILABLE if probe.npcap_available is True else
                       CheckState.MISSING if probe.npcap_available is False else CheckState.WAITING)
        npcap_detail = (
            "已检测到 Npcap 安装；抓包能否正常启动见下方抓包同步状态。" if probe.npcap_available is True else
            "未检测到 Npcap，低风险抓包需要此组件。请点击下方“下载 Npcap”，运行官方安装程序，"
            "安装完成后回到这里点击“重新检测”。" if probe.npcap_available is False else
            "尚未取得 Npcap 检测结果，请重新检测。"
        )
        items.append(FeatureCheck("npcap", "Npcap 抓包依赖", npcap_state, npcap_detail,
                                  ("download_npcap", "recheck") if probe.npcap_available is False else ("recheck",)))
        state, detail, actions = CheckState.AVAILABLE, "抓包与完整快照已就绪。", ()
        if probe.packet_fault:
            state, detail, actions = CheckState.FAULT, probe.packet_fault, ("recheck",)
        elif probe.core_available is not True:
            state = CheckState.MISSING if probe.core_available is False else CheckState.WAITING
            detail, actions = "配套采集组件未通过核对，请查看组件包检测详情。" if probe.core_available is False else "采集组件尚未核对。", ("recheck",)
        elif probe.npcap_available is not True:
            state, detail, actions = npcap_state, "抓包依赖尚未就绪，请按上方 Npcap 检测结果处理。", ("recheck",)
        elif not probe.packet_listening:
            state, detail, actions = CheckState.WAITING, "等待启动抓包监听；应在登录前监听。", ("recheck",)
        elif probe.game_running is not True:
            state, detail = CheckState.WAITING, "监听已启动，等待游戏进程就绪。"
        elif not probe.logged_in:
            state, detail = CheckState.WAITING_LOGIN, "监听已启动，请登录进入游戏，等待完整数据。"
        elif not probe.packet_snapshot:
            state, detail = CheckState.WAITING_LOGIN, "等待完整登录数据；晚启动可能需要重新登录，增量不视为完整。"
        items.append(FeatureCheck("packet_capture", "抓包同步", state, detail, actions, (
            ("npcap", probe.npcap_available), ("listening", probe.packet_listening),
            ("snapshot", probe.packet_snapshot),
        )))
    if probe.native_diagnostic and Capability.NATIVE_SYNC in allowed:
        items.append(FeatureCheck(
            "native_connection", "原生连接与业务检测", probe.native_diagnostic_state or CheckState.FAULT,
            probe.native_diagnostic, ("recheck",),
            (("game_running", probe.game_running), ("core_available", probe.core_available)),
        ))
    for capability, label, needs_snapshot in (
        (Capability.NATIVE_LOAD, "游戏内组件文件", False),
        (Capability.NATIVE_BATTLE, "DLL 战报", False),
        (Capability.NATIVE_EQUIPMENT, "DLL 装配", True),
    ):
        if capability in allowed:
            items.append(_native_check(capability.value, label, getattr(probe, capability.value), probe,
                                       needs_snapshot=needs_snapshot,
                                       external_provider_allowed=settings.mode == WorkMode.DEVELOPER))
    if Capability.NATIVE_SYNC in allowed:
        for feature, label in (
            ("native_character", "DLL 角色状态（已支持字段）"),
            ("native_inventory", "DLL 完整背包库存"),
            ("native_team", "DLL 队伍观测（已支持字段）"),
            ("native_environment", "DLL 环境观测（已支持字段）"),
        ):
            items.append(_native_check(feature, label, getattr(probe, feature), probe, needs_snapshot=True,
                                       external_provider_allowed=settings.mode == WorkMode.DEVELOPER))
    return WorkModeReport(
        settings.mode, tuple(items),
        sync_enable_ready=decide_sync_enable(settings, deployment_record or {}, probe).ready,
    )


def project_component_probe(
    local_probe: WorkModeProbe, *, files: bool, capabilities: Collection[str],
    package_ready: bool, package_issues: Collection[str], auto_error: str,
    settings: WorkModeSettings, cleanup_state: CheckState | None, cleanup_detail: str,
    path_valid: bool, running: bool | None, launcher_running: bool | None, launcher_error: str,
) -> WorkModeProbe:
    """解释 owner 已探测的文件和包事实，不探测、连接或部署组件。"""
    domains = {"native_" + name: NativeFeatureProbe(
        files=files and f"{name}.snapshot.v1" in capabilities,
        reason="packaged_capability_missing" if files and f"{name}.snapshot.v1" not in capabilities else "",
    ) for name in ("character", "inventory", "team", "environment")}
    battle_supported = {"combat.hit_buff.v1", "combat.context.v1"}.issubset(capabilities)
    equipment_supported = "equipment.execute.v1" in capabilities
    if auto_error:
        state, detail = CheckState.FAULT, auto_error
    elif not package_ready:
        state, detail = CheckState.MISSING, "；".join(package_issues) or "配套组件包尚未通过核对。"
    elif settings.pending_cleanup:
        state, detail = CheckState.CLEANUP_PENDING, cleanup_detail or "旧组件清理尚未完成，请先完成清理。"
    elif files:
        state, detail = CheckState.AVAILABLE, "当前配套组件已部署。"
    else:
        state, detail = CheckState.MISSING, "当前游戏内组件尚未部署或与组件包不兼容；请前往部署组件后重新检测。"
    return replace(local_probe, component_update_state=state, component_update_detail=detail,
        game_path_valid=path_valid, game_running=running, launcher_running=launcher_running,
        launcher_probe_error=launcher_error, core_available=package_ready,
        native_load=NativeFeatureProbe(files=files), **domains,
        native_battle=NativeFeatureProbe(files=files, supported=battle_supported,
            reason="" if battle_supported else "packaged_capability_missing"),
        native_equipment=NativeFeatureProbe(files=files, supported=equipment_supported,
            reason="" if equipment_supported else "packaged_capability_missing"),
        cleanup_detail=cleanup_detail, cleanup_state=cleanup_state)


def project_transport_probe(
    probe: WorkModeProbe, *, pipe: bool, handshake: bool | None = None,
) -> WorkModeProbe:
    return replace(probe, **{key: replace(getattr(probe, key), pipe=pipe, handshake=handshake)
        for key in ("native_battle", "native_equipment", "native_character", "native_inventory",
                    "native_team", "native_environment")})


def project_inspection_response(
    probe: WorkModeProbe, response: dict[str, Any], *, mode: WorkMode,
) -> WorkModeProbe:
    """仅投影本次完整成功响应；能力、正式 DTO、完整性和业务就绪各自独立。"""
    caps = response["hello"].get("capabilities", [])
    equipment = response.get("equipment") or {}
    inventory_ready = response.get("inventory_snapshot_ready") is True
    developer_live = mode == WorkMode.DEVELOPER
    # 开发模式的外部提供方按本次正式能力判断，不把包可选能力当作 live 能力。
    # 只改变成功诊断投影，不改文件归属、Core 整包门禁、模式权限或自动同步准备。
    equipment_package = probe.native_equipment
    equipment_reason = str(equipment.get("reason") or (
        "equipment_check_required" if response.get("equipment") is None else ""))
    values = {"native_equipment": replace(equipment_package, handshake=True,
        supported={"equipment", "native_equipment_v1"}.issubset(caps) and (
            developer_live or equipment_package.supported is True),
        ready=equipment.get("ready"), snapshot=inventory_ready, projection_complete=inventory_ready,
        reason=equipment_reason if developer_live else equipment_package.reason or equipment_reason)}
    battle = response["status"].get("native_status", {})
    values["native_battle"] = replace(probe.native_battle, handshake=True,
        supported={"native_hit_buff_v1", "battle_axis_v1", "native_context_observation_v1"}.issubset(caps)
        and (developer_live or probe.native_battle.reason != "packaged_capability_missing"),
        ready=battle.get("ready"), reason=str(battle.get("readyReason") or ""))
    domains = response["domains"].get("domains", [])
    domain_map = {item.get("domain"): item for item in domains if isinstance(item, dict)}
    domain_errors = response.get("domain_errors", {})
    for domain in ("character", "inventory", "team", "environment"):
        item = domain_map.get(domain, {})
        error = domain_errors.get(domain)
        previous = getattr(probe, "native_" + domain)
        values["native_" + domain] = replace(previous, handshake=True,
            supported=f"{domain}.snapshot.v1" in caps and (domain not in {"inventory", "character"} or
                ("native_inventory_dto_v1" if domain == "inventory" else "native_character_profile_v1") in caps),
            profile_projection_supported=domain == "character" and "native_character_profile_v1" in caps,
            ready=False if error else item.get("ready"),
            reason=previous.reason or (str(error.get("reason") or error["message"]) if error else
                                      str(item.get("readyReason") or "")),
            snapshot=bool(item.get("snapshotId")), complete=item.get("complete"),
            source_coverage=str(item.get("sourceCoverage") or "unknown"))
    return replace(probe, logged_in=battle.get("ready") is True, **values)


def project_inspection_failure(
    probe: WorkModeProbe, error: Exception, *, record: bool, pipe: bool | None = None,
) -> WorkModeProbe:
    # 无成功响应时丢弃局部业务事实，只保留本请求明确确认的握手；不借上次 ready。
    if pipe is not None:
        context = error.request_context if isinstance(error, NteCoreError) else None
        handshake = True if context is not None and context.handshake_confirmed else None
        probe = project_transport_probe(probe, pipe=pipe, handshake=handshake)
    return replace(probe, native_diagnostic=detection_failure_detail(error, record=record),
                   native_diagnostic_state=detection_failure_state(error))
