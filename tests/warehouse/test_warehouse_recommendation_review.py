# 验证建议卡片的冻结展示、身份勾选和复用比较弹窗，不依赖像素或装饰细节。
from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QPushButton, QTextBrowser, QToolButton, QWidget

from src.domain.equipment.warehouse.warehouse_review import enrich_review_rows, no_worse, retained_candidates, review_score, stat_changes
from src.features.inventory.warehouse.management.recommendation_review import RecommendationReview
from src.features.inventory.warehouse.display.result_card import WarehouseResultCard


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def data():
    rows = []
    for uid, kind, score, area, old in [('large', 'drive', 30, 4, False), ('small', 'drive', 25, 3, False),
                                      ('tape', 'tape', 100, 15, False), ('old', 'drive', 18, 2, True),
                                      ('unknown', 'drive', None, 3, False)]:
        rows.append(dict(uid=uid, kind=kind, area=area, shape_id=f'H_{area}', set_name='套装', suit_id='suit',
            main_stat='攻击力%' if kind == 'tape' else '', quality='Gold', stats={'攻击力%': 1},
            decision='discard', original_discarded=old, reasons=['有保留替代'], replacement_uids=[],
            best_current_score=score, best_current_grade='SS' if score is not None else None,
            current_scores={'角色甲': score} if score is not None else {}))
    return rows


