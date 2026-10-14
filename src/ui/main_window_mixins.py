# 显式声明主窗口从各功能模块获得的方法。
"""Explicit MainWindow mixins for extracted feature methods."""

from __future__ import annotations

from src.features.inventory.equipment.assembly_controller import EquipmentAssemblyControllerMixin
from src.features.inventory.equipment.display_controller import EquipmentDisplayControllerMixin
from src.features.inventory.warehouse.controller import WarehouseControllerMixin
from src.features.toolbox.page import build_toolbox_page, refresh_toolbox_page
from src.ui.controllers.environment_controller import EnvironmentControllerMixin
from src.ui.controllers.hotkey_controller import HotkeyControllerMixin
from src.ui.controllers.inventory_sync_controller import InventorySyncControllerMixin
from src.features.settings.support import SupportActionsMixin


class ToolboxMixin:
    _page_toolbox = build_toolbox_page
    _refresh_toolbox = refresh_toolbox_page


class FeatureMainWindowMixin(
    EnvironmentControllerMixin,
    HotkeyControllerMixin,
    InventorySyncControllerMixin,
    SupportActionsMixin,
    WarehouseControllerMixin,
    EquipmentAssemblyControllerMixin,
    EquipmentDisplayControllerMixin,
    ToolboxMixin,
):
    """Explicitly composed feature surface for MainWindow."""
