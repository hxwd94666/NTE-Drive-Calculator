# 用仓库卡片审查冻结建议，筛选和比较始终按装备身份保持采用状态。
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from src.domain.equipment.warehouse.warehouse_review import (
    adopted_candidates,
    best_role,
    discard_basis,
    enrich_review_rows,
    no_worse,
    review_ratio,
    review_score,
    stat_changes,
)
from src.features.inventory.warehouse.display.recommendation_cards import (
    RecommendationCardDelegate,
    RecommendationCardModel,
    RecommendationGrid,
    recommendation_item_view,
)
from src.ui.equipment.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    EquipmentReviewOptions,
    show_equipment_replacement_dialog,
)


class RecommendationReview(QWidget):
    def __init__(self, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.by_uid, self.targets = {}, {}
        self.weight_profiles = {}
        self.model = RecommendationCardModel(self)
        root = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(resolve_text_catalog(text_catalog).render('ui.inventory.init.search'))
        self.kind = QComboBox()
        for label, key in [('全部类型', ''), ('空幕', 'tape'), ('驱动', 'drive')]:
            self.kind.addItem(ui_enum_text(text_catalog, label), key)
        self.action = QComboBox()
        for label, key in [('本次新增弃置', 'new_discard'), ('所有弃置建议', 'discard'),
                           ('原已弃置且仍建议弃置', 'old_discard'), ('建议锁定', 'lock'),
                           ('保留', 'keep'), ('待核对', 'review'), ('全部建议', '')]:
            self.action.addItem(ui_enum_text(text_catalog, label), key)
        self.order = QComboBox()
        self.order.addItem(resolve_text_catalog(text_catalog).render('ui.inventory.init.order'), 'descending')
        self.order.addItem(resolve_text_catalog(text_catalog).render('ui.inventory.init.order_2'), 'ascending')
        self.kind.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.inventory.init.kind'))
        self.action.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.inventory.init.action'))
        self.order.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.inventory.init.order_3'))
        for control in (self.search, self.kind, self.action, self.order):
            filters.addWidget(control, 1 if control is self.search else 0)
        root.addLayout(filters)
        hint = plain_label(resolve_text_catalog(text_catalog).render('ui.inventory.init.hint'))
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.grid = RecommendationGrid(self, text_catalog=text_catalog)
        self.grid.setModel(self.model)
        self.delegate = RecommendationCardDelegate(self.grid, text_catalog=text_catalog)
        self.grid.setItemDelegate(self.delegate)
        self.delegate.details_requested.connect(self.show_candidates)
        self.grid.details_requested.connect(self.show_candidates)
        root.addWidget(self.grid, 1)
        self.summary = plain_label()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        actions = QHBoxLayout()
        self.details = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.details'))
        self.details.setEnabled(False)
        self.details.clicked.connect(lambda: self.show_candidates(self.grid.currentIndex().data(Qt.UserRole)))
        actions.addWidget(self.details, 1)
        self.check_visible = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.check_visible'))
        self.uncheck_visible = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.uncheck_visible'))
        self.check_visible.clicked.connect(lambda: self.set_visible_checked(True))
        self.uncheck_visible.clicked.connect(lambda: self.set_visible_checked(False))
        actions.addWidget(self.check_visible)
        actions.addWidget(self.uncheck_visible)
        root.addLayout(actions)
        self.search.textChanged.connect(self.filter_rows)
        for combo in (self.kind, self.action, self.order):
            combo.currentIndexChanged.connect(self.filter_rows)
        self.model.checked_changed.connect(self.update_summary)
        self.grid.selectionModel().currentChanged.connect(lambda *_: self.details.setEnabled(self.grid.currentIndex().isValid()))

    def clear(self):
        self.by_uid, self.targets = {}, {}
        self.weight_profiles = {}
        self.model.rows, self.model.views, self.model.bases, self.model.targets = {}, {}, {}, {}
        self.model.checked.clear()
        self.model.set_visible([])
        self.details.setEnabled(False)
        self.summary.clear()

    def set_result(self, result, targets):
        self.weight_profiles = result.get('stat_weight_profiles') or {}
        self.by_uid = {row['uid']: row for row in enrich_review_rows(result['rows'])}
        self.targets = {uid: value for uid, value in targets.items() if uid in self.by_uid}
        self.model.rows, self.model.targets = self.by_uid, self.targets
        self.model.views = {uid: recommendation_item_view(row, self.weight_profiles.get(best_role(row)), text_catalog=getattr(self, 'text_catalog', None))
                            for uid, row in self.by_uid.items()}
        self.model.bases = {uid: discard_basis(row, self.by_uid) for uid, row in self.by_uid.items()}
        self.model.checked = set(self.targets)
        self.filter_rows()

    def filter_rows(self, *_):
        needle, kind, action = self.search.text().casefold(), self.kind.currentData(), self.action.currentData()
        visible = []
        for uid, row in self.by_uid.items():
            matches = (not action or row['decision'] == action
                       or action == 'new_discard' and row['decision'] == 'discard' and not row['original_discarded']
                       or action == 'old_discard' and row['decision'] == 'discard' and row['original_discarded'])
            view = self.model.views[uid]
            text = ' '.join([view['display_name'], row['shape_id'], best_role(row) or '', self.model.bases[uid],
                             *(str(s['label']) for s in [*view['main_stats'], *view['sub_stats']]), *row['reasons']]).casefold()
            if matches and (not kind or row['kind'] == kind) and needle in text:
                visible.append(uid)
        descending = self.order.currentData() == 'descending'
        # 未评估始终放末尾，同分保持冻结库存顺序。
        def sort_key(uid):
            ratio = review_ratio(self.by_uid[uid])
            return ratio is None, (-ratio if descending else ratio) if ratio is not None else 0
        visible.sort(key=sort_key)
        self.model.set_visible(visible)
        self.details.setEnabled(False)
        self.update_summary()

    def checked_targets(self):
        return {uid: state for uid, state in self.targets.items() if uid in self.model.checked}

    def set_visible_checked(self, checked):
        eligible = set(self.model.visible_uids) & self.targets.keys()
        self.model.checked.update(eligible) if checked else self.model.checked.difference_update(eligible)
        self.grid.viewport().update()
        self.update_summary()

    def update_summary(self, *_):
        chosen = self.checked_targets()
        counts = {state: sum(v == state for v in chosen.values()) for state in ('locked', 'discarded', 'normal')}
        self.summary.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.update_summary.summary', value=f'{len(self.model.visible_uids)}', value_2=f"{counts['discarded']}", value_3=f"{counts['locked']}", value_4=f"{counts['normal']}"))

    def show_candidates(self, uid):
        if uid not in self.by_uid:
            return
        frozen = self.by_uid
        row, role = frozen[uid], best_role(frozen[uid])
        others = adopted_candidates(row, frozen, self.checked_targets())
        def card(value):
            score = review_score(value, role) if role else None
            area = value.get('area')
            ratio = score / (area * 10) if score is not None and area else None
            changes = stat_changes(row, value)
            note = '同类品质及副属性不差' if no_worse(row, value) else '属性取舍，不能直接替代'
            view = {**recommendation_item_view(value, self.weight_profiles.get(role), text_catalog=getattr(self, 'text_catalog', None)), 'recommended_character_name': role,
                    'recommended_character_icon_path': row.get('best_role_icon_path')}
            grade = (value.get('current_grades') or {}).get(role)
            if grade is None and role and role == best_role(value):
                grade = value.get('best_current_grade')
            return EquipmentReplacementCard(value['uid'], view, score, grade, None, None, note, ratio, tuple(changes.items()))
        rule = row.get('winning_rule') or {}
        reason = ('命中第 ' + str(rule['position']) + ' 条规则\n') if rule.get('position') is not None else ''
        reason += '\n'.join(row['reasons'])
        show_equipment_replacement_dialog(self, title=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.show_candidates.caption'), role_name=role or '未确认',
            summary=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.show_candidates.caption_2'),
            current=card(row), candidates=others, candidate_adapter=card,
            is_current=lambda: self.by_uid is frozen,
            review=EquipmentReviewOptions(reason, uid in self.model.checked, uid in self.targets,
                                          lambda checked: self.model.set_checked(uid, checked) if self.by_uid is frozen else None), text_catalog=getattr(self, 'text_catalog', None))
