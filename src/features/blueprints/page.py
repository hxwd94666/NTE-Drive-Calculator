# 使用官方 SQLite 图纸约束构建并展示本地图纸方案。
"""Blueprint page that owns its widgets and request worker."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr, display_term
from src.app.context import AppContext
from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.features.blueprints.controller import BlueprintController
from src.features.blueprints.dependencies import BlueprintDependencies
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.services.blueprint_service import (
    OFFICIAL_SHAPE_LABELS as _OFFICIAL_SHAPE_LABELS,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin

__all__ = ["BlueprintPage"]


class BlueprintPage:
    """Own blueprint page state and delegate generation to BlueprintController."""

    def __init__(
        self,
        *,
        app_context: AppContext,
        navigate: Callable[[str], None],
    ) -> None:
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
        return_button = QPushButton(tr("← 返回角色"))
        return_button.setObjectName("returnToRoleButton")
        return_button.clicked.connect(lambda: self._navigate("my_role"))
        header.addWidget(return_button)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("搜索角色图纸（支持拼音）…"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.filter)
        header.addWidget(self._search, 1)
        refresh_button = QPushButton(tr("生成图纸"))
        refresh_button.setObjectName("btnAction")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        layout.addLayout(header)
        self._status = QLabel(
            tr("图纸由本地求解器生成；官方角色读取发行模板，自建角色读取当前账号保存的底盘与默认套装。")
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
        content_layout, status = self._require_widgets()
        self._clear_content(content_layout)
        status.setText(tr("正在生成官方与自建角色图纸…"))
        content_layout.addWidget(QLabel(tr("正在组合驱动并求解盘面…")))
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
        worker.finished.connect(self._release_worker)
        worker.start()

    def filter(self, text: str) -> None:
        self._draw(text)

    def reset_account_state(self) -> None:
        self._data = {}
        if self._content_layout is not None:
            self._clear_content(self._content_layout)
        if self._status is not None:
            self._status.setText(tr("账号已切换，请重新生成角色图纸。"))

    def _render_error(
        self,
        error: str,
        controller: BlueprintController,
    ) -> None:
        if controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            _, status = self._require_widgets()
            status.setText(tr("生成图纸失败：{error}", error=error))

    def _render(
        self,
        data: dict[str, dict],
        controller: BlueprintController,
    ) -> None:
        if not controller.accepts(
            BlueprintDependencies.from_app_context(self._app_context)
        ):
            return
        self._data = data or {}
        plan_count = sum(
            len(entry["blueprints"]) for entry in self._data.values()
        )
        _, status = self._require_widgets()
        status.setText(
            tr("已为 {roles} 名角色生成 {plans} 个图纸方案。",
               roles=len(self._data), plans=plan_count)
        )
        self._draw()

    def _draw(self, filter_text: str = "") -> None:
        content_layout, _ = self._require_widgets()
        self._clear_content(content_layout)
        if not self._data:
            content_layout.addWidget(
                QLabel(tr("暂无可生成图纸的角色，请点击“生成图纸”。"))
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
            content_layout.addWidget(QLabel(tr("没有匹配的角色图纸。")))

    def _build_role_group(
        self,
        role_name: str,
        role_data: dict,
        *,
        show_all: bool,
    ) -> QGroupBox:
        blueprints = role_data["blueprints"]
        group = QGroupBox(
            tr("{role}  —  {suit}  ({count} 套图纸)",
               role=display_term(str(role_name)),
               suit=display_term(str(role_data["suit_name"])),
               count=len(blueprints))
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
                self._build_plan_card(index, blueprint),
                (index - 1) // 2,
                (index - 1) % 2,
            )
        group_layout.addLayout(plans_grid)
        hidden_count = len(blueprints) - len(visible)
        if hidden_count > 0:
            more = QLabel(
                tr("仅展示前 4 套图纸；另有 {count} 套可行方案未展示。", count=hidden_count)
            )
            more.setStyleSheet(
                themed_style("color:#8b949e;font-size:11px")
            )
            group_layout.addWidget(more)
        return group

    @staticmethod
    def _build_plan_card(index: int, blueprint: dict) -> QWidget:
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
        extras_layout.addWidget(QLabel(tr("方案 {index} · 额外形状", index=index)))
        image_row = QHBoxLayout()
        image_row.setSpacing(4)
        for shape_id in blueprint.get("extra_pieces", [])[:3]:
            shape_label = _OFFICIAL_SHAPE_LABELS.get(
                str(shape_id),
                str(shape_id),
            )
            image = QLabel()
            image.setPixmap(warehouse_shape_pixmap(shape_label, "Gold"))
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

    def _release_worker(self) -> None:
        self._worker = None
