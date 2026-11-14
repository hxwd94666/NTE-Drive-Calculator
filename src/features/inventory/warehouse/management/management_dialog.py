# 仓库管理独立窗口只拥有控件和草稿；保存、计算和取消由专属 Controller 编排。
from src.ui.common.text_catalog import plain_label, resolve_text_catalog, set_button_text
from copy import deepcopy

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
)

from src.app.theme import current_style_sheet
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.features.inventory.warehouse.management.management_rules import WarehouseRulesEditor
from src.ui.equipment_management.scoring_fields import ScoringFields
from src.ui.role_suit_matching.dialog import MatchingEditorDialog


class WarehouseManagementDialog(ScoringFields, QDialog):
    generate_requested = Signal()
    rules_requested = Signal()
    dismiss_requested = Signal()
    _show_server_region_option = False

    def __init__(self, catalog, *, config_dir, parent=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.catalog, self.text, self.config_dir = catalog, catalog.render, config_dir
        self._committing = False
        self.setWindowTitle(self.text("warehouse.management.title"))
        self.setWindowModality(Qt.WindowModal)
        self.setStyleSheet(current_style_sheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.status = plain_label(self.text("warehouse.management.loading"))
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        footer = QHBoxLayout()
        footer.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        # Windows 原生按钮组默认确认在左；本窗口固定取消在左、生成预览在最右。
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        self.buttons.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), self.text("warehouse.management.cancel"))
        set_button_text(self.buttons.button(QDialogButtonBox.Ok), self.text("warehouse.management.preview"))
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("btnNew")
        # 原生按钮组有最小宽度；显式限制实际文案宽度，主题 Show 钩子不得重写业务动作。
        for role in (QDialogButtonBox.Cancel, QDialogButtonBox.Ok):
            button = self.buttons.button(role)
            button.setFixedWidth(min(260, button.fontMetrics().horizontalAdvance(button.accessibleName()) + 34))
        self.buttons.accepted.connect(self.generate_requested)
        self.buttons.rejected.connect(self.dismiss_requested)
        footer.addWidget(self.buttons)
        root.addLayout(footer)
        self.matching = MatchingEditorDialog(catalog, self, embedded=True, scope=MatchingScope.WAREHOUSE)
        self.matching.hide()
        set_button_text(
            self.matching.buttons.button(QDialogButtonBox.Save), self.text("warehouse.management.matching_done")
        )
        self.matching.save_requested.connect(lambda _: self.matching.show_overview())
        self.matching.reload_button.hide()
        self.matching.reload_requested.connect(self.dismiss_requested)
        self.rules = None
        self.set_busy(True)
        fit_dialog_to_available_screen(self, QSize(1050, 760))

    def install(self, data):
        self.config, _expected_matching, matching, choices = data
        self._shape_options, self._set_options, self._role_options = choices[:3]
        self._role_selection_data = choices[3] if len(choices) > 3 else ()
        self._widgets, self._range_values = {}, {}
        self._selected_character_ids = {
            key: list(self.config[key]["selected_character_ids"]) for key in ("lock", "discard")
        }
        scoring = QScrollArea()
        scoring.setWidgetResizable(True)
        self._scoring_page = self._build_scoring_page()
        self._scoring_page.installEventFilter(self)
        scoring.setWidget(self._scoring_page)
        self.tabs.addTab(scoring, self.text("warehouse.management.scoring"))
        self.rules = WarehouseRulesEditor(
            self.catalog, config_dir=self.config_dir, shapes=self._shape_options, sets=self._set_options, parent=self
        )
        self.rules.load(
            self.config["preserve_rules"], self.config.get("recommendation", {}),
            skip_scoring=self.config["skip_scoring"],
        )
        self.rules.skip_scoring.toggled.connect(self._update_scoring_state)
        self.rules.generate_requested.connect(self.rules_requested)
        self.rules.error.connect(self.show_error)
        self.tabs.addTab(self.rules, self.text("warehouse.management.rules_tab"))
        self.matching.load(matching)
        self.tabs.addTab(self.matching, self.text("warehouse.management.matching"))
        self._update_scoring_state(self.rules.skip_scoring.isChecked())
        self.set_busy(False)
        self.status.setText(self.text("warehouse.management.preview_hint"))

    def collect(self):
        config = deepcopy(self.config)
        for key, fields in self._widgets.items():
            config[key].update(
                enabled=fields["enabled"].isChecked(),
                selected_character_ids=list(self._selected_character_ids[key]),
                grade=fields["grade"].currentData(),
                threshold_mode=fields["threshold_mode"].currentData(),
                threshold_percent=fields["threshold_percent"].value(),
                **{
                    field: fields[field].currentData()
                    for field in ("role_scope", "quality_scope", "type_scope", "on_locked", "on_discarded")
                },
                shape_ids=list(self._range_values[key]["shape_ids"]),
                set_names=list(self._range_values[key]["set_names"]),
            )
        config["preserve_rules"] = deepcopy(self.rules.rules)
        config["recommendation"] = deepcopy(self.rules.recommendation)
        config["skip_scoring"] = self.rules.skip_scoring.isChecked()
        return normalize_warehouse_config(config), deepcopy(self.matching.draft)

    def _update_scoring_state(self, skipped):
        # 跳过不清空模块设置；保留只读展示，恢复后继续消费原阈值和所选范围。
        self._scoring_page.setEnabled(not skipped)
        key = "warehouse.management.scoring_skipped" if skipped else "warehouse.management.scoring"
        self.tabs.setTabText(0, self.text(key))

    def show_rules(self):
        self.tabs.setCurrentWidget(self.rules)

    def set_busy(self, busy, *, committing=False, message=None):
        self._committing = committing
        self.tabs.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not busy and self.rules is not None)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(not committing)
        if message is not None:
            self.status.setText(message)

    def show_error(self, message):
        self.status.setText(self.text("warehouse.management.error", reason=message))

    def _module_help_text(self, key):
        return self.text(f"warehouse.management.help.{key}")

    def _show_module_help(self, key, title):
        QMessageBox.information(
            self, self.text("warehouse.management.help.title", module=title), self._module_help_text(key)
        )

    def reject(self):
        if not self._committing:
            self.dismiss_requested.emit()

    def closeEvent(self, event):
        event.ignore()
        self.reject()

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_scoring_page", None) and event.type() == QEvent.Resize:
            # 窄屏上下排列，保留评分表单可滚动；宽屏两模块等宽填满，不把按钮推出屏幕。
            modules = self._scoring_page.layout().itemAt(0).layout()
            direction = QBoxLayout.TopToBottom if event.size().width() < 900 else QBoxLayout.LeftToRight
            if modules.direction() != direction:
                modules.setDirection(direction)
        return super().eventFilter(watched, event)
