# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QLineEdit, QListView, QMessageBox, QProgressDialog, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, current_style_sheet, theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.role.replacement_service import (
    build_equipment_role_context,
    rank_replacement_candidates_by_damage,
)
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.features.inventory.warehouse import (
    WarehouseCardDelegate,
    WarehouseGridView,
    WarehouseInventoryModel,
    filter_warehouse_items,
    load_warehouse_snapshot,
    warehouse_item_compare_category,
    warehouse_item_with_state,
    warehouse_item_view,
    warehouse_type_options,
)
from src.features.identification.page import build_identify_result_row
from src.features.scanning.post_action_dialog import (
    load_scan_post_action_config,
    show_scan_post_action_dialog,
)
from src.features.scanning.post_actions import validate_post_action_config
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.warehouse_identification_service import WarehouseIdentificationService
from src.services.warehouse_state_management import WarehouseStateManagementService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger
from src.ui.main_window_method_install import install_methods as _install_main_window_methods
from .equipment_display_controller import _sqlite_plan_display_state


def set_bonus_from_tape_source(source) -> dict:
    """Build a safe placeholder while tape-set bonus details move to SQLite."""
    if isinstance(source, dict):
        set_name = str(source.get("set_name", "") or "")
    else:
        set_name = str(getattr(source, "set_name", "") or "")
    return {"display_name": set_name, "skill": {}, "skill_2": {}, "skill_cover": 0.8}

__all__ = ['_equipment_compare_signature', '_same_equipment_by_ocr', '_page_equipment', '_refresh_equip',
           '_page_warehouse', '_refresh_warehouse', '_apply_warehouse_filters', '_on_warehouse_sync_state',
           '_on_warehouse_selection_changed', '_set_warehouse_selected_state', '_toggle_warehouse_item_state', '_save_warehouse_state_changes',
           '_show_warehouse_item_identification', '_update_warehouse_save_state', '_on_warehouse_manual_plan_ready', '_open_warehouse_state_manager',
           '_on_warehouse_state_plan_ready', '_on_warehouse_state_applied', '_on_warehouse_state_error',
           '_set_warehouse_management_busy',
           '_saved_plan_diff_text', '_show_saved_plan_diff_dialog', '_clear_all_equipment', '_delete_role_equipment', '_optimize_saved_equipment',
           '_preview_assemble_role', '_preview_fast_assemble_all_roles', '_preview_automatic_assemble_all_roles']

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力", "AtkUp": "攻击力%", "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%", "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%", "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%", "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%", "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%", "DefAdd": "防御力", "DefUp": "防御力%",
    "HealUp": "治疗加成", "HPMaxAdd": "生命值", "HPMaxUp": "生命值%",
    "MagBase": "环合强度", "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2", "hen3": "H_3", "hen4": "H_4", "shu2": "V_2",
    "shu3": "V_3", "shu4": "V_4", "z3": "Trap_4_H", "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL", "zhijiao2": "L_3_TL", "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _assembly_report_dialog(action_name: str, report, expected_role_count: int | None = None):
    """Build a completion/warning dialog from a game assembly execution report."""
    role_count = len(getattr(report, "role_reports", []) or [])
    action_count = getattr(report, "executed_actions", 0)
    missing = list(getattr(report, "missing_roles", []) or [])
    skipped = list(getattr(report, "skipped_roles", []) or [])
    duplicates = list(getattr(report, "duplicate_roles", []) or [])
    unrecognized = list(getattr(report, "unrecognized_roles", []) or [])
    verification_failures = list(getattr(report, "verification_failures", []) or [])

    # The roster scan crosses non-target roles.  Their OCR result is useful
    # diagnostics, but cannot invalidate an assembly once every requested role
    # has been found and executed.  A target recognition failure is already
    # represented by ``missing``.
    incomplete = bool(missing or skipped or duplicates or verification_failures)
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = f"{action_name}未完成" if incomplete else f"{action_name}完成"
    lines = [f"已装配 {role_count} 个角色，执行 {action_count} 个动作。"]
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(f"预计装配 {expected_role_count} 个角色，还有 {expected_role_count - role_count} 个未完成。")
    if missing:
        lines.append("未找到角色：" + "、".join(str(role) for role in missing))
    if skipped:
        lines.append("跳过角色：" + "、".join(str(role) for role in skipped))
    if duplicates:
        lines.append(f"重复识别角色槽位：{len(duplicates)} 个。")
    if unrecognized:
        lines.append(f"未识别角色槽位：{len(unrecognized)} 个。")
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = f"第 {int(entry['roster_index']) + 1} 个角色"
            elif entry.get("page_index") is not None and entry.get("slot_index") is not None:
                position = f"第 {int(entry['page_index']) + 1} 页第 {int(entry['slot_index']) + 1} 个角色"
            else:
                position = "未知位置"
            raw_text = str(entry.get("raw_text") or "").strip() or "未读取到文字"
            lines.append(f"- {position}（OCR：{raw_text}）")
    if verification_failures:
        lines.append(f"图纸截图校验失败：{len(verification_failures)} 个。")
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or "未知角色")
            block_ids = [
                str(item.get("block_id"))
                for item in (failure.get("missing_blocks") or [])
                if isinstance(item, dict) and item.get("block_id") is not None
            ]
            if block_ids:
                lines.append(f"- {role_name}：未通过校验的驱动块 #{'、#'.join(block_ids)}")
    if incomplete:
        lines.append("请检查角色识别结果后重新执行。")
    elif unrecognized:
        lines.append("其余未识别槽位不属于本次目标角色，不影响本次装配结果。")
    return title, "\n".join(lines), not incomplete


