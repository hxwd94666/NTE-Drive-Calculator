# 使用角色页公共组件编辑战报中的单个角色配置副本。
"""Battle build snapshot editor dialog."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.official_role.profile_editor import OfficialRoleProfileEditor
from src.features.battle_report.marginal_replacement_controller import (
    show_marginal_equipment_replacement,
)
from src.services.battle_build_equipment_service import freeze_equipment_context
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)


class BattleBuildSnapshotEditorDialog(QDialog):
    """Collect a complete counterfactual role and equipment copy."""

    ACTION_SAVE = "save"
    ACTION_IMPORT_CULTIVATION = "import_cultivation"
    ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT = (
        "import_cultivation_and_equipment"
    )
    ACTION_SAVE_AND_SYNC_CULTIVATION = "save_and_sync_cultivation"

    def __init__(
        self,
        editor_data: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑本场角色配置副本")
        self._action = ""
        self._equipment_editable = bool(editor_data.get("equipment_editable", True))
        self._details = deepcopy(list(editor_data.get("details") or ()))
        self._editors: list[OfficialRoleProfileEditor] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        note = QLabel(self._note_text())
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#58a6ff;font-weight:600"))
        layout.addWidget(note)
        if not editor_data.get("has_edit"):
            seed = QLabel(
                "首次修改已按当前角色页配置生成草稿；保存前不会改变战报或角色页。"
            )
            seed.setWordWrap(True)
            seed.setStyleSheet(themed_style("color:#d29922;font-size:12px"))
            layout.addWidget(seed)

        self.tabs = QTabWidget()
        for index, detail in enumerate(self._details):
            scroll = self._editor_scroll(index)
            character = detail["character"]
            self.tabs.addTab(
                scroll,
                str(character.get("name_zh") or character["character_id"]),
            )
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.import_cultivation_button = QPushButton(
            "从角色页面同步（不含空幕驱动）"
        )
        self.import_cultivation_button.setToolTip(
            "用当前角色页养成覆盖战报修改副本，保留副本已选空幕/驱动。"
        )
        self.import_cultivation_button.clicked.connect(
            self._import_cultivation
        )
        actions.addWidget(self.import_cultivation_button)
        self.import_all_button = QPushButton(
            "从角色页面同步（含空幕驱动）"
        )
        self.import_all_button.setToolTip(
            "用当前角色页养成和当前空幕/驱动覆盖战报修改副本；"
            "不会反向写回装备。"
        )
        self.import_all_button.clicked.connect(self._import_all)
        actions.addWidget(self.import_all_button)
        self.import_all_button.setVisible(self._equipment_editable)
        actions.addStretch()
        self.save_button = QPushButton("保存修改副本")
        self.save_button.setObjectName("btnPrimary")
        self.save_button.clicked.connect(self._save_only)
        actions.addWidget(self.save_button)
        self.save_and_sync_button = QPushButton(
            "保存并同步到角色页（不含空幕驱动）"
        )
        self.save_and_sync_button.setToolTip(
            "仅把养成配置同步到角色页；所选空幕/驱动只保存在战报副本。"
        )
        self.save_and_sync_button.clicked.connect(self._save_and_sync)
        actions.addWidget(self.save_and_sync_button)
        layout.addLayout(actions)
        fit_dialog_to_available_screen(self, QSize(1080, 820))

    def profiles(self) -> list[dict]:
        profiles = []
        for editor in self._editors:
            profile = editor.profile()
            if not self._equipment_editable:
                profiles.append(profile)
                continue
            selection = editor.selected_equipment_context()
            if selection is None:
                raise ValueError("战报角色副本缺少边际配装上下文")
            context_key, context = selection
            profile.update(
                {
                    "equipment_context_key": context_key,
                    "equipment_context_title": str(
                        context.get("source_title")
                        or context.get("title")
                        or "战报配装副本"
                    ),
                    "equipment_source_kind": str(
                        context.get("source_kind") or "edited_copy"
                    ),
                    "equipment_override": freeze_equipment_context(context),
                }
            )
            profiles.append(profile)
        return profiles

    def _note_text(self) -> str:
        if not self._equipment_editable:
            return (
                "这是导入战报：包内固化空幕/驱动只读。可修改等级、觉醒、"
                "技能和弧盘，并可与角色页单向同步养成；不会改写装备。"
            )
        return (
            "原始战报快照始终保留；这里只维护一个可反复覆盖的修改副本。"
            "每个角色可从本场、游戏当前或已保存方案选择并替换空幕/驱动，"
            "保存时复制进本场副本；不会修改角色页、库存或配装方案。"
        )

    def _editor_scroll(self, index: int) -> QScrollArea:
        detail = self._details[index]
        editor = OfficialRoleProfileEditor(
            detail,
            self,
            include_analysis=False,
            include_equipment=True,
            allow_equipment_replacement=self._equipment_editable,
            show_equipment_context_selector=self._equipment_editable,
            equipment_replacement_handler=(
                lambda target, context_key, current=index: (
                    self._replace_equipment(current, target, context_key)
                )
            ),
            scoring_engine=getattr(self.parent(), "scoring_engine", None),
            shape_areas=getattr(self.parent(), "_shape_areas", {}),
        )
        if index < len(self._editors):
            self._editors[index] = editor
        else:
            self._editors.append(editor)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(editor)
        return scroll

    def _replace_equipment(
        self,
        index: int,
        target: dict,
        context_key: str,
    ) -> bool:
        if not self._equipment_editable or not 0 <= index < len(self._details):
            return False
        detail = self._details[index]
        context = (detail.get("equipment_contexts") or {}).get(context_key)
        if not isinstance(context, dict):
            return False

        accepted = show_marginal_equipment_replacement(
            self,
            detail,
            target,
            context_key=context_key,
            on_replaced=lambda replacement: (
                BattleMarginalCandidateService.replace_equipment(
                    context,
                    target,
                    replacement,
                )
            ),
            title="本场角色配置替换",
            summary=(
                "这里只修改当前弹窗草稿；点击保存后才复制进本场战报修改副本。"
                "不会修改角色页、库存、游戏当前配装或已保存方案。"
            ),
        )
        if accepted:
            title = self.tabs.tabText(index)
            old_scroll = self.tabs.widget(index)
            self.tabs.removeTab(index)
            old_scroll.deleteLater()
            self.tabs.insertTab(index, self._editor_scroll(index), title)
            self.tabs.setCurrentIndex(index)
        return accepted

    def action(self) -> str:
        return self._action

    def _save_only(self) -> None:
        self._action = self.ACTION_SAVE
        self.accept()

    def _save_and_sync(self) -> None:
        self._action = self.ACTION_SAVE_AND_SYNC_CULTIVATION
        self.accept()

    def _import_cultivation(self) -> None:
        self._action = self.ACTION_IMPORT_CULTIVATION
        self.accept()

    def _import_all(self) -> None:
        self._action = self.ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT
        self.accept()
