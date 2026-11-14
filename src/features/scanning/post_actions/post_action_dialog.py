# 构建全量扫描后状态管理配置弹窗。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_tooltip, resolve_text_catalog, set_button_text
import copy
from pathlib import Path
from src.domain.equipment.eligibility.management_settings import MatchingScope

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.domain.inventory.post_actions import (
    default_post_action_config,
    merge_post_action_config,
    validate_post_action_config,
)
from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.scanning.management_settings import load_scan_post_action_config, save_scan_post_action_config


SUB_MATCH_OPTIONS = (("任意一个", 1), ("任意两个", 2), ("任意三个", 3), ("任意四个", 4))


from src.ui.equipment_management.rule_actions import PreserveRuleActionsMixin


from src.ui.equipment_management.scoring_fields import ScoringFields


from src.ui.equipment_management.rule_list_fields import RuleListFields


from src.services.inventory.management.management_choices import (
    load_drive_shape_options as _load_drive_shape_options,
    load_set_name_options as _load_set_name_options,
    load_role_options as _load_role_options,
    load_role_selection_data as _load_role_selection_data,
)


class ScanPostActionDialog(ScoringFields, RuleListFields, PreserveRuleActionsMixin, QDialog):
    def __init__(
        self,
        parent,
        user_config_dir: Path,
        config_dir: Path,
        *,
        user_database_path: Path | None = None,
        static_database_path: Path | None = None,
        asset_root: Path | None = None,
        window_title: str = "全量扫描管理",
        show_server_region_option: bool = True,
        role_suit_matching=None,
        catalog=None,
    ):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.user_config_dir = Path(user_config_dir)
        self.config_dir = Path(config_dir)
        self.user_database_path = user_database_path
        self.static_database_path = static_database_path
        self.asset_root = asset_root
        self._show_server_region_option = show_server_region_option
        self.catalog = resolve_text_catalog(catalog)
        self.role_suit_matching = role_suit_matching
        self._matching_panel = None
        self._matching_committing = False
        self.setWindowTitle(window_title)
        self.setMinimumWidth(560)
        self.config = load_scan_post_action_config(
            self.user_config_dir,
            user_database_path=self.user_database_path,
        )
        self._widgets = {}
        self._shape_options = _load_drive_shape_options(self.static_database_path) if self.static_database_path is not None else _load_drive_shape_options()
        self._set_options = _load_set_name_options(self.static_database_path) if self.static_database_path is not None else _load_set_name_options()
        self._role_options = _load_role_options(self.user_database_path, self.static_database_path, self.asset_root) if self.static_database_path is not None else _load_role_options(self.user_database_path)
        self._role_selection_data = _load_role_selection_data(self.user_database_path, self.static_database_path, self.asset_root)
        self._selected_character_ids = {
            key: list(self.config[key].get("selected_character_ids", [])) for key in ("discard", "lock")
        }
        self._range_values = {}
        self._preserve_rules = copy.deepcopy(self.config.get("preserve_rules", []))
        self._build_ui()
        fit_dialog_to_available_screen(self, QSize(980, 700))


    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        footer = QHBoxLayout()
        self.hmt_region_check = QCheckBox(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.scanning.build_ui.hmt_region_check'), self)
        self.hmt_region_check.setChecked(self.config.get("server_region") == "hmt")
        self.hmt_region_check.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.scanning.build_ui.hmt_region_check_2')))
        self.hmt_region_check.setVisible(self._show_server_region_option)
        if self._show_server_region_option:
            footer.addWidget(self.hmt_region_check)
        footer.addStretch()
        self._scoring_footer = footer

        self._main_tabs = QTabWidget()
        self._main_tabs.addTab(self._build_scoring_page(), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.scoring'))
        self._main_tabs.addTab(self._build_preserve_rules_page(), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.rules_tab'))
        if self.role_suit_matching is not None:
            panel = self.role_suit_matching.create_panel(self, scope=MatchingScope.SCAN)
            self._matching_panel = panel
            self._main_tabs.addTab(panel, self.role_suit_matching.catalog.render("matching.title"))
        add_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.scanning.build_ui.add_button'))
        add_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:5px 12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        add_button.clicked.connect(self._add_preserve_rule)
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.addWidget(add_button)
        self._main_tabs.setCornerWidget(corner, Qt.TopRightCorner)
        corner.setVisible(False)
        self._main_tabs.currentChanged.connect(lambda index: corner.setVisible(index == 1))

        root.addWidget(self._main_tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        set_button_text(buttons.button(QDialogButtonBox.Save), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.save'))
        set_button_text(buttons.button(QDialogButtonBox.Cancel), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.cancel'))
        self._buttons = buttons
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        if self._matching_panel is not None:
            panel = self._matching_panel
            bottom.addWidget(panel.footer, 1)
            panel.footer.hide()
            self._main_tabs.currentChanged.connect(self._update_matching_footer)
            panel.stack.currentChanged.connect(self._update_matching_footer)
            panel.busy_changed.connect(self._matching_busy)
            panel.management_save_finished.connect(self._matching_saved)
            self._matching_busy(not panel.stack.isEnabled(), False)
        else:
            bottom.addStretch()
        bottom.addWidget(buttons)
        root.addLayout(bottom)

    def _update_matching_footer(self, _index=None):
        panel = self._matching_panel
        matching_visible = self._main_tabs.currentIndex() == 2
        panel.footer.setVisible(matching_visible)
        # 只有卡带编辑页使用内层保存/返回；其他页面仍走管理窗口的保存/取消。
        self._buttons.setVisible(not (matching_visible and panel.stack.currentIndex() == 1))

    def _matching_busy(self, busy, committing):
        self._matching_committing = committing
        self._buttons.button(QDialogButtonBox.Save).setEnabled(not busy)
        self._buttons.button(QDialogButtonBox.Cancel).setEnabled(not committing)
        self._main_tabs.setEnabled(not committing)

    def _matching_saved(self, success):
        if success:
            self.accept()
        else:
            self._main_tabs.setCurrentIndex(2)

    def reject(self):
        if not self._matching_committing:
            super().reject()

    def closeEvent(self, event):
        if self._matching_committing:
            event.ignore()
        else:
            super().closeEvent(event)


    def _collect_config(self) -> dict:
        config = default_post_action_config()
        config["server_region"] = "hmt" if self.hmt_region_check.isChecked() else "default"
        for key, widgets in self._widgets.items():
            module = config[key]
            module["selected_character_ids"] = list(self._selected_character_ids[key])
            module["enabled"] = widgets["enabled"].isChecked()
            module["threshold_mode"] = widgets["threshold_mode"].currentData()
            module["threshold_percent"] = widgets["threshold_percent"].value()
            for field in ("grade", "role_scope", "quality_scope", "type_scope", "on_locked", "on_discarded"):
                module[field] = widgets[field].currentData()
            module["shape_ids"] = list(self._range_values[key]["shape_ids"])
            module["set_names"] = list(self._range_values[key]["set_names"])
        config["recommendation"] = copy.deepcopy(self.config.get("recommendation", {}))
        config["preserve_rules"] = copy.deepcopy(self._preserve_rules)
        return merge_post_action_config(config)

    def _save(self) -> None:
        config = self._collect_config()
        if any(rule.get('smart_kind') for rule in config['preserve_rules']):
            QMessageBox.warning(self, self.catalog.render('scan.management.invalid_title'),
                plain_tooltip(self.catalog.render('scan.management.dynamic_unavailable')))
            return
        error = validate_post_action_config(config)
        if error:
            QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.scanning.save.qmessagebox'), error)
            return
        if self._matching_panel is not None:
            # 同一确认提交评分、规则及套装配置，成功才触发原调用方的后续计算。
            self._matching_panel.management_save_requested.emit(config)
            return
        save_scan_post_action_config(
            self.user_config_dir,
            config,
            user_database_path=self.user_database_path,
        )
        self.accept()


def show_scan_post_action_dialog(
    parent,
    user_config_dir: Path,
    config_dir: Path,
    *,
    user_database_path: Path | None = None,
    static_database_path: Path | None = None,
    asset_root: Path | None = None,
    window_title: str = "全量扫描管理",
    show_server_region_option: bool = True,
    role_suit_matching=None,
    catalog=None,
) -> bool:
    dialog = ScanPostActionDialog(
        parent,
        user_config_dir,
        config_dir,
        user_database_path=user_database_path,
        static_database_path=static_database_path,
        asset_root=asset_root,
        window_title=window_title,
        show_server_region_option=show_server_region_option,
        role_suit_matching=role_suit_matching,
        catalog=catalog,
    )
    return dialog.exec() == QDialog.Accepted
