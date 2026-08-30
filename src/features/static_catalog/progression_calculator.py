# 游戏资料库共用的养成材料与活力计算面板。
"""Game-styled, non-table progression calculator dialog."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QCoreApplication, QSize, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.workers import WorkerThread
from src.domain.progression_stamina import ProgressionStaminaResult, StaminaPlanStatus
from src.features.static_catalog.progression_calculator_models import (
    ProgressionCalculatorOrchestrator,
    ProgressionCalculatorOutcome,
    ProgressionCalculatorSession,
    ProgressionMaterialInput,
    deliver_progression_outcome,
)
from src.services.progression_stamina_service import ProgressionStaminaService
from src.services.static_catalog_fork_release_metadata import ForkProgressionRequest
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


ResultCallback = Callable[[ProgressionCalculatorOutcome], None]


class _Disclosure(QFrame):
    """Default-collapsed identity rows; never leaks raw IDs into the card face."""

    def __init__(
        self,
        rows: tuple[tuple[str, str], ...],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("progressionDisclosure")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(5)
        toggle = QPushButton("更多信息  ▾", self)
        toggle.setObjectName("progressionDisclosureToggle")
        toggle.setCheckable(True)
        content = QFrame(self)
        content.setObjectName("progressionDisclosureContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(9, 7, 9, 7)
        content_layout.setSpacing(4)
        for title, value in rows:
            label = QLabel(f"{title}  ·  {value}", content)
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            content_layout.addWidget(label)
        content.setVisible(False)

        def toggle_content(expanded: bool) -> None:
            content.setVisible(expanded)
            toggle.setText("更多信息  ▴" if expanded else "更多信息  ▾")

        toggle.toggled.connect(toggle_content)
        root.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(content)


class _MaterialCard(QFrame):
    """One material requirement card with a user-editable owned amount."""

    def __init__(
        self,
        material: ProgressionMaterialInput,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.material = material
        self.setObjectName("progressionMaterialCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(7)
        top = QHBoxLayout()
        name = QLabel(material.display_name, self)
        name.setObjectName("progressionMaterialName")
        requirement = QLabel(material.requirement_text, self)
        requirement.setObjectName("progressionMaterialRequired")
        top.addWidget(name, 1)
        top.addWidget(requirement)
        root.addLayout(top)
        owned_row = QHBoxLayout()
        owned_label = QLabel("当前持有", self)
        self.owned = QSpinBox(self)
        self.owned.setObjectName("progressionOwnedQuantity")
        self.owned.setRange(0, 99_999_999)
        self.owned.setSingleStep(1)
        owned_row.addWidget(owned_label)
        owned_row.addStretch(1)
        owned_row.addWidget(self.owned)
        root.addLayout(owned_row)
        if material.more_info:
            root.addWidget(_Disclosure(material.more_info, parent=self))


class ProgressionCalculatorDialog(QDialog):
    """Reusable modeless calculator owned by the static-catalog composition root."""

    def __init__(
        self,
        *,
        service: ProgressionStaminaService,
        terminology_service: StaticCatalogTerminologyService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("progressionCalculatorDialog")
        self.setWindowTitle("养成材料与活力")
        self.setModal(False)
        self._orchestrator = ProgressionCalculatorOrchestrator(
            service=service,
            terminology_service=terminology_service,
        )
        self._session: ProgressionCalculatorSession | None = None
        self._callback: ResultCallback | None = None
        self._calculation_token: object | None = None
        self._calculation_worker: WorkerThread | None = None
        self._material_cards: dict[str, _MaterialCard] = {}
        self._build()
        self._apply_styles()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hero = QFrame(self)
        hero.setObjectName("progressionHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(15, 13, 15, 13)
        hero_layout.setSpacing(4)
        self.title = QLabel("养成材料与活力", hero)
        self.title.setObjectName("progressionTitle")
        subtitle = QLabel(
            "编辑猎人等级和现有材料，按正式副本产出计算最低活力。",
            hero,
        )
        subtitle.setObjectName("progressionSubtitle")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(self.title)
        hero_layout.addWidget(subtitle)
        root.addWidget(hero)

        level_card = QFrame(self)
        level_card.setObjectName("progressionLevelCard")
        level_layout = QHBoxLayout(level_card)
        level_layout.setContentsMargins(12, 10, 12, 10)
        level_layout.setSpacing(10)
        hunter_label = QLabel("猎人等级", level_card)
        self.hunter_level = QSpinBox(level_card)
        self.hunter_level.setObjectName("progressionHunterLevel")
        self.hunter_level.setRange(1, 60)
        self.hunter_level.setValue(60)
        identification_label = QLabel("生效鉴别等级", level_card)
        self.identification_level = QComboBox(level_card)
        self.identification_level.setObjectName("progressionIdentificationLevel")
        level_layout.addWidget(hunter_label)
        level_layout.addWidget(self.hunter_level)
        level_layout.addStretch(1)
        level_layout.addWidget(identification_label)
        level_layout.addWidget(self.identification_level)
        root.addWidget(level_card)

        scroll = QScrollArea(self)
        scroll.setObjectName("progressionScroll")
        scroll.setWidgetResizable(True)
        scroll_host = QWidget(scroll)
        self.body = QVBoxLayout(scroll_host)
        self.body.setContentsMargins(1, 1, 1, 1)
        self.body.setSpacing(9)
        materials_heading = QLabel("材料清单", scroll_host)
        materials_heading.setObjectName("progressionSectionTitle")
        self.body.addWidget(materials_heading)
        self.materials_host = QFrame(scroll_host)
        self.materials_layout = QVBoxLayout(self.materials_host)
        self.materials_layout.setContentsMargins(0, 0, 0, 0)
        self.materials_layout.setSpacing(8)
        self.body.addWidget(self.materials_host)
        self.gaps_host = QFrame(scroll_host)
        self.gaps_layout = QVBoxLayout(self.gaps_host)
        self.gaps_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.gaps_host)
        self.result_host = QFrame(scroll_host)
        self.result_host.setObjectName("progressionResultCard")
        self.result_layout = QVBoxLayout(self.result_host)
        self.result_layout.setContentsMargins(12, 10, 12, 10)
        self.result_layout.setSpacing(6)
        self.result_host.setVisible(False)
        self.body.addWidget(self.result_host)
        self.body.addStretch(1)
        scroll.setWidget(scroll_host)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        self.validation = QLabel("", self)
        self.validation.setObjectName("progressionValidation")
        self.validation.setWordWrap(True)
        self.calculate_button = QPushButton("计算最低活力", self)
        self.calculate_button.setObjectName("btnPrimary")
        close_button = QPushButton("关闭", self)
        actions.addWidget(self.validation, 1)
        actions.addWidget(self.calculate_button)
        actions.addWidget(close_button)
        root.addLayout(actions)

        self.hunter_level.valueChanged.connect(self._refresh_identification)
        self.calculate_button.clicked.connect(self._calculate)
        close_button.clicked.connect(self.close)
        self._refresh_identification()

    def open_request(
        self,
        request: Mapping[str, object] | ForkProgressionRequest,
        *,
        on_result: ResultCallback,
    ) -> ProgressionCalculatorSession:
        """Replace the active frozen request and show the reusable dialog."""

        self._calculation_token = None
        session = self._orchestrator.prepare(request)
        self._session = session
        self._callback = on_result
        self.title.setText(session.title)
        self._render_materials(session)
        self._clear_layout(self.gaps_layout)
        if session.more_info:
            self.gaps_layout.addWidget(_Disclosure(session.more_info, parent=self))
        self._clear_result()
        self.validation.setText("")
        self._set_calculation_busy(False)
        fit_dialog_to_available_screen(self, QSize(620, 720))
        self.show()
        self.raise_()
        self.activateWindow()
        return session

    def dispose(self) -> None:
        """Release the active page callback before composition-root shutdown."""

        self._calculation_token = None
        self._callback = None
        self._session = None
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._calculation_token = None
        self._callback = None
        self._session = None
        super().closeEvent(event)

    def _refresh_identification(self) -> None:
        hunter = self.hunter_level.value()
        projection = self._orchestrator.identification_level(hunter)
        previous = self.identification_level.currentData()
        self.identification_level.blockSignals(True)
        self.identification_level.clear()
        self.identification_level.addItem(
            f"鉴别 {projection.native_level}（原生）",
            projection.native_level,
        )
        if projection.native_level >= 3:
            self.identification_level.addItem(
                f"鉴别 {projection.native_level - 1}（下调一级）",
                projection.native_level - 1,
            )
        selected = self.identification_level.findData(previous)
        self.identification_level.setCurrentIndex(max(0, selected))
        self.identification_level.blockSignals(False)

    def _render_materials(self, session: ProgressionCalculatorSession) -> None:
        self._clear_layout(self.materials_layout)
        self._material_cards.clear()
        if not session.materials:
            empty = QLabel("当前正式数据未提供可计算的材料需求。", self)
            empty.setObjectName("progressionEmptyState")
            empty.setWordWrap(True)
            self.materials_layout.addWidget(empty)
            return
        for material in session.materials:
            card = _MaterialCard(material, parent=self.materials_host)
            self._material_cards[material.key] = card
            self.materials_layout.addWidget(card)

    def _calculate(self) -> None:
        session = self._session
        if session is None:
            self.validation.setText("请先从角色或弧盘养成页打开计算器。")
            return
        owned = {
            key: card.owned.value() for key, card in self._material_cards.items()
        }
        try:
            calculation = self._orchestrator.freeze_calculation(
                session,
                hunter_level=self.hunter_level.value(),
                effective_identification_level=self.identification_level.currentData(),
                owned_quantities=owned,
            )
        except (TypeError, ValueError) as exc:
            self.validation.setText(str(exc))
            return
        token = object()
        self._calculation_token = token
        self._set_calculation_busy(True)
        self.validation.setText("正在计算最低活力…")
        run_calculation = self._orchestrator.run_calculation
        worker = WorkerThread(
            target=lambda frozen=calculation, run=run_calculation: run(frozen),
            parent=QCoreApplication.instance(),
        )
        self._calculation_worker = worker
        worker.result_ready.connect(
            lambda outcome, current=token: self._calculation_ready(current, outcome)
        )
        worker.error.connect(
            lambda _message, current=token: self._calculation_failed(current)
        )
        worker.finished.connect(
            lambda current=token, instance=worker: self._calculation_finished(
                current, instance
            )
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _calculation_ready(self, token: object, outcome: object) -> None:
        if token is not self._calculation_token:
            return
        if not isinstance(outcome, ProgressionCalculatorOutcome):
            self._calculation_failed(token)
            return
        self._calculation_token = None
        self._set_calculation_busy(False)
        self.validation.setText("")
        self._render_result(outcome.result)
        callback = self._callback
        if callback is not None:
            callback_error = deliver_progression_outcome(callback, outcome)
            if callback_error is not None:
                self.validation.setText(callback_error)

    def _calculation_failed(self, token: object) -> None:
        if token is not self._calculation_token:
            return
        self._calculation_token = None
        self._set_calculation_busy(False)
        self.validation.setText("养成计算失败，请稍后重试。")

    def _calculation_finished(
        self,
        token: object,
        worker: WorkerThread,
    ) -> None:
        if self._calculation_worker is worker:
            self._calculation_worker = None
        if token is self._calculation_token:
            self._calculation_token = None
            self._set_calculation_busy(False)

    def _set_calculation_busy(self, busy: bool) -> None:
        self.calculate_button.setEnabled(not busy)
        self.calculate_button.setText("正在计算…" if busy else "计算最低活力")

    def _render_result(self, result: ProgressionStaminaResult) -> None:
        self._clear_layout(self.result_layout)
        labels = {
            StaminaPlanStatus.COMPLETE: "精确结果",
            StaminaPlanStatus.PARTIAL: "部分可计算",
            StaminaPlanStatus.UNAVAILABLE: "暂不可计算",
        }
        status = QLabel(labels[result.status], self.result_host)
        status.setObjectName("progressionResultStatus")
        self.result_layout.addWidget(status)
        if result.total_stamina is not None:
            total = QLabel(f"最低活力  {result.total_stamina}", self.result_host)
        elif result.known_stamina > 0:
            total = QLabel(
                f"已知部分活力  {result.known_stamina} · 完整总量不可用",
                self.result_host,
            )
        else:
            total = QLabel("完整活力暂不可用", self.result_host)
        total.setObjectName("progressionResultTotal")
        self.result_layout.addWidget(total)
        for run in result.runs:
            row = QLabel(
                f"{run.label}  × {run.runs} 次  ·  {run.total_stamina} 活力",
                self.result_host,
            )
            row.setObjectName("progressionRunCard")
            row.setWordWrap(True)
            self.result_layout.addWidget(row)
        if result.gaps:
            rows = tuple(
                (f"计算未完整项 {index}", gap)
                for index, gap in enumerate(result.gaps, start=1)
            )
            self.result_layout.addWidget(_Disclosure(rows, parent=self.result_host))
        self.result_host.setVisible(True)

    def _clear_result(self) -> None:
        self._clear_layout(self.result_layout)
        self.result_host.setVisible(False)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _apply_styles(self) -> None:
        self.setStyleSheet(themed_style(
            "QFrame#progressionHero{background:#10243f;border:1px solid #2f81f7;"
            "border-radius:12px;}"
            "QLabel#progressionTitle{color:#f0f6fc;font-size:20px;font-weight:900;}"
            "QLabel#progressionSubtitle{color:#8b949e;font-size:11px;}"
            "QFrame#progressionLevelCard,QFrame#progressionMaterialCard,"
            "QFrame#progressionResultCard{background:#161b22;border:1px solid #30363d;"
            "border-radius:10px;}"
            "QLabel#progressionSectionTitle,QLabel#progressionMaterialName{"
            "color:#f0f6fc;font-size:13px;font-weight:900;}"
            "QLabel#progressionMaterialRequired{color:#58a6ff;font-size:11px;"
            "font-weight:700;}"
            "QLabel#progressionEmptyState{color:#d29922;background:#161b22;"
            "border:1px dashed #d29922;border-radius:10px;padding:12px;}"
            "QPushButton#progressionDisclosureToggle{background:transparent;"
            "color:#8b949e;border:1px solid #30363d;border-radius:7px;"
            "padding:4px 8px;text-align:left;font-size:10px;}"
            "QFrame#progressionDisclosureContent{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
            "QFrame#progressionDisclosureContent QLabel{color:#8b949e;"
            "background:transparent;border:none;font-size:10px;}"
            "QLabel#progressionResultStatus{color:#58a6ff;font-weight:900;}"
            "QLabel#progressionResultTotal{color:#f0f6fc;font-size:18px;"
            "font-weight:900;}"
            "QLabel#progressionRunCard{color:#c9d1d9;background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;padding:8px;}"
            "QLabel#progressionValidation{color:#d29922;font-size:11px;}"
        ))


def build_progression_calculator_dialog(
    *,
    service: ProgressionStaminaService,
    terminology_service: StaticCatalogTerminologyService,
    parent: QWidget | None = None,
) -> ProgressionCalculatorDialog:
    """Public factory for the shared static-catalog composition root."""

    return ProgressionCalculatorDialog(
        service=service,
        terminology_service=terminology_service,
        parent=parent,
    )


__all__ = [
    "ProgressionCalculatorDialog",
    "ResultCallback",
    "build_progression_calculator_dialog",
]
