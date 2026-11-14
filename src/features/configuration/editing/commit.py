# 统一基础权重页的保存、重置、自建角色和离页决定；失败保草稿，提交后不重放写入。
from __future__ import annotations

from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..page_owner import BasicWeightPage

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ..dependencies import freeze_basic_weight_dependencies


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


def _submit(page, work, done, *, title, completion=None, text_catalog=None):
    controller = page.controller()
    if controller.is_writing():
        return False
    status = getattr(page, "config_load_status", None)
    if page is not None:
        page.setEnabled(False)
    if status is not None:
        status.setText(resolve_text_catalog(text_catalog).render('ui.configuration.submit.status', title=f'{title}'))

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
            QMessageBox.warning(page, resolve_text_catalog(text_catalog).render('ui.configuration.failed.qmessagebox', title=f'{title}'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.failed.qmessagebox_2', error=f'{error}')))
            if completion is not None:
                completion(False)
        elif completion is not None:
            completion(False)

    def application_failed(error):
        unlock()
        if current():
            QMessageBox.warning(page, resolve_text_catalog(text_catalog).render('roles.page.submit_role_change.qmessagebox_2'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.application_failed.qmessagebox', error=f'{error}')))
            if completion is not None:
                completion(False)

    return controller.submit_change(work, committed, failed, application_failed)


def confirm_pending_config_changes(page: BasicWeightPage, *, completion=None, text_catalog=None):
    if not getattr(page, "_config_dirty", False):
        return True
    current_name = getattr(page, "_current_config_name", None)
    if not current_name:
        return True
    ret = QMessageBox.question(
        page,
        resolve_text_catalog(text_catalog).render('ui.configuration.confirm_pending_config_changes.ret'),
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.confirm_pending_config_changes.ret_2')),
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if ret == QMessageBox.Cancel:
        page.controller().log_dirty_exit(
            "cancel",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        return False
    if ret == QMessageBox.Save:
        page.controller().log_dirty_exit(
            "save",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        save_config_form(page, completion=completion, show_message=False, text_catalog=text_catalog)
        return False  # 只有提交确认才能续行导航，发起保存本身不代表成功。
    else:
        page.controller().log_dirty_exit(
            "discard",
            len(getattr(page, "_config_dirty_character_ids", set())),
        )
        _clear_draft(page, discard=True)
    return True



def save_config_form(page: BasicWeightPage, *, completion=None, show_message=True, text_catalog=None):
    if getattr(page, "_current_config_name", None) != "account_weights":
        return False
    controller = page.controller()
    data = deepcopy(getattr(page, "_config_form_data", {}) or {})
    fields = tuple(set(getattr(page, field, set())) for field in _DIRTY_FIELDS)

    def done(_value):
        _clear_draft(page)
        # 提交成功只清草稿标记，不另读“最新”状态重新解释本次提交。
        if show_message:
            QMessageBox.information(page, resolve_text_catalog(text_catalog).render('common.save'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.done.qmessagebox_3')))

    return _submit(page, lambda: controller.save_changes(data, *fields), done,
                   title=resolve_text_catalog(text_catalog).render('common.save'), completion=completion, text_catalog=text_catalog)


def _reload_after_weight_reset(page, active_role):
    from .form import switch_config_form
    _clear_draft(page, discard=True)
    switch_config_form(page, active_role=active_role)


def _confirm_weight_reset(page, message, *, text_catalog=None):
    if (getattr(page, "_current_config_name", None) != "account_weights"
            or page.controller().is_writing()):
        return False
    return QMessageBox.question(page, resolve_text_catalog(text_catalog).render('ui.configuration.confirm_weight_reset.qmessagebox'), message,
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes


def reset_current_config_weights(page: BasicWeightPage, *, text_catalog=None):
    name = str(getattr(page, "_config_active_role", "") or "")
    role = (getattr(page, "_config_form_data", {}) or {}).get(name) or {}
    if not name or not role:
        QMessageBox.information(page, resolve_text_catalog(text_catalog).render('roles.page.page_my_role.reset_current'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.reset_current_config_weights.qmessagebox')))
        return
    display_name = str(role["name_zh"])
    if role.get("is_custom"):
        QMessageBox.information(page, resolve_text_catalog(text_catalog).render('roles.page.page_my_role.reset_current'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.reset_current_config_weights.qmessagebox_2')))
        return
    if not _confirm_weight_reset(page,
        resolve_text_catalog(text_catalog).render('ui.configuration.reset_current_config_weights.caption', display_name=f'{display_name}')):
        return
    controller, ids = page.controller(), (int(role["character_id"]),)

    def done(_value):
        _reload_after_weight_reset(page, name)
        QMessageBox.information(page, resolve_text_catalog(text_catalog).render('roles.page.page_my_role.reset_current'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.done.qmessagebox', display_name=f'{display_name}')))

    _submit(page, lambda: controller.reset_weights(ids), done, title=resolve_text_catalog(text_catalog).render('ui.allocation.build.primary_reset_btn'))


def reset_all_config_weights(page: BasicWeightPage, *, text_catalog=None):
    data = getattr(page, "_config_form_data", {}) or {}
    ids = tuple(int(role["character_id"]) for role in data.values()
                if isinstance(role, dict) and role.get("character_id") is not None and not role.get("is_custom"))
    if not ids or not _confirm_weight_reset(page,
        resolve_text_catalog(text_catalog).render('ui.configuration.reset_all_config_weights.caption', value=f'{len(ids)}')):
        return
    controller, active = page.controller(), str(getattr(page, "_config_active_role", "") or "")

    def done(restored):
        _reload_after_weight_reset(page, active)
        QMessageBox.information(page, resolve_text_catalog(text_catalog).render('roles.page.page_my_role.reset_all'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.done.qmessagebox_2', value=f'{len(restored)}')))

    _submit(page, lambda: controller.reset_weights(ids), done, title=resolve_text_catalog(text_catalog).render('ui.allocation.build.primary_reset_btn'))


def create_custom_role(page, *, text_catalog=None):
    from .form import switch_config_form
    controller = page.controller()
    if controller.is_writing():
        return
    if getattr(page, "_config_dirty", False):
        if not confirm_pending_config_changes(page,
                completion=lambda success: create_custom_role(page, text_catalog=text_catalog) if success else None, text_catalog=text_catalog):
            return
    name, accepted = QInputDialog.getText(page, resolve_text_catalog(text_catalog).render('ui.configuration.create_custom_role.name_accepted'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.create_custom_role.name_accepted_2')))
    if not accepted:
        return

    def done(role):
        _clear_draft(page, discard=True)
        switch_config_form(page, active_role=str(role["character_id"]))

    _submit(page, lambda: controller.create_custom_role(str(name)), done, title=resolve_text_catalog(text_catalog).render('ui.configuration.create_custom_role.name_accepted'))


def delete_custom_role(page, role_name, role_data, rebuild_all_tabs, *, text_catalog=None):
    controller = page.controller()
    if controller.is_writing() or QMessageBox.question(page, resolve_text_catalog(text_catalog).render('ui.configuration.delete_custom_role.caption'),
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.configuration.delete_custom_role.qmessagebox', value=f"{role_data['name_zh']}")),
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

    _submit(page, lambda: controller.delete_custom_role(character_id), done, title=resolve_text_catalog(text_catalog).render('ui.configuration.delete_custom_role.caption'))