def _return_to_equipment_after_assembly(self) -> None:
    """Restore the calculator window and return to the equipment page."""
    show_normal = getattr(self, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(self, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(self, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(self, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _prompt_protagonist_alias_if_needed(self, role_names) -> dict[str, str]:
    roles = {str(role).strip() for role in (role_names or []) if str(role).strip()}
    protagonist_roles = roles.intersection({"主角", "零", "「零」"})
    if not protagonist_roles:
        return {}
    preferences = getattr(self, "_ui_preferences", {}) or {}
    default_name = str(
        preferences.get("protagonist_game_name")
        or getattr(self, "_drive_assembly_protagonist_name", "")
        or ""
    ).strip()
    if default_name:
        self._drive_assembly_protagonist_name = default_name
        return {role_name: default_name for role_name in protagonist_roles}

    dialog = QDialog(self)
    dialog.setWindowTitle("主角名称")
    dialog.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("零在游戏内显示为玩家名字，请输入该名字后继续自动装配。"))
    name_edit = QLineEdit()
    name_edit.setPlaceholderText("游戏内主角名字")
    layout.addWidget(name_edit)
    dont_remind = QCheckBox("记住此名字，不再提醒")
    layout.addWidget(dont_remind)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return {}
    player_name = name_edit.text().strip()
    if not player_name:
        QMessageBox.warning(self, "主角名称", "需要输入主角在游戏中显示的名字。")
        return {}
    self._drive_assembly_protagonist_name = player_name
    if isinstance(preferences, dict):
        preferences["protagonist_game_name"] = player_name
        preferences["skip_protagonist_name_prompt"] = bool(dont_remind.isChecked())
        saver = getattr(self, "_save_ui_preferences", None)
        if callable(saver):
            saver()
    return {role_name: player_name for role_name in protagonist_roles}


def _is_equipment_plugin_unavailable_error(error: object) -> bool:
    """识别核心已启动但游戏内装备插件桥接不可用的不可重试错误。"""

    return "EQUIPMENT_PLUGIN_UNAVAILABLE" in str(error)


def _run_nte_core_equipment_apply(
    self,
    role_names: list[str],
    *,
    identity_overrides: dict[str, dict] | None = None,
    job_id: int | None = None,
    progress_callback=None,
) -> dict:
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None:
        raise RuntimeError("背包同步服务尚未启动，请先在首页启动后台同步")

    identity_overrides = identity_overrides or {}
    applied: list[dict] = []
    with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
        apply_service = EquipmentApplyService(user_dao, sync_service)
        if job_id is not None:
            job = user_dao.get_equipment_apply_job(job_id)
            if job is None:
                raise RuntimeError(f"装配任务 {job_id} 不存在")
            user_dao.reset_failed_equipment_apply_job_items(job_id)
            prepared = [
                ({
                    "job_item_id": row["job_item_id"],
                    "role_name": row["role_name"],
                    "character_id": row["character_id"],
                    "character_uid": row["character_uid"],
                    "plan_id": row["plan_id"],
                } | {
                    "module_count": sum(
                        1 for assignment in (user_dao.get_loadout_plan(row["plan_id"]) or {}).get("assignments", [])
                        if assignment["kind"] == "module"
                    ),
                    "core_count": sum(
                        1 for assignment in (user_dao.get_loadout_plan(row["plan_id"]) or {}).get("assignments", [])
                        if assignment["kind"] == "core"
                    ),
                })
                for row in job["items"] if row["status"] in {"pending", "running", "failed"}
            ]
            if not prepared:
                return {"job_id": job_id, "applied": [], "completed": job["status"] == "completed"}
        else:
            initial_snapshot_id = user_dao.current_inventory_snapshot_id()
            if initial_snapshot_id is None:
                raise RuntimeError("用户数据库中还没有稳定背包快照")

            # 必须在第一条装配指令前缓存全部角色 UID。后续角色可能因装备被前面的
            # 方案移走而暂时全身为空，此时再从当前快照解析会失败。
            prepared: list[dict] = []
            identity_requests: list[dict] = []
            for role_name in role_names:
                plan = user_dao.get_active_loadout_plan_for_role(role_name)
                if plan is None:
                    raise RuntimeError(
                        f"装配前检查 [{role_name}] 失败，尚未发送任何装配指令："
                        "没有来自官方背包快照的已保存方案，请重新计算并保存。"
                    )
                source_snapshot_id = plan.get("source_snapshot_id")
                source_summary = (
                    user_dao.inventory_snapshot_summary(int(source_snapshot_id))
                    if source_snapshot_id is not None else None
                )
                if source_summary is None or source_summary.get("source") != "nte_core":
                    raise RuntimeError(
                        f"装配前检查 [{role_name}] 失败，视觉扫描库存没有本地组件可用的原生 UID。"
                        "请改用自动装配；极速装配仅支持抓包稳定快照。"
                    )
                override = identity_overrides.get(role_name)
                try:
                    character_id = int(override["character_id"]) if override else int(plan["character_id"])
                    if character_id != int(plan["character_id"]):
                        raise RuntimeError("手动选择的角色 ID 与该 SQLite 方案不匹配")
                    character_uid = apply_service.resolve_character_uid(
                        character_id, initial_snapshot_id,
                        explicit_uid=override.get("character_uid") if override else None,
                    )
                except Exception as exc:
                    identity_requests.append(
                        {
                            "role_name": role_name,
                            "candidate_character_ids": [int(plan["character_id"])],
                            "reason": str(exc),
                        }
                    )
                    continue
                prepared.append({
                    "role_name": role_name, "character_id": character_id,
                    "character_uid": character_uid, "plan_id": plan["plan_id"],
                    "module_count": sum(1 for row in plan["assignments"] if row["kind"] == "module"),
                    "core_count": sum(1 for row in plan["assignments"] if row["kind"] == "core"),
                })
            if identity_requests:
                return {"identity_requests": identity_requests}
            job_id = user_dao.create_equipment_apply_job(initial_snapshot_id, prepared)
            for entry, prepared_role in zip(user_dao.get_equipment_apply_job(job_id)["items"], prepared):
                prepared_role["job_item_id"] = entry["job_item_id"]

        # A game-side equipment operation can temporarily move the capture
        # listener out of ``listening``.  Validate and pin one login-time
        # snapshot for the whole job; checking that volatile state again for
        # every following role incorrectly aborts an otherwise valid job.
        stable_snapshot_id = apply_service.require_stable_snapshot()
        _report_fast_apply_progress(
            progress_callback,
            current=0,
            total=len(prepared),
            message="正在检查已保存方案…",
        )
        for index, prepared_role in enumerate(prepared, start=1):
            role_name = prepared_role["role_name"]
            _report_fast_apply_progress(
                progress_callback,
                current=index - 1,
                total=len(prepared),
                message=f"正在下发 [{role_name}] 的装配指令…",
            )
            user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="running")
            try:
                result = apply_service.apply_plan(
                    prepared_role["plan_id"],
                    character_uid=prepared_role["character_uid"],
                    timeout=30.0,
                    verify_after_dispatch=False,
                    stable_snapshot_id=stable_snapshot_id,
                )
                user_dao.mark_equipment_apply_job_item(
                    prepared_role["job_item_id"], status="succeeded",
                    before_snapshot_id=result.before_snapshot_id,
                    after_snapshot_id=result.after_snapshot_id,
                    verified=result.verified,
                )
                applied.append(
                    {
                        "role_name": role_name,
                        "character_id": prepared_role["character_id"],
                        "plan_id": prepared_role["plan_id"],
                        "module_count": prepared_role.get("module_count"),
                        "core_count": prepared_role.get("core_count"),
                        "snapshot_id": result.after_snapshot_id,
                        "verified": result.verified,
                        "already_applied": result.already_applied,
                    }
                )
                _report_fast_apply_progress(
                    progress_callback,
                    current=index,
                    total=len(prepared),
                    message=(
                        f"[{role_name}] 已确认"
                        if result.verified else f"[{role_name}] 指令已下发"
                    ),
                )
            except Exception as exc:
                user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="failed", error=str(exc))
                _report_fast_apply_progress(
                    progress_callback,
                    current=index - 1,
                    total=len(prepared),
                    message=f"[{role_name}] 下发失败",
                )
                return {"job_id": job_id, "applied": applied, "failed_role": role_name, "error": str(exc), "completed": False}
        completed = user_dao.complete_equipment_apply_job_if_done(job_id)
    return {"job_id": job_id, "applied": applied, "completed": completed}


def _report_fast_apply_progress(progress_callback, *, current: int, total: int, message: str) -> None:
    """Safely bridge worker-side apply state to the UI progress dialog."""
    if not callable(progress_callback):
        return
    try:
        progress_callback({
            "current": max(0, int(current)),
            "total": max(1, int(total)),
            "message": str(message),
        })
    except Exception:
        # Progress UI must never make a successful equipment request fail.
        logger.debug("极速装配进度回调失败", exc_info=True)


def _prompt_character_identity_requests(self, requests: list[dict]) -> dict[str, dict] | None:
    overrides: dict[str, dict] = {}
    for request in requests:
        role_name = request["role_name"]
        choices = [str(value) for value in request.get("candidate_character_ids") or []]
        if not choices:
            QMessageBox.warning(self, "角色实例", f"[{role_name}] 没有可选的官方角色 ID。")
            return None
        character_id, ok = QInputDialog.getItem(
            self, "选择角色实例", f"[{role_name}] 无法自动确定身份。\n原因：{request['reason']}\n\n请选择官方角色 ID：", choices, 0, False,
        )
        if not ok:
            return None
        uid_text, ok = QInputDialog.getText(
            self, "输入角色实例 UID", f"请输入 [{role_name}] 的实例 UID（slot,serial）：", QLineEdit.Normal,
        )
        if not ok:
            return None
        try:
            slot_text, serial_text = [value.strip() for value in str(uid_text).split(",", 1)]
            uid = {"slot": int(slot_text), "serial": int(serial_text)}
            with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
                user_dao.upsert_character_instance_mapping(int(character_id), uid, source="manual")
        except Exception as exc:
            QMessageBox.warning(self, "角色实例", f"实例 UID 无效或无法保存：{exc}")
            return None
        overrides[role_name] = {"character_id": int(character_id), "character_uid": uid}
    return overrides


def _start_nte_core_equipment_apply(self, role_names: list[str], *, identity_overrides: dict[str, dict] | None = None, job_id: int | None = None) -> None:
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "正在装配", "已有装配任务正在执行，请等待指令下发完成。")
        return

    progress_state = {
        "current": 0,
        "total": max(1, len(role_names)),
        "message": "正在准备极速装配…",
    }
    progress_dialog = QProgressDialog(
        progress_state["message"], "", 0, progress_state["total"], self,
    )
    progress_dialog.setWindowTitle("极速装配进度")
    progress_dialog.setWindowModality(Qt.WindowModal)
    progress_dialog.setCancelButton(None)
    progress_dialog.setAutoClose(False)
    progress_dialog.setAutoReset(False)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.setValue(0)
    progress_dialog.show()

    progress_timer = QTimer(progress_dialog)

    def update_progress_dialog() -> None:
        total = max(1, int(progress_state.get("total", 1)))
        progress_dialog.setMaximum(total)
        progress_dialog.setValue(min(total, max(0, int(progress_state.get("current", 0)))))
        progress_dialog.setLabelText(str(progress_state.get("message") or "正在极速装配…"))

    progress_timer.timeout.connect(update_progress_dialog)
    progress_timer.start(80)

    def update_progress(payload: dict) -> None:
        progress_state.update(payload)

    def close_progress_dialog() -> None:
        progress_timer.stop()
        progress_dialog.close()
        progress_dialog.deleteLater()

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(
            self,
            role_names,
            identity_overrides=identity_overrides,
            job_id=job_id,
            progress_callback=update_progress,
        ),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        close_progress_dialog()
        requests = report.get("identity_requests") or []
        if requests:
            overrides = _prompt_character_identity_requests(self, requests)
            if overrides is not None:
                _start_nte_core_equipment_apply(self, role_names, identity_overrides=overrides)
            return
        applied = report.get("applied") or []
        details = "\n".join(
            f"• {row['role_name']}"
            + (
                f"：{row['module_count']} 个驱动"
                + (" + 1 个核心" if row.get("core_count") else "")
                if row.get("module_count") is not None else "：已下发"
            )
            + ("（原本已装好）" if row.get("already_applied") else "")
            for row in applied
        )
        changed_count = sum(not row.get("already_applied") for row in applied)
        unchanged_count = len(applied) - changed_count
        unverified_count = sum(
            not row.get("verified", False) and not row.get("already_applied")
            for row in applied
        )
        summary = (
            f"已下发 {len(applied)} 个角色的配装"
            if unverified_count
            else f"已确认 {len(applied)} 个角色的配装"
        )
        if unchanged_count:
            summary += f"（实际装配 {changed_count} 个，原本已装好 {unchanged_count} 个）"
        if report.get("failed_role"):
            error_message = str(report.get("error") or "未知错误")
            if _is_equipment_plugin_unavailable_error(error_message):
                QMessageBox.warning(
                    self,
                    "装备插件不可用",
                    f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n"
                    "本地核心组件已连接，但未能连接游戏内装备插件（命名管道不可用或超时）。\n\n"
                    "请先确认：\n"
                    "1. 当前运行的 HTGame.exe 已加载与本地核心组件版本匹配的装备插件；\n"
                    "2. 游戏保持登录，随后从首页重新启动背包同步并等待“后台监听”；\n"
                    "3. 完成上述检查后，再点击右上角“极速装配”重新执行。\n\n"
                    f"此前已确认 {len(applied)} 个角色；任务日志已保存。此次不会立即重试。",
                )
                return
            retry = QMessageBox.question(
                self, "装配暂停",
                f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n{error_message}\n\n"
                f"此前已确认 {len(applied)} 个角色；任务日志已保存。是否重试失败角色并继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                _start_nte_core_equipment_apply(self, [], job_id=report["job_id"])
            return
        verification_note = (
            "\n\n当前游戏内不会产生新的稳定背包快照；本次已完成装配前校验并下发指令。"
            "请在下次登录后完成背包同步，以更新仓库显示。"
            if unverified_count else ""
        )
        QMessageBox.information(self, "装配完成", f"{summary}。\n任务 #{report.get('job_id')} 已保存日志。\n\n{details}{verification_note}")
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        close_progress_dialog()
        QMessageBox.critical(
            self,
            "装配失败",
            f"本地组件未能完成装配：\n{message}\n\n"
            "请确认游戏已登录、插件已加载，且首页背包同步处于“后台监听”。",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _preview_nte_core_assemble_role(self, role_name: str, *, confirmed: bool = False) -> None:
    """确认后通过装备插件极速装配一个已保存角色方案。"""

    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            source_snapshot_id = plan.get("source_snapshot_id") if plan else None
            source = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)).get("source")
                if source_snapshot_id is not None
                and user_dao.inventory_snapshot_summary(int(source_snapshot_id)) is not None
                else None
            )
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取已保存方案：{exc}")
        return
    if source == "gamepad":
        QMessageBox.information(
            self,
            "切换自动装配",
            "未找到抓包稳定快照。视觉扫描库存不包含本地组件所需的原生 UID，"
            "将改用逐步自动装配。",
        )
        _preview_automatic_assemble_role(self, role_name, confirmed=confirmed)
        return

    if confirmed:
        _start_nte_core_equipment_apply(self, [role_name])
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将通过游戏内装备插件把 [{role_name}] 的已保存方案直接装入游戏。\n\n"
        "若当前已经是目标配装会立即完成，否则发送指令并等待稳定背包快照确认；"
        "不需要切换到游戏配装页面。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, [role_name])


