# 共用规则列表的展示控件；具体管理拥有自己的草稿、仲裁和持久化。
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget
from src.app.theme import themed_style
from src.ui.equipment_management.rule_order import RuleOrderController, RuleOrderHandle
from src.ui.equipment_management.rule_text import _preserve_rule_summary


class RuleListFields:
    def _build_preserve_rules_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(6)
        order_hint = plain_label(self.catalog.render("scan.management.order_hint"))
        order_hint.setWordWrap(True)
        root.addWidget(order_hint)

        scroll = self._rules_scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self._preserve_rules_layout = QVBoxLayout(content)
        self._preserve_rules_layout.setContentsMargins(0, 2, 0, 2)
        self._preserve_rules_layout.setSpacing(8)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self._rule_order = RuleOrderController(scroll, lambda: self._rule_rows, self._move_preserve_rule, text_catalog=getattr(self, 'text_catalog', None))
        self._render_preserve_rules()
        return page

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _render_preserve_rules(self) -> None:
        old_scroll = self._rules_scroll.verticalScrollBar().value() if hasattr(self, "_rules_scroll") else 0
        self._clear_layout(self._preserve_rules_layout)
        self._rule_rows = []
        if not self._preserve_rules:
            empty = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.render_preserve_rules.empty'))
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#8b949e;padding:48px 0")
            self._preserve_rules_layout.addWidget(empty)
        else:
            for rule in self._preserve_rules:
                row = self._build_preserve_rule_row(rule)
                self._rule_rows.append((rule, row))
                self._preserve_rules_layout.addWidget(row)
        self._preserve_rules_layout.addStretch()
        QTimer.singleShot(0, self, lambda value=old_scroll: self._restore_rules_scroll(value))

    def _restore_rules_scroll(self, value: int) -> None:
        bar = self._rules_scroll.verticalScrollBar()
        bar.setValue(min(value, bar.maximum()))

    def _build_preserve_rule_row(self, rule: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("preserveRuleRow")
        row.setStyleSheet(
            themed_style(
                'QFrame#preserveRuleRow{background:#161b22;border:1px solid #30363d;border-radius:6px;}QFrame#preserveRuleRow[dragSource="true"]{background:#10243f;border:2px solid #58a6ff;}'
            )
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(10)
        text = resolve_text_catalog(getattr(self, "text_catalog", None)).render
        rule_name = str(rule.get("name") or text("warehouse.management.rules.unnamed"))
        enabled = QCheckBox()
        enabled.setToolTip('')
        enabled.setAccessibleName(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.build_preserve_rule_row.enabled_2', value=rule_name))
        layout.addWidget(RuleOrderHandle(self._rule_order, rule, text_catalog=getattr(self, 'text_catalog', None)))
        enabled.setChecked(bool(rule.get("enabled", True)))
        enabled.toggled.connect(
            lambda checked, current=rule: self._set_preserve_rule_enabled(self._rule_index(current), checked)
        )
        layout.addWidget(enabled)
        details = QVBoxLayout()
        details.setSpacing(3)
        origin = rule.get("intelligent")
        source = text("ui.equipment_management.build_preserve_rule_row.label") if origin and origin.get("edited") else text("ui.rules.origin.generated") if origin else ""
        name = plain_label(rule_name + source)
        name.setWordWrap(True)
        if origin:
            enabled.toggled.connect(
                lambda _checked, label=name, current=rule: label.setText(
                    str(current.get("name") or text("warehouse.management.rules.unnamed")) + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.build_preserve_rule_row.label')
                )
            )
        name.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
        summary = plain_label(_preserve_rule_summary(rule, text_catalog=getattr(self, "text_catalog", None)))
        summary.setStyleSheet(themed_style("color:#8b949e"))
        summary.setWordWrap(True)
        details.addWidget(name)
        details.addWidget(summary)
        layout.addLayout(details, 1)
        edit_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.rules.edit'))
        copy_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.rules.copy'))
        delete_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.rules.delete'))
        compact_height = edit_button.sizeHint().height()
        item_type = "动态" if rule.get("smart_kind") else "卡带" if rule.get("item_type") == "tape" else "驱动"
        type_badge = plain_label(ui_enum_text(getattr(self, 'text_catalog', None), item_type))
        type_badge.setFixedHeight(compact_height)
        type_badge.setStyleSheet(
            themed_style("color:#58a6ff;border:1px solid #1f6feb;border-radius:6px;padding:1px 7px")
        )
        action_label = {"lock": "锁定", "discard": "弃置"}.get(rule.get("action"), "仅保留")
        action_badge = plain_label(ui_enum_text(getattr(self, "text_catalog", None), action_label))
        action_badge.setFixedHeight(compact_height)
        action_badge.setStyleSheet(
            themed_style(
                "color:#f85149;border:1px solid #da3633;border-radius:6px;padding:1px 7px"
                if rule.get("action") == "discard"
                else "color:#3fb950;border:1px solid #238636;border-radius:6px;padding:1px 7px"
                if rule.get("action") != "lock"
                else "color:#d2a8ff;border:1px solid #8957e5;border-radius:6px;padding:1px 7px"
            )
        )
        layout.addWidget(type_badge, 0, Qt.AlignVCenter)
        layout.addWidget(action_badge, 0, Qt.AlignVCenter)
        for button, action in ((edit_button, "编辑规则"), (copy_button, "复制规则"), (delete_button, "删除规则")):
            button.setAccessibleName(text("ui.rules.accessible.action", action=ui_enum_text(getattr(self, "text_catalog", None), action), name=rule_name))
        edit_button.clicked.connect(
            lambda _checked=False, current=rule: self._edit_preserve_rule(self._rule_index(current))
        )
        copy_button.clicked.connect(
            lambda _checked=False, current=rule: self._duplicate_preserve_rule(self._rule_index(current))
        )
        delete_button.setStyleSheet("QPushButton{color:#f85149}")
        delete_button.clicked.connect(
            lambda _checked=False, current=rule: self._delete_preserve_rule(self._rule_index(current))
        )
        layout.addWidget(edit_button)
        layout.addWidget(copy_button)
        layout.addWidget(delete_button)
        return row

    def _rule_index(self, rule: dict) -> int:
        # 回调绑定草稿对象而非易变行号；重排后尚未退场的旧按钮也不会操作相邻规则。
        return next((i for i, value in enumerate(self._preserve_rules) if value is rule), -1)

    def _move_preserve_rule(self, index: int, offset: int) -> None:
        target = index + offset
        if index != target and 0 <= index < len(self._preserve_rules) and 0 <= target < len(self._preserve_rules):
            rule = self._preserve_rules.pop(index)
            self._preserve_rules.insert(target, rule)
            self._render_preserve_rules()

            # 等新行进入布局后恢复同一规则焦点，连续键盘移动不跳到首行。
            def restore_focus():
                row = next((row for value, row in self._rule_rows if value is rule), None)
                if row is not None:
                    row.findChild(RuleOrderHandle).setFocus(Qt.TabFocusReason)
                    self._rules_scroll.ensureWidgetVisible(row)

            QTimer.singleShot(0, self, restore_focus)
