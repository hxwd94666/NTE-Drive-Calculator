# 仓库规则编辑分成两个物理区域；拖放和键盘只调整本区顺序，复制由执行类型决定去向。
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from copy import deepcopy

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.inventory.post_actions import DEFAULT_PRESERVE_RULE
from src.domain.equipment.warehouse.warehouse_rule_sections import (
    RuleSection,
    partition_rules,
    reorder_section,
    edit_rule,
    copy_rule,
    append_manual_rule,
    replace_intelligent_rules,
    set_rule_enabled,
)
from src.ui.equipment_management.intelligent_budget_bar import IntelligentBudgetBar
from src.ui.equipment_management.rule_editor import PreserveRuleEditor
from src.ui.equipment_management.intelligent_rule_editor import IntelligentRuleEditor
from src.ui.equipment_management.rule_order import RuleOrderController, RuleOrderHandle
from src.ui.equipment_management.rule_text import _preserve_rule_summary


class WarehouseRulesEditor(QWidget):
    generate_requested = Signal()
    error = Signal(str)

    def __init__(self, catalog, *, config_dir, shapes, sets, parent=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = catalog.render
        self.config_dir, self.shapes, self.sets = config_dir, shapes, sets
        self.rules, self.recommendation = [], {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(6)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 4, 2)
        layout.setSpacing(12)
        self.sections, self.lists, self.rows, self.orders = {}, {}, {}, {}
        for section in RuleSection:
            frame = QFrame()
            frame.setObjectName("warehouseRuleSection")
            frame.setStyleSheet(
                themed_style("QFrame#warehouseRuleSection{border:1px solid #30363d;border-radius:8px;}")
            )
            box = QVBoxLayout(frame)
            box.setContentsMargins(12, 12, 12, 12)
            box.setSpacing(8)
            header = QWidget()
            header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            toolbar = QBoxLayout(QBoxLayout.LeftToRight, header)
            toolbar.setContentsMargins(0, 0, 0, 0)
            heading = ElidedTextLabel(self.text(f"warehouse.management.rules.{section.value}"))
            heading.setMinimumWidth(80)
            heading.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9;"))
            toolbar.addWidget(heading, 1 if section == RuleSection.MANUAL else 0)
            if section == RuleSection.MANUAL:
                add = TextActionButton(self.text("warehouse.management.rules.add"))
                add.setObjectName("btnNew")
                add.setAutoDefault(False)
                add.clicked.connect(self.add_rule)
                toolbar.addWidget(add)
            else:
                options = QWidget()
                options.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
                option_layout = QHBoxLayout(options)
                option_layout.setContentsMargins(0, 0, 0, 0)
                self.skip_scoring = QCheckBox(self.text("warehouse.management.rules.skip_scoring"))
                self.skip_scoring.setEnabled(False)
                self.skip_scoring.setAccessibleName(self.text("warehouse.management.rules.skip_scoring"))
                self.skip_scoring_help = QPushButton("?")
                self.skip_scoring_help.setObjectName("btnHelp")
                self.skip_scoring_help.setFixedWidth(28)
                self.skip_scoring_help.setAutoDefault(False)
                self.skip_scoring_help.setAccessibleName(self.text("warehouse.management.rules.skip_scoring_help_title"))
                self.skip_scoring_help.setToolTip(plain_tooltip(self.text("warehouse.management.rules.skip_scoring_help")))
                self.skip_scoring_help.clicked.connect(self._show_skip_scoring_help)
                option_layout.addWidget(self.skip_scoring)
                option_layout.addWidget(self.skip_scoring_help)
                toolbar.addWidget(options)
                # 空白留在开关与操作按钮之间，不让标题的 stretch 把开关推到最右侧。
                toolbar.addStretch(1)
                actions = QWidget()
                action_layout = QBoxLayout(QBoxLayout.LeftToRight, actions)
                action_layout.setContentsMargins(0, 0, 0, 0)
                self.generate = TextActionButton(self.text("warehouse.management.rules.generate"))
                self.generate.setObjectName("btnPrimary")
                self.clear = TextActionButton(self.text("warehouse.management.rules.clear"))
                self.clear.setObjectName("btnDanger")
                for button in (self.generate, self.clear):
                    button.setAutoDefault(False)
                self.generate.clicked.connect(self.generate_requested)
                self.clear.clicked.connect(self.clear_intelligent)
                action_layout.addWidget(self.generate)
                action_layout.addWidget(self.clear)
                toolbar.addWidget(actions)
                self._intelligent_header, self._intelligent_actions = header, actions
                header.installEventFilter(self)
                actions.installEventFilter(self)
            box.addWidget(header)
            hint = plain_label(self.text(f"warehouse.management.rules.{section.value}_hint"))
            hint.setWordWrap(True)
            hint.setStyleSheet(themed_style("color:#8b949e;"))
            box.addWidget(hint)
            if section == RuleSection.INTELLIGENT:
                self.budget = IntelligentBudgetBar(
                    lambda: self.rules,
                    self.render,
                    frame,
                    advice=lambda: self.recommendation.get("initial_grade_advice"),
                    text_catalog=catalog,
                )
                box.addWidget(self.budget)
            rows = QVBoxLayout()
            rows.setSpacing(8)
            box.addLayout(rows)
            layout.addWidget(frame)
            self.sections[section], self.lists[section], self.rows[section] = frame, rows, []
            self.orders[section] = RuleOrderController(
                self.scroll,
                lambda key=section: self.rows[key],
                lambda index, offset, key=section: self.move_rule(key, index, offset),
                contains=lambda point, area=frame: area.rect().contains(area.mapFromGlobal(point)),
                text_catalog=catalog,
            )
        layout.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll)

    def load(self, rules, recommendation, *, skip_scoring=False):
        self.rules, self.recommendation = deepcopy(rules), deepcopy(recommendation)
        self.skip_scoring.setChecked(skip_scoring)
        self.render()

    def _show_skip_scoring_help(self):
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Information)
        message.setWindowTitle(self.text("warehouse.management.rules.skip_scoring_help_title"))
        message.setTextFormat(Qt.PlainText)
        message.setText(self.text("warehouse.management.rules.skip_scoring_help"))
        message.setStandardButtons(QMessageBox.Ok)
        fit_dialog_to_available_screen(message, message.sizeHint())
        try:
            message.exec()
        finally:
            message.deleteLater()

    def _mutate(self, operation):
        try:
            self.rules = operation()
        except ValueError as exc:
            self.error.emit(str(exc))
        self.render()

    def render(self):
        old_scroll = self.scroll.verticalScrollBar().value()
        sections = partition_rules(self.rules)
        self._handles = {}
        self._row_widgets = {}
        for order in self.orders.values():
            order.cancel()
        for section, values in zip(RuleSection, sections):
            layout = self.lists[section]
            while layout.count():
                widget = layout.takeAt(0).widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            self.rows[section] = []
            for rule in values:
                # 行身份引用本窗口唯一草稿，不能使用 partition 返回的可丢弃深副本。
                actual = next(row for row in self.rules if row["rule_id"] == rule["rule_id"])
                row = self._row(actual, section)
                layout.addWidget(row)
                self.rows[section].append((actual, row))
                self._row_widgets[actual["rule_id"]] = row
            if not values:
                empty = plain_label(self.text("warehouse.management.rules.empty"))
                empty.setWordWrap(True)
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet(themed_style("color:#8b949e;padding:20px 0;"))
                layout.addWidget(empty)
        has_intelligent = bool(sections[1])
        self.clear.setEnabled(has_intelligent)
        if not has_intelligent:
            # 清空或删到最后一条时一并恢复评分，不能留下已勾选却不可操作的开关。
            self.skip_scoring.setChecked(False)
        self.skip_scoring.setEnabled(has_intelligent)
        self.budget.refresh()
        QTimer.singleShot(0, self, lambda value=old_scroll: self._restore_scroll(value))

    def _restore_scroll(self, value):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(min(value, bar.maximum()))

    def _row(self, rule, section):
        row = QFrame()
        row.setObjectName("preserveRuleRow")
        row.setStyleSheet(
            themed_style(
                "QFrame#preserveRuleRow{background:#161b22;border:1px solid #30363d;border-radius:6px;}"
                'QFrame#preserveRuleRow[dragSource="true"]{background:#10243f;border:2px solid #58a6ff;}'
            )
        )
        line = QBoxLayout(QBoxLayout.LeftToRight, row)
        line.setContentsMargins(12, 10, 10, 10)
        line.setSpacing(10)
        info = QWidget()
        info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content = QHBoxLayout(info)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)
        handle = RuleOrderHandle(self.orders[section], rule, text_catalog=getattr(self, 'text_catalog', None))
        handle.setToolTip(self.text(f"warehouse.management.rules.{section.value}_move"))
        handle.setAccessibleName(self.text("warehouse.management.rules.move_name", name=rule["name"]))
        self._handles[rule["rule_id"]] = handle
        content.addWidget(handle)
        enabled = QCheckBox()
        enabled.setChecked(rule.get("enabled", True))
        enabled.setAccessibleName(self.text("warehouse.management.rules.enable_name", name=rule["name"]))
        enabled.toggled.connect(
            lambda checked, identity=rule["rule_id"]: self._mutate(
                lambda: set_rule_enabled(self.rules, identity, checked)
            )
        )
        content.addWidget(enabled)
        description = QVBoxLayout()
        description.setSpacing(3)
        name = (rule.get("name") or self.text("warehouse.management.rules.unnamed")).replace("空幕", "卡带")
        suffix = ""
        title = plain_label(name + suffix)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9;"))
        description.addWidget(title)
        full_summary = _preserve_rule_summary(rule, text_catalog=self.text_catalog).replace("空幕", "卡带")
        detail = full_summary
        kind = rule.get("smart_kind")
        if kind:
            action = self.text("warehouse.management.rules.action_" + rule.get("action", "keep"))
            if kind in {"current_drive", "current_tape", "future"}:
                detail = self.text("warehouse.management.rules.reserve", count=rule.get("reserve", 2))
            elif kind == "grade":
                detail = self.text("warehouse.management.rules.grade", grade=rule.get("grade", "SSS"))
            else:
                detail = self.text("warehouse.management.rules.dynamic")
            detail = self.text("warehouse.management.rules.dynamic_summary", action=action, detail=detail)
        summary = plain_label(detail)
        summary.setToolTip(plain_tooltip(full_summary))
        summary.setWordWrap(True)
        summary.setStyleSheet(themed_style("color:#8b949e;"))
        description.addWidget(summary)
        content.addLayout(description, 1)
        line.addWidget(info, 1)
        actions = QWidget()
        actions.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        controls = QHBoxLayout(actions)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        item_type = "dynamic" if kind else "tape" if rule.get("item_type") == "tape" else "drive"
        action = rule.get("action", "keep")
        for text, style in (
            (self.text("warehouse.management.rules.type_" + item_type), "color:#58a6ff;border:1px solid #1f6feb;"),
            (
                self.text("warehouse.management.rules.action_" + action),
                {
                    "discard": "color:#f85149;border:1px solid #da3633;",
                    "lock": "color:#8957e5;border:1px solid #8957e5;",
                }.get(action, "color:#3fb950;border:1px solid #238636;"),
            ),
        ):
            badge = plain_label()
            width = min(140, badge.fontMetrics().horizontalAdvance(text) + 18)
            badge.setFixedWidth(width)
            badge.setText(badge.fontMetrics().elidedText(text, Qt.ElideRight, width - 18))
            badge.setAccessibleName(text)
            badge.setToolTip(plain_tooltip(text))
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(themed_style(style + "border-radius:6px;padding:5px 7px;"))
            controls.addWidget(badge)
        for key, callback in (
            ("edit", lambda: self.edit(rule)),
            (
                "copy",
                lambda: self._mutate(
                    lambda: copy_rule(
                        self.rules, rule["rule_id"], name=self.text("warehouse.management.rules.copy_name", name=name)
                    )
                ),
            ),
            ("delete", lambda: self.delete(rule)),
        ):
            button = TextActionButton(self.text(f"warehouse.management.rules.{key}"))
            button.setAutoDefault(False)
            button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            if key == "delete":
                button.setStyleSheet(themed_style("QPushButton{color:#f85149;}"))
            button.clicked.connect(callback)
            controls.addWidget(button)
        line.addWidget(actions)
        row.installEventFilter(self)
        return row

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and watched in (
            getattr(self, "_intelligent_header", None), getattr(self, "_intelligent_actions", None)
        ):
            # 新开关和帮助按可用宽度换行；只改变排布，不重建控件或覆盖用户草稿。
            layout = watched.layout()
            required = sum(
                layout.itemAt(index).widget().sizeHint().width() for index in range(layout.count())
                if layout.itemAt(index).widget() is not None
            ) + layout.spacing() * max(0, layout.count() - 1)
            direction = QBoxLayout.TopToBottom if event.size().width() < required else QBoxLayout.LeftToRight
            if layout.direction() != direction:
                layout.setDirection(direction)
        if event.type() == QEvent.Resize and watched.objectName() == "preserveRuleRow":
            # 只改变展示排布，不重建草稿或按钮；窄屏让完整操作带换行，避免压缩规则正文。
            layout = watched.layout()
            direction = QBoxLayout.TopToBottom if event.size().width() < 820 else QBoxLayout.LeftToRight
            if layout.direction() != direction:
                layout.setDirection(direction)
        return super().eventFilter(watched, event)

    def move_rule(self, section, index, offset):
        identities = [rule["rule_id"] for rule, _ in self.rows[section]]
        target = index + offset
        if not 0 <= index < len(identities) or not 0 <= target < len(identities):
            return
        identities.insert(target, identities.pop(index))
        self._mutate(lambda: reorder_section(self.rules, section, identities))
        identity = identities[target]
        # 重排销毁旧行后恢复同一身份焦点，连续 Ctrl+方向键仍作用于原规则。
        QTimer.singleShot(0, self, lambda: self.focus_rule(identity))

    def focus_rule(self, identity):
        row = self._row_widgets.get(identity)
        if row is not None:
            self.scroll.ensureWidgetVisible(row)
            self._handles[identity].setFocus(Qt.OtherFocusReason)
            return True
        return False

    def _editor(self, rule):
        return (
            IntelligentRuleEditor(self, rule, text_catalog=getattr(self, 'text_catalog', None))
            if rule.get("smart_kind")
            else PreserveRuleEditor(self, rule, self.shapes, self.sets, self.config_dir, text_catalog=getattr(self, 'text_catalog', None))
        )

    def add_rule(self):
        dialog = self._editor(DEFAULT_PRESERVE_RULE)
        try:
            if dialog.exec() == QDialog.Accepted and dialog.result_rule() is not None:
                self._mutate(lambda: append_manual_rule(self.rules, dialog.result_rule()))
        finally:
            dialog.deleteLater()

    def edit(self, rule):
        dialog = self._editor(rule)
        try:
            if dialog.exec() == QDialog.Accepted and dialog.result_rule() is not None:
                self._mutate(lambda: edit_rule(self.rules, rule["rule_id"], dialog.result_rule()))
        finally:
            dialog.deleteLater()

    def delete(self, rule):
        if (
            QMessageBox.question(
                self,
                self.text("warehouse.management.rules.delete"),
                self.text("warehouse.management.rules.delete_confirm"),
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            == QMessageBox.Yes
        ):
            self._mutate(lambda: [deepcopy(row) for row in self.rules if row["rule_id"] != rule["rule_id"]])

    def clear_intelligent(self):
        self._mutate(lambda: replace_intelligent_rules(self.rules, []))

    def install_generated(self, value):
        # 先完整校验返回值；生成失败不提交半份规则，也不改变跳过选择。
        rules = replace_intelligent_rules(self.rules, value["rules"])
        recommendation = deepcopy(value["options"])
        self.rules, self.recommendation = rules, recommendation
        self.skip_scoring.setChecked(True)
        self.render()
