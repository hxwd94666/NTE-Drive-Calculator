# 编排游戏界面自动装配的确认、账号投影、后台执行和结果恢复。
"""Controller helpers for step-by-step game UI equipment assembly."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.inventory.equipment_assembly_dialogs import (
    assembly_report_dialog as _assembly_report_dialog,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.utils.logger import logger

from .equipment_plan_optimizer import _sqlite_plan_display_state
from .equipment_slot_selection_dialog import select_assembly_slot_ids


def _return_to_equipment_after_assembly(window: Any) -> None:
    """Restore the calculator window and return to the equipment page."""

    show_normal = getattr(window, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(window, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(window, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(window, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _prompt_protagonist_alias_if_needed(
    window: Any,
    role_names: list[str],
) -> dict[str, str]:
    roles = {str(role).strip() for role in (role_names or []) if str(role).strip()}
    protagonist_roles = roles.intersection({"主角", "零", "「零」"})
    if not protagonist_roles:
        return {}
    preferences = getattr(window, "_ui_preferences", {}) or {}
    default_name = str(
        preferences.get("protagonist_game_name")
        or getattr(window, "_drive_assembly_protagonist_name", "")
        or ""
    ).strip()
    if default_name:
        window._drive_assembly_protagonist_name = default_name
        return {role_name: default_name for role_name in protagonist_roles}

    dialog = QDialog(window)
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
        QMessageBox.warning(window, "主角名称", "需要输入主角在游戏中显示的名字。")
        return {}
    window._drive_assembly_protagonist_name = player_name
    if isinstance(preferences, dict):
        preferences["protagonist_game_name"] = player_name
        preferences["skip_protagonist_name_prompt"] = bool(dont_remind.isChecked())
        saver = getattr(window, "_save_ui_preferences", None)
        if callable(saver):
            saver()
    return {role_name: player_name for role_name in protagonist_roles}


def _account_database_path(window: Any) -> Path:
    app_context = getattr(window, "app_context", None)
    if app_context is None:
        explicit = getattr(window, "user_database_path", None)
        if explicit is None:
            raise RuntimeError("装配控制器缺少当前账号数据库依赖")
        return Path(explicit)
    return Path(app_context.account.user_database_path)


def _assembly_runtime_paths(window: Any) -> tuple[Path, Path]:
    """Return role-template and run-record roots for the active account."""

    app_context = getattr(window, "app_context", None)
    if app_context is None:
        template_dir = getattr(window, "role_template_dir", None)
        screenshot_dir = getattr(window, "screenshot_dir", None)
        if template_dir is None or screenshot_dir is None:
            raise RuntimeError("自动装配缺少角色模板或当前账号截图目录依赖")
        return Path(template_dir), Path(screenshot_dir) / "record"
    return (
        Path(app_context.paths.template_dir) / "roles",
        Path(app_context.account.screenshot_dir) / "record",
    )


def _sqlite_automatic_assembly_state(
    database_path: str | Path,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
) -> dict[str, dict[str, Any]]:
    """从 SQLite 已保存方案构建自动装配动作所需的只读投影。"""

    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        states: dict[str, dict[str, Any]] = {}
        if slot_ids:
            plans_by_role = [
                (selection.role_name, dict(selection.plan))
                for selection in LoadoutSlotSelectionService(user_dao).resolve(slot_ids)
            ]
        else:
            plans_by_role = [
                (selection.role_name, dict(selection.plan))
                for selection in LoadoutSlotSelectionService(user_dao).resolve_default_roles(role_names)
            ]
        for role_name, plan in plans_by_role:
            states[role_name] = _sqlite_plan_display_state(
                plan,
                user_dao,
                static_dao,
            )
    return states


def _start_automatic_equipment_assembly(
    window: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
) -> None:
    """在工作线程中执行逐步游戏界面自动装配。"""

    current_worker = getattr(window, "_automatic_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(
            window,
            "自动装配",
            "已有自动装配任务正在执行，请等待它结束。",
        )
        return
    try:
        state = _sqlite_automatic_assembly_state(
            _account_database_path(window),
            role_names,
            slot_ids=slot_ids,
        )
    except Exception as exc:
        QMessageBox.warning(window, "自动装配", f"无法读取官方 SQLite 方案：{exc}")
        return

    execution_role_names = list(state)
    aliases = _prompt_protagonist_alias_if_needed(window, execution_role_names)
    protagonist_names = {"主角", "零", "「零」"}
    if {str(role).strip() for role in execution_role_names}.intersection(
        protagonist_names
    ) and not aliases:
        return
    hotkey_manager = getattr(window, "global_hotkey_manager", None)
    configuration = getattr(hotkey_manager, "configuration", None)
    stop_hotkey = str(getattr(configuration, "stop", "全局停止键"))
    confirmation = QMessageBox.question(
        window,
        "自动装配准备",
        "将模拟游戏内操作逐步装配。请在 3 秒内切换到游戏的角色详情页，"
        f"并保持游戏窗口可见；执行期间可按设置中的全局停止键（{stop_hotkey}）停止。\n\n"
        "请保证游戏里的 C 键角色页面已打开，且游戏分辨率为 1080p 或 2K。",
        QMessageBox.Ok | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if confirmation != QMessageBox.Ok:
        return
    hotkey_owner = "automatic_equipment_apply"
    active_hotkey_owner = getattr(hotkey_manager, "active_owner", None)
    if active_hotkey_owner not in (None, hotkey_owner):
        QMessageBox.information(
            window,
            "自动装配",
            "当前全局停止键正由其他任务使用，请先停止该任务后再开始自动装配。",
        )
        return
    stop_requested = threading.Event()
    if hotkey_manager is not None:
        hotkey_manager.start(owner=hotkey_owner, on_stop=stop_requested.set)
    show_minimized = getattr(window, "showMinimized", None)
    if callable(show_minimized):
        show_minimized()

    def run() -> object:
        template_dir, record_root = _assembly_runtime_paths(window)
        if len(execution_role_names) == 1:
            return execute_selected_role_from_current_game_page(
                state,
                execution_role_names[0],
                template_dir=str(template_dir),
                record_root=record_root,
                role_name_aliases=aliases,
                should_stop=stop_requested.is_set,
            )
        return execute_all_roles_from_current_game_page(
            state,
            template_dir=str(template_dir),
            record_root=record_root,
            role_name_aliases=aliases,
            should_stop=stop_requested.is_set,
        )

    worker = WorkerThread(target=run, parent=window)
    window._automatic_equipment_apply_worker = worker

    def on_result(report: object) -> None:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        _return_to_equipment_after_assembly(window)
        title, message, completed = _assembly_report_dialog(
            "自动装配",
            report,
            len(execution_role_names),
        )
        (QMessageBox.information if completed else QMessageBox.warning)(
            window,
            title,
            message,
        )
        refresh = getattr(window, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        _return_to_equipment_after_assembly(window)
        QMessageBox.critical(
            window,
            "自动装配失败",
            f"自动装配未能完成：\n{message}",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_duplicate_warning(window: Any) -> bool:
    """Warn once that UI automation cannot resolve repeated drive placement."""

    preferences = getattr(window, "_ui_preferences", None)
    if isinstance(preferences, dict) and preferences.get(
        "skip_automatic_assembly_duplicate_warning"
    ):
        return True

    dialog = QMessageBox(window)
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
            window._ui_preferences = preferences
        preferences["skip_automatic_assembly_duplicate_warning"] = True
        saver = getattr(window, "_save_ui_preferences", None)
        if callable(saver):
            try:
                saver()
            except Exception as exc:
                logger.warning(f"保存自动装配提示偏好失败: {exc}")
    return True


def _preview_automatic_assemble_role(
    window: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
    confirmed: bool = False,
) -> None:
    """确认后通过游戏界面自动化装配一个角色。"""

    if not confirmed:
        result = QMessageBox.question(
            window,
            "自动装配",
            f"将模拟游戏内操作逐步装配 [{role_name}]。\n\n"
            "不需要装备插件，但需切换到游戏角色详情页，耗时更长；"
            "执行期间可按设置中的全局停止键停止。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
    if not _confirm_automatic_assembly_duplicate_warning(window):
        return
    _start_automatic_equipment_assembly(
        window,
        [role_name] if slot_id is None else [],
        slot_ids=[int(slot_id)] if slot_id is not None else None,
    )


def _preview_automatic_assemble_all_roles(
    window: Any,
    role_names: list[str] | None = None,
) -> None:
    """确认后通过游戏界面自动化装配全部已保存角色。"""

    requested_roles = tuple(
        dict.fromkeys(str(name) for name in (role_names or ()))
    )
    try:
        with UserDataDao(_account_database_path(window)) as user_dao:
            selection_service = LoadoutSlotSelectionService(user_dao)
            current_slots = selection_service.list_current()
            if requested_roles:
                available_roles = {selection.role_name for selection in current_slots}
                missing = [name for name in requested_roles if name not in available_roles]
                if missing:
                    QMessageBox.information(
                        window,
                        "自动装配",
                        f"以下角色尚未保存当前方案：{'、'.join(missing)}",
                    )
                    return
                current_slots = tuple(
                    selection
                    for selection in current_slots
                    if selection.role_name in requested_roles
                )
            selected_slot_ids = select_assembly_slot_ids(window, current_slots)
            if selected_slot_ids is None:
                return
            selections = selection_service.resolve(selected_slot_ids)
    except Exception as exc:
        QMessageBox.warning(
            window,
            "自动装配",
            f"无法读取官方 SQLite 方案：{exc}",
        )
        return
    if not selections:
        QMessageBox.information(
            window,
            "自动装配",
            "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。",
        )
        return
    result = QMessageBox.question(
        window,
        "自动装配",
        f"将模拟游戏内操作，依次装配 {len(selected_slot_ids)} 个角色。\n\n"
        "无需装备插件，但需切换到游戏角色详情页，耗时更长；"
        "执行期间可按设置中的全局停止键停止。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if (
        result == QMessageBox.Yes
        and _confirm_automatic_assembly_duplicate_warning(window)
    ):
        _start_automatic_equipment_assembly(window, [], slot_ids=selected_slot_ids)
