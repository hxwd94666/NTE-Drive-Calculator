# 配置页面 MainWindow 兼容转发方法。
"""Configuration controller installed onto MainWindow."""

from __future__ import annotations

from src.features.configuration.page import (
    add_weight as config_add_weight,
    build_config_page,
    confirm_pending_config_changes as config_confirm_pending_config_changes,
    del_weight as config_del_weight,
    refresh_config_forms as config_refresh_config_forms,
    render_roles_form,
    reset_all_config_weights as config_reset_all_config_weights,
    reset_current_config_weights as config_reset_current_config_weights,
    reset_config_form as config_reset_config_form,
    save_config_data as config_save_config_data,
    save_config_form as config_save_config_form,
    save_role_weight_value as config_save_role_weight_value,
    switch_config_form as config_switch_config_form,
)
from src.features.official_role.page import confirm_pending_my_role_changes


def _page_config(self):
    return build_config_page(self)

def _refresh_config_forms(self):
    return config_refresh_config_forms(self, self.app_context.paths.config_dir)

def _confirm_leave_config_page(self):
    return config_confirm_pending_config_changes(
        self,
        self.app_context.paths.config_dir,
    )

def _confirm_leave_my_role_page(self):
    return confirm_pending_my_role_changes(self)

def _switch_config_form(self,name):
    return config_switch_config_form(
        self,
        name,
        self.app_context.paths.config_dir,
    )

def _build_roles_form(self,data):
    return render_roles_form(self,data)

def _add_weight(self,rn,data,cb,weight_field="weights"):
    return config_add_weight(
        self,
        rn,
        data,
        cb,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _save_role_weight_value(self,rn,key,value,data,weight_field="weights"):
    return config_save_role_weight_value(
        self,
        rn,
        key,
        value,
        data,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _del_weight(self,rn,key,data,cb,weight_field="weights"):
    return config_del_weight(
        self,
        rn,
        key,
        data,
        cb,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _save_config_form(self):
    return config_save_config_form(
        self,
        self.app_context.paths.config_dir,
        None,
    )

def _reset_config_form(self):
    return config_reset_config_form(
        self,
        self.app_context.paths.config_dir,
        self.app_context.paths.bundled_config_dir,
    )

def _reset_current_config_weights(self):
    return config_reset_current_config_weights(
        self,
        self.app_context.paths.config_dir,
    )

def _reset_all_config_weights(self):
    return config_reset_all_config_weights(
        self,
        self.app_context.paths.config_dir,
    )

def _save_config_data(self,data):
    return config_save_config_data(
        self,
        data,
        self.app_context.paths.config_dir,
    )


class ConfigurationControllerMixin:
    _page_config = _page_config
    _refresh_config_forms = _refresh_config_forms
    _confirm_leave_config_page = _confirm_leave_config_page
    _confirm_leave_my_role_page = _confirm_leave_my_role_page
    _switch_config_form = _switch_config_form
    _build_roles_form = _build_roles_form
    _add_weight = _add_weight
    _save_role_weight_value = _save_role_weight_value
    _del_weight = _del_weight
    _save_config_form = _save_config_form
    _reset_config_form = _reset_config_form
    _reset_current_config_weights = _reset_current_config_weights
    _reset_all_config_weights = _reset_all_config_weights
    _save_config_data = _save_config_data
