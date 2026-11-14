# 验证前端翻译不改变动作 ID、规则配置、用户名称和正式数值投影，且文本目录保持有界。
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QDialog

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.localization.catalog import make_catalog
from src.features.battle_report.editing.control import BattleBuildSnapshotControl
from src.features.battle_report.analysis.view import BattleLongAnalysisView
from src.features.inventory.warehouse.management.management_dialog import WarehouseManagementDialog
from src.features.teams.editor import TeamEditor
from src.domain.teams.models import TeamRole
from src.features.settings.work_mode_card import report_summary
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.common.enum_text import ui_enum_text
from tests.warehouse.test_warehouse_management_ui import editor_data

NTE_TEST_TIER = 'core'


def english():
    base = bundled_text_catalog()
    values = {key: 'TEXT ' + key + ' ' + ' '.join('{' + name + '}' for name in sorted(base.parameters[key]))
              for key in base.messages}
    return make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture', messages=values), base=base)


def test_finite_caption_projection_is_frozen_and_preserves_ids_without_mutating_source():
    base, active = bundled_text_catalog(), english()
    source = {'all': '所有角色', 'selected': '所选角色'}
    original = deepcopy(source)
    view = ui_enum_text(active, source)
    assert set(view) == set(source) and source == original
    assert all(text.startswith('TEXT ') for text in view.values())
    assert ui_enum_text(base, source) == source
    assert ui_enum_text(active, 'synthetic backend error <tag>') == 'synthetic backend error <tag>'


def test_warehouse_translation_does_not_change_saved_settings_or_matching_domain():
    application()
    baseline = WarehouseManagementDialog(bundled_text_catalog(), config_dir=Path('config'))
    translated = WarehouseManagementDialog(english(), config_dir=Path('config'))
    try:
        for dialog in (baseline, translated):
            dialog.install(editor_data())
        assert baseline.collect() == translated.collect()
        for module in translated._widgets.values():
            assert module['role_scope'].currentData() in {'all', 'selected'}
            assert module['role_scope'].currentText().startswith('TEXT ')
        assert translated.tabs.tabText(0).startswith('TEXT ')
    finally:
        for dialog in (baseline, translated):
            QDialog.done(dialog, QDialog.Rejected)
            dispose(dialog)


def test_identical_translated_activation_captions_still_follow_boolean_state():
    application()
    base = bundled_text_catalog()
    messages = {key: 'Same label' for key, value in base.messages.items() if value in {'恢复原始快照', '使用修改副本'}}
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture', messages=messages), base=base)
    control = BattleBuildSnapshotControl(text_catalog=catalog)
    results = []
    control.activation_requested.connect(results.append)
    try:
        control.set_state(has_edit=True, active=False)
        control.activation_button.click()
        control.set_state(has_edit=True, active=True)
        control.activation_button.click()
        assert results == [True, False]
        assert control.edit_button.toolTip()
    finally:
        dispose(control)


def test_analysis_controls_keep_scope_ids_and_receive_same_catalog():
    application()
    catalog = english()
    view = BattleLongAnalysisView(text_catalog=catalog)
    try:
        assert set(view.scope_buttons) == {'current', 'first', 'second'}
        assert all(button.accessibleName().startswith('TEXT ') for button in view.scope_buttons.values())
        assert view.build_edit_control.text_catalog is catalog
        assert view.roles_table.horizontalHeaderItem(0).text().startswith('TEXT ')
    finally:
        dispose(view)


def test_user_role_names_are_not_looked_up_as_ui_captions():
    application()
    dialog = TeamEditor(roles=(TeamRole(1, '确定'),), catalog=english())
    try:
        assert dialog.role_buttons[1].text() == '确定'
        assert dialog.name_edit.placeholderText().startswith('TEXT ')
    finally:
        dispose(dialog)


def test_equal_state_labels_do_not_merge_different_inspection_states():
    base = bundled_text_catalog()
    messages = {key: 'Same' for key, value in base.messages.items() if value in {'可用', '正常等待'}}
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture', messages=messages), base=base)
    report = SimpleNamespace(features=tuple(SimpleNamespace(state=SimpleNamespace(value=value), facts=()) for value in ['available','waiting']))
    assert report_summary(report, text_catalog=catalog).count('Same') == 2


def test_numerical_projection_keeps_canonical_property_names():
    from src.features.inventory.equipment.display.plan_projection import official_stat_values
    value = official_stat_values([{'property_id':'AtkUp','value':0.125,'percent':True}])
    assert value == {'攻击力%': 12.5}




def test_incomplete_inspections_keep_the_original_single_summary_group():
    report = SimpleNamespace(features=tuple(SimpleNamespace(state=SimpleNamespace(value=value),
        facts=(("inspection_incomplete", True),)) for value in ['available', 'waiting']))
    text = report_summary(report)
    assert text.count('未完成检测') == 1 and '2 项' in text


def test_account_dialog_translates_captions_but_preserves_account_id_and_name(monkeypatch):
    from PySide6.QtWidgets import QComboBox, QLineEdit
    from src.features.accounts.manager import show_account_manager_dialog
    application()
    catalog = english()
    observed = []
    manager = SimpleNamespace(read_index=lambda: {'accounts': [{'id': 'fixture', 'name': '确定'}]},
                              account_meta=lambda _key: {'id': 'fixture', 'name': '确定'})
    def inspect_dialog(dialog):
        assert dialog.windowTitle().startswith('TEXT ')
        assert dialog.findChild(QComboBox).currentData() == 'fixture'
        assert dialog.findChild(QLineEdit).text() == '确定'
        observed.append(dialog)
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, 'exec', inspect_dialog)
    try:
        show_account_manager_dialog(None, '', manager, 'fixture', lambda *_: None, lambda: None,
                                    text_catalog=catalog)
        assert len(observed) == 1
    finally:
        for dialog in observed:
            QDialog.done(dialog, QDialog.Rejected)
            dispose(dialog)


def test_rule_summary_localizes_without_rewriting_conditions_or_backend_summary():
    from src.domain.equipment.warehouse.intelligent_rules import KINDS, dynamic_summary
    from src.ui.equipment_management.rule_text import _preserve_rule_summary
    active = english()
    for kind in KINDS:
        for target in (0, 1000):
            rule = dict(smart_kind=kind, grade='SSS', reserve=2, target_inventory_size=target)
            original = deepcopy(rule)
            assert _preserve_rule_summary(rule) == dynamic_summary(rule)
            assert _preserve_rule_summary(rule, text_catalog=active).startswith('TEXT ')
            assert rule == original
    manual = dict(item_type='tape', main_stats=['攻击力%', '生命值%', '防御力%'],
                  sub_stats=['暴击率%'], required_sub_stats=['暴击伤害%'], sub_match='all')
    original = deepcopy(manual)
    assert _preserve_rule_summary(manual) == '主：攻击力%、生命值% 等 3 项｜副：暴击率%（任意四个）｜必含：暴击伤害%'
    translated = _preserve_rule_summary(manual, text_catalog=active)
    assert 'TEXT ' in translated and '暴击率%' in translated and manual == original