def _preview_nte_core_assemble_all_roles(
    self, *, confirmed: bool = False, role_names: list[str] | None = None,
) -> None:
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plans_by_role = user_dao.list_active_loadout_plans_by_role()
            if requested_roles:
                missing = [name for name in requested_roles if name not in plans_by_role]
                if missing:
                    QMessageBox.information(
                        self, "极速装配", f"以下角色尚未保存当前方案：{'、'.join(missing)}",
                    )
                    return
                plans_by_role = {name: plans_by_role[name] for name in requested_roles}
            nte_roles = []
            visual_roles = []
            for role_name, plan in plans_by_role.items():
                snapshot_id = plan.get("source_snapshot_id")
                summary = user_dao.inventory_snapshot_summary(int(snapshot_id)) if snapshot_id is not None else None
                if summary and summary.get("source") == "nte_core":
                    nte_roles.append(role_name)
                elif summary and summary.get("source") == "gamepad":
                    visual_roles.append(role_name)
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if nte_roles:
        role_names = list(nte_roles) if requested_roles else sorted(nte_roles)
    elif visual_roles:
        QMessageBox.information(
            self,
            "切换自动装配",
            "未找到抓包稳定快照。视觉扫描库存不包含本地组件所需的原生 UID，"
            "将改用逐步自动装配。",
        )
        _preview_automatic_assemble_all_roles(
            self, role_names=list(requested_roles) if requested_roles else None,
        )
        return
    else:
        QMessageBox.information(self, "极速装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    if confirmed:
        _start_nte_core_equipment_apply(self, role_names)
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将依次向本地组件发送 {len(role_names)} 个角色的装配指令，"
        "已经正确装配的角色会直接跳过，其余角色在稳定背包快照确认后再处理下一个。"
        "\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, role_names)


def _preview_fast_assemble_all_roles(self, role_names: list[str] | None = None) -> None:
    """从配装页右上角启动全部角色的极速装配。"""

    _preview_nte_core_assemble_all_roles(self, role_names=role_names)


def _sqlite_automatic_assembly_state(role_names: list[str]) -> dict[str, dict]:
    """从 SQLite 已保存方案构建自动装配动作所需的只读投影。"""

    with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
        states: dict[str, dict] = {}
        for role_name in role_names:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError(f"[{role_name}] 没有来自官方背包快照的已保存方案")
            states[role_name] = _sqlite_plan_display_state(plan, user_dao, static_dao)
    return states


def _start_automatic_equipment_assembly(self, role_names: list[str]) -> None:
    """在工作线程中执行逐步游戏界面自动装配。"""

    current_worker = getattr(self, "_automatic_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "自动装配", "已有自动装配任务正在执行，请等待它结束。")
        return
    try:
        state = _sqlite_automatic_assembly_state(role_names)
    except Exception as exc:
        QMessageBox.warning(self, "自动装配", f"无法读取官方 SQLite 方案：{exc}")
        return

    aliases = _prompt_protagonist_alias_if_needed(self, role_names)
    # 主角在不同存档中可能保存为“主角”“零”或“「零」”。未取得玩家填写的
    # 游戏内名称时不能继续，否则角色识别会把后续点击落在未知对象上。
    if {str(role).strip() for role in role_names}.intersection({"主角", "零", "「零」"}) and not aliases:
        return
    QMessageBox.information(
        self,
        "自动装配准备",
        "将模拟游戏内操作逐步装配。请在 3 秒内切换到游戏的角色详情页，"
        "并保持游戏窗口可见；执行期间可按 F12 停止。",
    )
    show_minimized = getattr(self, "showMinimized", None)
    if callable(show_minimized):
        show_minimized()

    def run() -> object:
        if len(role_names) == 1:
            return execute_selected_role_from_current_game_page(
                state, role_names[0], role_name_aliases=aliases,
            )
        return execute_all_roles_from_current_game_page(state, role_name_aliases=aliases)

    worker = WorkerThread(target=run, parent=self)
    self._automatic_equipment_apply_worker = worker

    def on_result(report: object) -> None:
        _return_to_equipment_after_assembly(self)
        title, message, completed = _assembly_report_dialog("自动装配", report, len(role_names))
        (QMessageBox.information if completed else QMessageBox.warning)(self, title, message)
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        _return_to_equipment_after_assembly(self)
        QMessageBox.critical(self, "自动装配失败", f"自动装配未能完成：\n{message}")

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_duplicate_warning(self) -> bool:
    """Warn once that UI automation cannot resolve repeated drive placement."""
    preferences = getattr(self, "_ui_preferences", None)
    if isinstance(preferences, dict) and preferences.get("skip_automatic_assembly_duplicate_warning"):
        return True

    dialog = QMessageBox(self)
    dialog.setWindowTitle("自动装配提示")
    dialog.setIcon(QMessageBox.Warning)
    dialog.setText("自动装配无法完美处理重复驱动情况。")
    dialog.setInformativeText("运行结束后，请自行填补因重复驱动产生的空缺。")
    dialog.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    dialog.setDefaultButton(QMessageBox.Cancel)
    dont_remind = QCheckBox("不再提醒")
    dialog.setCheckBox(dont_remind)
    confirm_button = dialog.button(QMessageBox.Ok)
    dialog.exec()
    if dialog.clickedButton() is not confirm_button:
        return False
    if dont_remind.isChecked():
        if not isinstance(preferences, dict):
            preferences = {}
            self._ui_preferences = preferences
        preferences["skip_automatic_assembly_duplicate_warning"] = True
        saver = getattr(self, "_save_ui_preferences", None)
        if callable(saver):
            try:
                saver()
            except Exception as exc:
                logger.warning(f"保存自动装配提示偏好失败: {exc}")
    return True


def _preview_automatic_assemble_role(self, role_name: str, *, confirmed: bool = False) -> None:
    """确认后通过游戏界面自动化装配一个角色。"""

    if not confirmed:
        ret = QMessageBox.question(
            self,
            "自动装配",
            f"将模拟游戏内操作逐步装配 [{role_name}]。\n\n"
            "不需要装备插件，但需切换到游戏角色详情页，耗时更长；执行期间可按 F12 停止。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
    if not _confirm_automatic_assembly_duplicate_warning(self):
        return
    _start_automatic_equipment_assembly(self, [role_name])


def _preview_automatic_assemble_all_roles(
    self, role_names: list[str] | None = None,
) -> None:
    """确认后通过游戏界面自动化装配全部已保存角色。"""

    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plans_by_role = user_dao.list_active_loadout_plans_by_role()
    except Exception as exc:
        QMessageBox.warning(self, "自动装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if requested_roles:
        missing = [name for name in requested_roles if name not in plans_by_role]
        if missing:
            QMessageBox.information(
                self, "自动装配", f"以下角色尚未保存当前方案：{'、'.join(missing)}",
            )
            return
        role_names = list(requested_roles)
    else:
        role_names = sorted(plans_by_role)
    if not role_names:
        QMessageBox.information(self, "自动装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    ret = QMessageBox.question(
        self,
        "自动装配",
        f"将模拟游戏内操作，依次装配 {len(role_names)} 个角色。\n\n"
        "无需装备插件，但需切换到游戏角色详情页，耗时更长；执行期间可按 F12 停止。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        if _confirm_automatic_assembly_duplicate_warning(self):
            _start_automatic_equipment_assembly(self, role_names)


def _select_single_role_assembly_mode(self, role_name: str) -> str | None:
    """让用户为一个角色显式选择极速或自动装配。"""

    dialog = QMessageBox(self)
    dialog.setWindowTitle("选择装配方式")
    dialog.setIcon(QMessageBox.Question)
    # QMessageBox 会根据标签内容重新收缩；同时设置标签最小宽度和初始尺寸，
    # 确保两种装配方式的说明不会挤在窄弹窗里。
    dialog.setMinimumSize(720, 400)
    dialog.setStyleSheet(
        "QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:620px;}"
    )
    dialog.setText(f"为 [{role_name}] 选择装配方式")
    dialog.setInformativeText(
        "极速装配：通过游戏内装备插件直接写入方案，速度快，无需打开配装页。\n\n"
        "自动装配：模拟游戏内操作逐步完成，无需装备插件，但需停在角色详情页且耗时更长。"
    )
    fast_button = dialog.addButton("极速装配", QMessageBox.ActionRole)
    automatic_button = dialog.addButton("自动装配", QMessageBox.ActionRole)
    dialog.addButton(QMessageBox.Cancel)
    dialog.resize(720, 400)
    dialog.exec()
    if dialog.clickedButton() is fast_button:
        return "fast"
    if dialog.clickedButton() is automatic_button:
        return "automatic"
    return None


def _preview_assemble_role(self, role_name: str) -> None:
    """为单个角色显示装配方式选择。"""

    mode = _select_single_role_assembly_mode(self, role_name)
    if mode == "fast":
        _preview_nte_core_assemble_role(self, role_name, confirmed=True)
    elif mode == "automatic":
        _preview_automatic_assemble_role(self, role_name, confirmed=True)