def cleanup(view, app):
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize('theme', ["black"])
def test_filters_order_and_hidden_checks_preserve_identity(app, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    before = current_theme_name()
    apply_app_theme(app, theme)
    view = RecommendationReview()
    try:
        rows = data()
        targets = {r['uid']: 'discarded' for r in rows if not r['original_discarded']}
        view.set_result({'rows': rows}, targets)
        assert view.model.visible_uids == ['small', 'large', 'tape', 'unknown']
        assert view.checked_targets() == targets
        view.model.set_checked('small', False)
        view.kind.setCurrentIndex(view.kind.findData('tape'))
        view.order.setCurrentIndex(1)
        assert view.model.visible_uids == ['tape']
        assert view.checked_targets() == {k:v for k,v in targets.items() if k != 'small'}
        view.uncheck_visible.click()
        assert view.checked_targets() == {'large': 'discarded', 'unknown': 'discarded'}
        view.kind.setCurrentIndex(0)
        view.action.setCurrentIndex(view.action.findData('old_discard'))
        assert view.model.visible_uids == ['old']
        assert not view.model.set_checked('old', True)
        view.action.setCurrentIndex(view.action.findData('new_discard'))
        view.search.setText('角色甲')
        assert set(view.model.visible_uids) == {'small', 'large', 'tape'}
    finally:
        cleanup(view, app)
        apply_app_theme(app, before)


def test_mouse_and_keyboard_separate_adoption_from_details(app):
    view = RecommendationReview()
    opened = []
    view.delegate.details_requested.disconnect()
    view.grid.details_requested.disconnect()
    view.delegate.details_requested.connect(opened.append)
    view.grid.details_requested.connect(opened.append)
    try:
        row = data()[0]
        view.set_result({'rows': [row]}, {'large':'discarded'})
        view.resize(1000, 600)
        view.show()
        app.processEvents()
        index = view.model.index(0)
        rect = view.grid.visualRect(index)
        QTest.mouseClick(view.grid.viewport(), Qt.LeftButton, pos=view.delegate.check_rect(rect).center())
        assert view.checked_targets() == {} and opened == []
        QTest.mouseClick(view.grid.viewport(), Qt.LeftButton, pos=rect.center())
        assert opened == ['large'] and view.checked_targets() == {}
        view.grid.setCurrentIndex(index)
        QTest.keyClick(view.grid, Qt.Key_Space)
        assert view.checked_targets() == {'large':'discarded'}
        QTest.keyClick(view.grid, Qt.Key_Return)
        assert opened == ['large', 'large']
    finally:
        cleanup(view, app)


def test_shared_comparison_uses_same_role_and_changes_only_adoption(app, monkeypatch):
    current = {**data()[0], 'replacement_uids':['kept'], 'reasons':['完整的取舍原因'],
               'stats': {'攻击力%': 1, '暴击伤害%': 20}}
    kept = {**deepcopy(current), 'uid':'kept', 'decision':'keep', 'stats':{'攻击力%':2},
            'best_current_score':100, 'current_scores':{'角色甲':35, '角色乙':100},
            'current_grades':{'角色甲':'S', '角色乙':'SSS'}}
    view = RecommendationReview()
    captured = []
    def inspect(dialog):
        dialog.show()
        app.processEvents()
        try:
            assert '完整的取舍原因' in dialog.findChild(QTextBrowser).toPlainText()
            assert not any(b.isVisible() and b.text() == '确定替换' for b in dialog.findChildren(QPushButton))
            group = dialog.findChild(QWidget, 'equipmentReplacementCandidateGroup')
            reasons = dialog.findChild(QTextBrowser)
            assert not reasons.isVisible()
            toggle = dialog.findChild(QToolButton, 'equipmentReviewReasonsToggle')
            available_height = group.height()
            toggle.setFocus()
            QTest.keyClick(toggle, Qt.Key_Space)
            app.processEvents()
            assert reasons.isVisible() and group.height() < available_height
            toggle.click()
            app.processEvents()
            assert not reasons.isVisible()
            card = group.findChild(WarehouseResultCard)
            assert card.score == 35 and card.grade == 'S'
            QTest.mouseClick(card, Qt.LeftButton)
            compared = dialog.findChild(WarehouseResultCard, 'equipmentReplacementCurrentPreview')
            assert compared.score == 30 and compared.ratio_only
            assert compared.item_view['sub_stats'][0]['comparison_background'] == '#f85149'
            differences = dialog.findChild(QLabel, 'equipmentReplacementComparison').text()
            assert '↑ 提升' in differences and '+1%' in differences
            assert '↓ 降低' in differences and '-20%' in differences
            dialog.findChild(QCheckBox, 'equipmentReviewAdopt').click()
            captured.append(True)
        finally:
            dialog.reject()
        return dialog.result()
    monkeypatch.setattr(QDialog, 'exec', inspect)
    frozen = deepcopy([current, kept])
    try:
        view.set_result({'rows':[current,kept]}, {'large':'discarded'})
        view.show_candidates('large')
        assert captured and view.checked_targets() == {}
        assert [current,kept] == frozen
    finally:
        cleanup(view, app)


def test_frozen_card_has_full_stats_and_does_not_forge_ownership(app):
    row = {**data()[0], 'item_name':'正式驱动名称', 'main_stats':{'攻击力':120,'生命值':600},
           'stats':{'暴击伤害%':20,'暴击率%':10,'攻击力%':12.5,'伤害增加%':10},
           'reasons':['完整取舍原因'], 'similar_reserve_trimmed':True}
    view = RecommendationReview()
    try:
        view.set_result({'rows':[row]}, {'large':'discarded'})
        card = view.model.views['large']
        assert card['display_name'] == '正式驱动名称'
        assert len(card['main_stats']) == 2 and len(card['sub_stats']) == 4
        assert card['recommended_character_name'] == '角色甲' and not card['equipped']
        assert '75.0%' in view.model.index(0).data(Qt.AccessibleTextRole)
        assert len(view.model.bases['large']) <= 8
        view.search.setText('完整取舍原因')
        assert view.model.visible_uids == ['large']
    finally:
        cleanup(view, app)


def test_no_candidate_reason_and_adoption_remain_available(app, monkeypatch):
    view = RecommendationReview()
    observed = []
    def inspect(dialog):
        dialog.show()
        try:
            assert dialog.findChild(QTextBrowser).toPlainText() == '有保留替代'
            dialog.findChild(QCheckBox).click()
            observed.append(True)
        finally:
            dialog.reject()
        return dialog.result()
    monkeypatch.setattr(QDialog, 'exec', inspect)
    try:
        view.set_result({'rows':[data()[0]]}, {'large':'discarded'})
        view.show_candidates('large')
        assert observed and view.checked_targets() == {}
    finally:
        cleanup(view, app)


def test_comparison_colors_use_current_role_not_each_candidates_best_role(app, monkeypatch):
    raw = dict(kind='module',quality='orange',level=0,main_stats=[],
               sub_stats=[dict(property_id='CritBase',value=.1,percent=True)])
    current = {**data()[0], 'display_item':raw, 'replacement_uids':['kept']}
    kept = {**deepcopy(current), 'uid':'kept','decision':'keep','current_scores':{'角色甲':35,'角色乙':100}}
    profiles = {'角色甲':{'sub':{'CritBase':.9}},'角色乙':{'sub':{'CritBase':.2}}}
    view=RecommendationReview()
    checked=[]
    def inspect(dialog):
        dialog.show()
        try:
            group=dialog.findChild(QWidget,'equipmentReplacementCandidateGroup')
            candidate=group.findChild(WarehouseResultCard)
            assert candidate.item_view['sub_stats'][0]['weight']==.9
            assert '0.9' in candidate.toolTip()
            checked.append(True)
        finally:
            dialog.reject()
        return dialog.result()
    monkeypatch.setattr(QDialog,'exec',inspect)
    try:
        view.set_result({'rows':[current,kept],'stat_weight_profiles':profiles},{'large':'discarded'})
        assert view.model.views['kept']['sub_stats'][0]['weight']==.2
        view.show_candidates('large')
        assert checked
    finally:
        cleanup(view,app)


def test_tradeoff_and_property_comparison_preserve_facts():
    original = {**data()[2], 'main_stat':'暴击伤害%', 'stats':{'暴击伤害%':20}}
    other = {**deepcopy(original), 'uid':'kept', 'decision':'keep', 'stats':{'暴击率%':10}}
    assert not no_worse(original, other)
    assert stat_changes(original, other) == {'暴击伤害%':-20, '暴击率%':10}
    other['stats'] = {'暴击伤害%':25}
    source = [original, other]
    frozen = deepcopy(source)
    rows = enrich_review_rows(source)
    lookup = {r['uid']:r for r in rows}
    assert source == frozen
    assert [r['uid'] for r in retained_candidates(lookup[original['uid']], lookup)] == ['kept']
    assert review_score(other, '角色甲') == 100
