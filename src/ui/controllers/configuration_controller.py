# 配置页面 MainWindow 兼容转发方法。
"""Configuration controller installed onto MainWindow."""

from __future__ import annotations

from src.app import runtime
from src.features.configuration.page import (
    add_weight as config_add_weight,
    build_config_page,
    confirm_pending_config_changes as config_confirm_pending_config_changes,
    del_weight as config_del_weight,
    refresh_config_forms as config_refresh_config_forms,
    render_roles_form,
    reset_config_form as config_reset_config_form,
    save_config_data as config_save_config_data,
    save_config_form as config_save_config_form,
    save_role_weight_value as config_save_role_weight_value,
    switch_config_form as config_switch_config_form,
)
from src.features.official_role.page import confirm_pending_my_role_changes
from src.ui.main_window_method_install import install_methods as _install_main_window_methods

_METHOD_NAMES = ["_page_config","_refresh_config_forms","_confirm_leave_config_page","_confirm_leave_my_role_page","_switch_config_form","_build_roles_form","_add_weight","_save_role_weight_value","_del_weight","_save_config_form","_reset_config_form","_save_config_data"]


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _page_config(self):
    return build_config_page(self)

def _refresh_config_forms(self):
    return config_refresh_config_forms(self, runtime.CONFIG_DIR)

def _confirm_leave_config_page(self):
    return config_confirm_pending_config_changes(self, runtime.CONFIG_DIR)

def _confirm_leave_my_role_page(self):
    return confirm_pending_my_role_changes(self)

def _switch_config_form(self,name):
    return config_switch_config_form(self, name, runtime.CONFIG_DIR)

def _build_roles_form(self,data):
    return render_roles_form(self,data)

def _add_weight(self,rn,data,cb,weight_field="weights"):
    return config_add_weight(self, rn, data, cb, runtime.CONFIG_DIR, weight_field)

def _save_role_weight_value(self,rn,key,value,data,weight_field="weights"):
    return config_save_role_weight_value(self, rn, key, value, data, runtime.CONFIG_DIR, weight_field)

def _del_weight(self,rn,key,data,cb,weight_field="weights"):
    return config_del_weight(self, rn, key, data, cb, runtime.CONFIG_DIR, weight_field)

def _save_config_form(self):
    return config_save_config_form(self, runtime.CONFIG_DIR, None)

def _reset_config_form(self):
    return config_reset_config_form(self, runtime.CONFIG_DIR, runtime.BUNDLED_CONFIG_DIR)

def _save_config_data(self,data):
    return config_save_config_data(self, data, runtime.CONFIG_DIR)
