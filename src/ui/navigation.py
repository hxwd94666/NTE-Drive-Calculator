# 定义主窗口侧边栏导航项。
"""Navigation metadata for the main window stack."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    button_attr: str
    page_builder: str
    refresh_method: str | None = None


NAV_ITEMS = (
    NavItem("home", "⌂  工作台", "btn_home", "_page_home", "_refresh_home"),
    NavItem("execute", "⚡  执行", "btn_exec", "_page_execute"),
    NavItem("equipment", "💎  配装", "btn_equip", "_page_equipment", "_refresh_equip"),
    NavItem("my_role", "👤  角色", "btn_my_role", "_page_my_role", "_refresh_my_role"),
    NavItem("identify", "🔍  鉴定", "btn_identify", "_page_identify", "_refresh_identify_options"),
    NavItem("blueprint", "📐  图纸", "btn_blueprint", "_page_blueprint", "_refresh_blueprints"),
    NavItem("config", "⚙  配置", "btn_config", "_page_config", "_refresh_config_forms"),
    NavItem("settings", "🔧  设置", "btn_settings", "_page_settings"),
)


def nav_index_map() -> dict[str, int]:
    return {item.key: index for index, item in enumerate(NAV_ITEMS)}


def nav_title_map() -> dict[str, str]:
    return {item.key: item.label for item in NAV_ITEMS}


def nav_item_by_key(key: str) -> NavItem | None:
    return next((item for item in NAV_ITEMS if item.key == key), None)
