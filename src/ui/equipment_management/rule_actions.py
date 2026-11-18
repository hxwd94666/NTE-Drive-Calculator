# 维护规则列表的用户编辑、复制和删除决定，不隐式保存或执行游戏操作。
from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
import copy
from PySide6.QtWidgets import QDialog, QMessageBox
from src.domain.inventory.post_actions import DEFAULT_PRESERVE_RULE
from src.domain.equipment.warehouse.intelligent_rules import preserve_origin
from src.ui.equipment_management.rule_editor import PreserveRuleEditor


class PreserveRuleActionsMixin:
    def _set_preserve_rule_enabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self._preserve_rules):
            old = self._preserve_rules[index]
            updated = preserve_origin(old, {**old, "enabled": enabled})
            old.clear()
            old.update(updated)

    def _add_preserve_rule(self) -> None:
        editor = PreserveRuleEditor(
            self,
            DEFAULT_PRESERVE_RULE,
            self._shape_options,
            self._set_options,
            self.config_dir,
            text_catalog=getattr(self, 'text_catalog', None),
        )
        if editor.exec() != QDialog.Accepted:
            return
        rule = editor.result_rule()
        if rule is not None:
            self._preserve_rules.append(rule)
            self._render_preserve_rules()

    def _edit_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        original = self._preserve_rules[index]
        if original.get("smart_kind"):
            from src.ui.equipment_management.intelligent_rule_editor import IntelligentRuleEditor

            editor = IntelligentRuleEditor(self, original)
        else:
            editor = PreserveRuleEditor(self, original, self._shape_options, self._set_options, self.config_dir, text_catalog=getattr(self, 'text_catalog', None))
        if editor.exec() != QDialog.Accepted:
            return
        rule = editor.result_rule()
        if rule is not None:
            self._preserve_rules[index] = preserve_origin(original, rule)
            self._render_preserve_rules()

    def _duplicate_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        duplicate = copy.deepcopy(self._preserve_rules[index])
        duplicate.pop("intelligent", None)  # 副本归用户所有，重新生成不能替换它。
        if duplicate.get("smart_kind"):
            duplicate["enabled"] = False  # 同种动态策略只允许启用一条。
        duplicate["name"] = localized_text(getattr(self, 'text_catalog', None), 'auto.ui.equipment_management.rule_actions.duplicate_preserve_rule.f5258bebc', '{value} 副本', value=f"{duplicate.get('name') or ui_enum_text(getattr(self, 'text_catalog', None), '未命名规则')}")
        self._preserve_rules.insert(index + 1, duplicate)
        self._render_preserve_rules()

    def _delete_preserve_rule(self, index: int) -> None:
        if not 0 <= index < len(self._preserve_rules):
            return
        if QMessageBox.question(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.delete_preserve_rule.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.delete_preserve_rule.qmessagebox_2'))) != QMessageBox.Yes:
            return
        del self._preserve_rules[index]
        self._render_preserve_rules()
