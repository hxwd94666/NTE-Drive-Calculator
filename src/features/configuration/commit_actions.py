# 编排基础权重页面的后台提交、草稿保留和提交后的异步刷新。
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .page_owner import BasicWeightPage

from PySide6.QtWidgets import QInputDialog, QMessageBox

from .dependencies import freeze_basic_weight_dependencies


_DIRTY_FIELDS = (
    "_config_dirty_character_ids", "_config_dirty_shape_bonus_ids",
    "_config_dirty_board_ids", "_config_dirty_target_suit_ids",
)


def _clear_draft(page, *, discard=False):
    page._config_dirty = False
    for field in _DIRTY_FIELDS:
        getattr(page, field, set()).clear()
    if discard:
        page._config_form_data = None
        page._config_loaded_model = None


def _submit(page, work, done, *, title, completion=None):
    controller = page.controller()
    if controller.is_writing():
        return False
    status = getattr(page, "config_load_status", None)
    if page is not None:
        page.setEnabled(False)
    if status is not None:
        status.setText(f"正在{title}…")

    def current():
        return (controller is getattr(page, "_basic_weight_controller", None)
                and controller.dependencies == freeze_basic_weight_dependencies(page.app_context))

    def unlock():
        if current():
            if page is not None:
                page.setEnabled(True)
            if status is not None:
                status.clear()

    def committed(value):
        unlock()
        if current():
            done(value)
            if completion is not None:
                completion(True)
        elif completion is not None:
            completion(False)

    def failed(error):
        unlock()
        if current():
            QMessageBox.warning(page, f"{title}未完成", f"编辑已保留；请核对已保存内容后重试。\n{error}")
            if completion is not None:
                completion(False)
        elif completion is not None:
            completion(False)

    def application_failed(error):
        unlock()
        if current():
            QMessageBox.warning(page, "提交后刷新失败", f"数据已提交，请重新进入页面刷新；不要重复保存。\n{error}")
            if completion is not None:
                completion(False)

    return controller.submit_change(work, committed, failed, application_failed)


def save_config_form(page: BasicWeightPage, *, completion=None, show_message=True):
    if getattr(page, "_current_config_name", None) != "account_weights":
        return False
    controller = page.controller()
    data = deepcopy(getattr(page, "_config_form_data", {}) or {})
    fields = tuple(set(getattr(page, field, set())) for field in _DIRTY_FIELDS)

    def done(_value):
        _clear_draft(page)
        # 提交成功只清草稿标记，不另读“最新”状态重新解释本次提交。
        if show_message:
            QMessageBox.information(page, "保存", "角色权重设置已保存。")

    return _submit(page, lambda: controller.save_changes(data, *fields), done,
                   title="保存", completion=completion)


def _reload_after_weight_reset(page, active_role):
    from .page import switch_config_form
    _clear_draft(page, discard=True)
    switch_config_form(page, active_role=active_role)


def _confirm_weight_reset(page, message):
    if (getattr(page, "_current_config_name", None) != "account_weights"
            or page.controller().is_writing()):
        return False
    return QMessageBox.question(page, "确认重置权重", message,
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes


def reset_current_config_weights(page: BasicWeightPage):
    name = str(getattr(page, "_config_active_role", "") or "")
    role = (getattr(page, "_config_form_data", {}) or {}).get(name) or {}
    if not name or not role:
        QMessageBox.information(page, "重置当前", "请先选择一个角色。")
        return
    display_name = str(role["name_zh"])
    if role.get("is_custom"):
        QMessageBox.information(page, "重置当前", "自建角色没有发行默认权重，保留当前自定义值。")
        return
    if not _confirm_weight_reset(page,
        f"将清除当前账号中 [{display_name}] 的自定义卡带主词条和驱动副词条权重，并恢复为当前异环工坊默认值。\n\n"
        "额外形状标签和额外形状加成不会改变；当前未保存编辑会丢弃。\n若权重未生效，请重启计算器。"):
        return
    controller, ids = page.controller(), (int(role["character_id"]),)

    def done(_value):
        _reload_after_weight_reset(page, name)
        QMessageBox.information(page, "重置当前", f"[{display_name}] 已恢复为默认权重，后续可随新版本更新。")

    _submit(page, lambda: controller.reset_weights(ids), done, title="重置")


def reset_all_config_weights(page: BasicWeightPage):
    data = getattr(page, "_config_form_data", {}) or {}
    ids = tuple(int(role["character_id"]) for role in data.values()
                if isinstance(role, dict) and role.get("character_id") is not None and not role.get("is_custom"))
    if not ids or not _confirm_weight_reset(page,
        f"将清除当前账号全部 {len(ids)} 名角色的自定义卡带主词条和驱动副词条权重，恢复为当前异环工坊默认值。\n\n"
        "此操作无法撤销；额外形状标签和额外形状加成不会改变，当前未保存编辑会丢弃。\n若权重未生效，请重启计算器。"):
        return
    controller, active = page.controller(), str(getattr(page, "_config_active_role", "") or "")

    def done(restored):
        _reload_after_weight_reset(page, active)
        QMessageBox.information(page, "重置所有", f"已恢复 {len(restored)} 名角色的默认权重，后续可随新版本更新。")

    _submit(page, lambda: controller.reset_weights(ids), done, title="重置")


def create_custom_role(page):
    from .page import confirm_pending_config_changes, switch_config_form
    controller = page.controller()
    if controller.is_writing():
        return
    if getattr(page, "_config_dirty", False):
        if not confirm_pending_config_changes(page,
                completion=lambda success: create_custom_role(page) if success else None):
            return
    name, accepted = QInputDialog.getText(page, "新建角色", "角色名称（也作为游戏内名称）：")
    if not accepted:
        return

    def done(role):
        _clear_draft(page, discard=True)
        switch_config_form(page, active_role=str(role["character_id"]))

    _submit(page, lambda: controller.create_custom_role(str(name)), done, title="新建角色")


def delete_custom_role(page, role_name, role_data, rebuild_all_tabs):
    controller = page.controller()
    if controller.is_writing() or QMessageBox.question(page, "删除角色",
        f"删除 [{role_data['name_zh']}] 以及它的计算偏好和配装槽位？",
        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
        return
    character_id = int(role_data["character_id"])

    def done(_value):
        # 只丢弃已删除角色的草稿，其余未保存编辑继续保留。
        for field in _DIRTY_FIELDS:
            getattr(page, field, set()).discard(character_id)
        page._config_dirty = any(getattr(page, field, set()) for field in _DIRTY_FIELDS)
        (getattr(page, "_config_form_data", {}) or {}).pop(role_name, None)
        page._config_loaded_model = None
        rebuild_all_tabs()

    _submit(page, lambda: controller.delete_custom_role(character_id), done, title="删除角色")
