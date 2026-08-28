# 提供可被战报等页面复用的官方角色养成编辑组件。
"""Public Qt editor for one official-role cultivation profile."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .role_calculation import (
    _build_damage_formula_group,
    _build_margin_group,
    _calculation_detail,
)
from .role_equipment import _build_drive_summary_group
from .role_growth import (
    _build_awakening_group,
    _build_base_group,
    _build_fork_group,
    _build_skill_group,
)
from .role_weights import _build_weight_group

__all__ = ["OfficialRoleProfileEditor"]


class OfficialRoleProfileEditor(QWidget):
    """Edit cultivation fields while keeping persistence outside the widget."""

    changed = Signal()

    def __init__(
        self,
        detail: dict,
        parent: QWidget | None = None,
        *,
        include_analysis: bool = False,
        include_equipment: bool | None = None,
        allow_equipment_replacement: bool = False,
        show_equipment_context_selector: bool = True,
        equipment_replacement_handler: Callable[[dict, str], bool] | None = None,
        scoring_engine=None,
        shape_areas: dict[str, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.scoring_engine = scoring_engine
        self._shape_areas = dict(shape_areas or {})
        self._detail = detail
        self._include_analysis = bool(include_analysis)
        self._include_equipment = (
            self._include_analysis
            if include_equipment is None
            else bool(include_equipment)
        )
        self._editor: dict = {
            "detail": detail,
            "marginal_property_weights": dict(detail.get("property_weights") or {}),
            "marginal_main_property_weights": dict(
                detail.get("main_property_weights") or {}
            ),
            "equipment_context_key": str(
                detail.get("selected_equipment_context_key") or "current"
            ),
        }
        self._official_role_dirty_ids: set[int] = set()
        self._my_role_dirty = False
        character_id = int(detail["character"]["character_id"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_build_base_group(self, character_id, detail, self._editor))
        layout.addWidget(
            _build_awakening_group(self, character_id, detail, self._editor)
        )
        layout.addWidget(_build_skill_group(self, character_id, detail, self._editor))
        if self._include_analysis:
            layout.addWidget(
                _build_margin_group(self, character_id, detail, self._editor)
            )
        layout.addWidget(_build_fork_group(self, character_id, detail, self._editor))
        if self._include_equipment:
            layout.addWidget(
                _build_drive_summary_group(
                    self,
                    detail,
                    self._editor,
                    allow_replacement=allow_equipment_replacement,
                    show_context_selector=show_equipment_context_selector,
                    replacement_handler=equipment_replacement_handler,
                )
            )
        if self._include_analysis:
            layout.addWidget(_build_damage_formula_group(detail, self._editor))
            layout.addWidget(
                _build_weight_group(self, character_id, detail, self._editor)
            )
        layout.addStretch()
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self.changed)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self.changed)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(self.changed)
        for widget in self.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(self.changed)

    def profile(self) -> dict:
        projected = _calculation_detail(self._detail, self._editor)
        profile = dict(projected["profile"])
        source = self._detail["profile"]
        profile.update({
            "character_id": int(self._detail["character"]["character_id"]),
            "observed_name": source.get("observed_name"),
            "ordinal": int(source.get("ordinal") or 0),
        })
        return profile

    def selected_equipment_context(self) -> tuple[str, dict] | None:
        """Expose the selected public context without owning its persistence."""

        if not self._include_equipment:
            return None
        context_key = str(self._editor.get("equipment_context_key") or "battle")
        context = (self._detail.get("equipment_contexts") or {}).get(context_key)
        if context is None or context_key == "theory":
            raise ValueError("请选择可冻结的空幕/驱动配装")
        return context_key, context
