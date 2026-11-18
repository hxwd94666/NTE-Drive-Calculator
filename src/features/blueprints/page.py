# 使用官方 SQLite 图纸约束构建并展示本地图纸方案。
"""Blueprint page that owns its widgets and request worker."""

from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.context import AppContext
from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.features.blueprints.controller import BlueprintController
from src.features.blueprints.dependencies import BlueprintDependencies
from src.features.inventory.warehouse.display.cards import warehouse_shape_pixmap
from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS as _OFFICIAL_SHAPE_LABELS
from src.ui.equipment.puzzle_board import PuzzleBoardWidget
from src.ui.common.widgets import match_pinyin

__all__ = ["BlueprintPage"]


class BlueprintPage:
    """Own blueprint page state and delegate generation to BlueprintController."""

    def __init__(
        self,
        *,
        app_context: AppContext,
        navigate: Callable[[str], None],
        text_catalog=None,
    ) -> None:
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._app_context = app_context
        self._navigate = navigate
        self._widget: QScrollArea | None = None
        self._search: QLineEdit | None = None
        self._status: QLabel | None = None
        self._content_layout: QVBoxLayout | None = None
        self._data: dict[str, dict] = {}
        self._controller: BlueprintController | None = None
        self._worker: WorkerThread | None = None

    def build(self) -> QScrollArea:
        if self._widget is not None:
            return self._widget
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        return_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build.return_button'))
        return_button.setObjectName("returnToRoleButton")
        return_button.clicked.connect(lambda: self._navigate("my_role"))
        header.addWidget(return_button)
        self._search = QLineEdit()
        self._search.setPlaceholderText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build.search'))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.filter)
        header.addWidget(self._search, 1)
        refresh_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build.refresh_button'))
        refresh_button.setObjectName("btnAction")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        layout.addLayout(header)
        self._status = plain_label(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build.status')
        )
        self._status.setStyleSheet(themed_style("color:#8b949e"))
        layout.addWidget(self._status)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignTop)
        layout.addWidget(content)
        layout.addStretch()
        self._widget = scroll
        return scroll

    def refresh(self) -> None:
        if self._controller is not None:
            self._controller.cancel()
        content_layout, status = self._require_widgets()
        self._clear_content(content_layout)
        status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.refresh.status'))
        content_layout.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.refresh.caption')))
        dependencies = BlueprintDependencies.from_app_context(self._app_context)
        controller = BlueprintController(dependencies)
        worker = WorkerThread(target=controller.generate, parent=self._widget)
        self._controller = controller
        self._worker = worker
        worker.result_ready.connect(
            lambda data: self._render(data, controller)
        )
        worker.error.connect(
            lambda error: self._render_error(error, controller)
        )
        worker.finished.connect(lambda: self._release_worker(worker))
        worker.start()

    def filter(self, text: str) -> None:
        self._draw(text)

    def reset_account_state(self) -> None:
        if self._controller is not None:
            self._controller.cancel()
            self._controller = None
        self._data = {}
        if self._content_layout is not None:
            self._clear_content(self._content_layout)
        if self._status is not None:
            self._status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.reset_account_state.status'))

    def _render_error(
        self,
        error: str,
        controller: BlueprintController,
    ) -> None:
        if controller is self._controller and controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            _, status = self._require_widgets()
            status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.render_error.status', error=f'{error}'))

    def _render(
        self,
        data: dict[str, dict],
        controller: BlueprintController,
    ) -> None:
        if controller is not self._controller or not controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            return
        self._data = data or {}
        plan_count = sum(
            len(entry["blueprints"]) for entry in self._data.values()
        )
        _, status = self._require_widgets()
        status.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.render.status', value=f'{len(self._data)}', plan_count=f'{plan_count}')
        )
        self._draw()

    def _draw(self, filter_text: str = "") -> None:
        content_layout, _ = self._require_widgets()
        self._clear_content(content_layout)
        if not self._data:
            content_layout.addWidget(
                plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.draw.caption'))
            )
            return
        search_text = filter_text.strip()
        matched_roles = [
            role_name
            for role_name in self._data
            if not search_text or match_pinyin(role_name, search_text)
        ]
        show_all_for = (
            matched_roles[0]
            if search_text and len(matched_roles) == 1
            else None
        )
        shown = 0
        for role_name, role_data in sorted(self._data.items()):
            if search_text and not match_pinyin(role_name, search_text):
                continue
            shown += 1
            content_layout.addWidget(
                self._build_role_group(
                    role_name,
                    role_data,
                    show_all=role_name == show_all_for,
                )
            )
        if not shown:
            content_layout.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.draw.caption_2')))

    def _build_role_group(
        self,
        role_name: str,
        role_data: dict,
        *,
        show_all: bool,
    ) -> QGroupBox:
        blueprints = role_data["blueprints"]
        group = QGroupBox(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build_role_group.group', role_name=f'{role_name}', value=f"{role_data['suit_name']}", value_2=f'{len(blueprints)}')
        )
        group.setStyleSheet(
            themed_style(
                "QGroupBox{font-size:13px;font-weight:600;color:#58a6ff;"
                "border:1px solid #21262d;border-radius:8px;padding-top:16px}"
            )
        )
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)
        visible = blueprints if show_all else blueprints[:4]
        plans_grid = QGridLayout()
        plans_grid.setHorizontalSpacing(16)
        plans_grid.setVerticalSpacing(10)
        for index, blueprint in enumerate(visible, start=1):
            plans_grid.addWidget(
                self._build_plan_card(index, blueprint, text_catalog=getattr(self, 'text_catalog', None)),
                (index - 1) // 2,
                (index - 1) % 2,
            )
        group_layout.addLayout(plans_grid)
        hidden_count = len(blueprints) - len(visible)
        if hidden_count > 0:
            more = plain_label(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.blueprints.build_role_group.more', hidden_count=f'{hidden_count}')
            )
            more.setStyleSheet(
                themed_style("color:#8b949e;font-size:11px")
            )
            group_layout.addWidget(more)
        return group

    @staticmethod
    def _build_plan_card(index: int, blueprint: dict, *, text_catalog=None) -> QWidget:
        plan_card = QWidget()
        row = QHBoxLayout(plan_card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(
            PuzzleBoardWidget(blueprint["board"], cell_size=28),
            0,
            Qt.AlignTop,
        )
        extras = QWidget()
        extras_layout = QVBoxLayout(extras)
        extras_layout.setContentsMargins(0, 0, 0, 0)
        extras_layout.setSpacing(2)
        extras_layout.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.blueprints.build_plan_card.caption', index=f'{index}')))
        image_row = QHBoxLayout()
        image_row.setSpacing(4)
        for shape_id in blueprint.get("extra_pieces", [])[:3]:
            shape_label = _OFFICIAL_SHAPE_LABELS.get(
                str(shape_id),
                str(shape_id),
            )
            image = plain_label()
            image.setPixmap(warehouse_shape_pixmap(shape_label, "Gold", text_catalog=text_catalog))
            image.setToolTip(shape_label)
            image.setFixedSize(52, 52)
            image.setScaledContents(True)
            image_row.addWidget(image)
        image_row.addStretch()
        extras_layout.addLayout(image_row)
        row.addWidget(extras, 1)
        return plan_card

    def _require_widgets(self) -> tuple[QVBoxLayout, QLabel]:
        if self._content_layout is None or self._status is None:
            raise RuntimeError("blueprint page has not been built")
        return self._content_layout, self._status

    @staticmethod
    def _clear_content(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _release_worker(self, worker: WorkerThread) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
