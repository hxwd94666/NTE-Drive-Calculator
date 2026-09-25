# 管理工具首页与账号绑定养成计算器整页之间的局部导航。
"""Toolbox-local navigation and account-bound calculator lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QStackedWidget, QWidget

from src.features.toolbox.cultivation_page import CultivationCalculatorPage
from src.services.cultivation_planner_service import CultivationPlannerService
from src.services.cultivation_owned_material_import import ImportedOwnedMaterials
from src.services.rewind_shape_recommendation_service import (
    RewindShapeRecommendationService,
)


def cultivation_context_identity(
    account_id: object,
    generation: object,
    static_database_path: str | Path,
) -> tuple[str, object, str, tuple[int, int]]:
    """Freeze dataset identity without the read-sensitive file access time."""

    path = Path(static_database_path)
    stat = path.stat()
    return str(account_id), generation, str(path), (stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class ToolboxDependencies:
    """Narrow account-bound dependencies assembled by ``src.ui.app``."""

    rewind_service_factory: Callable[[], RewindShapeRecommendationService]
    cultivation_service_factory: Callable[[], CultivationPlannerService]
    navigate_static_catalog: Callable[[], None]
    operation_guard: Callable[[str], None] | None = None
    operation_generation: Callable[[], object] | None = None
    operation_entry: Callable[[str, str], bool] | None = None
    operation_unavailable: Callable[[str, str, str], None] | None = None
    cultivation_context_identity: Callable[[], object] | None = None
    cultivation_asset_root: Callable[[], Path] | None = None
    cultivation_material_importer: Callable[[], ImportedOwnedMaterials] | None = None

    def rewind_service(self) -> RewindShapeRecommendationService:
        return self.rewind_service_factory()


class CultivationToolboxNavigation:
    """Keep a same-context draft and discard it when account identity changes."""

    def __init__(
        self,
        *,
        stack: QStackedWidget,
        home: QWidget,
        dependencies: ToolboxDependencies,
        dialog_parent: QWidget,
    ) -> None:
        self.stack = stack
        self.home = home
        self.dependencies = dependencies
        self.dialog_parent = dialog_parent
        self.page: CultivationCalculatorPage | None = None
        self.identity: object | None = self._identity()

    def open(self) -> None:
        if self.page is None:
            try:
                service = self.dependencies.cultivation_service_factory()
            except Exception as exc:
                QMessageBox.warning(
                    self.dialog_parent,
                    "养成计算器",
                    f"读取养成数据失败：{exc}",
                )
                return
            self.page = CultivationCalculatorPage(
                service,
                self.stack,
                context_identity=self._identity,
                material_importer=self.dependencies.cultivation_material_importer,
                asset_root=(
                    self.dependencies.cultivation_asset_root()
                    if self.dependencies.cultivation_asset_root is not None
                    else None
                ),
            )
            self.page.back_requested.connect(self.show_home)
            self.stack.addWidget(self.page)
            self.identity = self._identity()
        self.stack.setCurrentWidget(self.page)

    def show_home(self) -> None:
        self.stack.setCurrentWidget(self.home)

    def refresh(self) -> None:
        identity = self._identity()
        if self.page is not None and self.dependencies.cultivation_context_identity is not None:
            if identity != self.identity:
                self.discard()
        self.identity = identity

    def discard(self) -> None:
        page = self.page
        if page is None:
            return
        page.shutdown()
        self.show_home()
        self.stack.removeWidget(page)
        page.deleteLater()
        self.page = None

    def _identity(self) -> object | None:
        factory = self.dependencies.cultivation_context_identity
        return factory() if factory is not None else None


__all__ = [
    "CultivationToolboxNavigation",
    "ToolboxDependencies",
    "cultivation_context_identity",
]
