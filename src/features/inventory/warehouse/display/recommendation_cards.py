# 以冻结装备身份驱动虚拟仓库卡片，勾选只表示采用本次建议。
from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import resolve_text_catalog
from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListView, QStyle, QStyledItemDelegate, QStyleOptionButton

from src.domain.equipment.warehouse.warehouse_review import best_role, review_ratio
from src.features.inventory.warehouse.display.cards import WarehouseGridView, warehouse_item_view
from src.features.inventory.warehouse.display.result_card import paint_warehouse_result_card


DECISION_NAMES = {'lock': '锁定', 'keep': '保留', 'discard': '弃置', 'review': '待核对'}


def recommendation_item_view(row, weights=None, *, text_catalog=None):
    """只投影已冻结的显示事实；不查询账号、静态库或重新评分。"""
    if row.get('display_item'):
        view = warehouse_item_view(row['display_item'], frozen_projection=True, text_catalog=text_catalog)
        view['main_stats_at_max_level'] = bool(row['display_item'].get('main_stats_at_max_level'))
    else:
        # 缺少展示资料时保持未知，不假定等级或补造主属性数值。
        def stat(key, value, main=False):
            return {'label': key, 'value': f'{value:g}', 'main': main}
        view = dict(kind='core' if row['kind'] == 'tape' else 'module',
                    quality=row.get('quality', '').lower(), level_known=False, frozen_projection=True,
                    main_stats=[stat(k, v, True) for k, v in (row.get('main_stats') or {}).items()],
                    sub_stats=[stat(k, v) for k, v in (row.get('stats') or {}).items()])
        if row['kind'] == 'tape' and row.get('main_stat'):
            view['main_stats'] = [{'label': row['main_stat'], 'value': '未知', 'main': True}]
    for field, group in (('main_stats', 'main'), ('sub_stats', 'sub')):
        for stat in view[field]:
            weight = (weights or {}).get(group, {}).get(stat.get('property_id'))
            if weight is not None:
                stat['weight'] = weight
    view.update(display_name=(row.get('set_name') or '空幕') if row['kind'] == 'tape'
                else row.get('item_name') or row['shape_id'],
                recommended_character_name=best_role(row),
                recommended_character_icon_path=row.get('best_role_icon_path'),
                equipped=False, state_known=False)
    return view


class RecommendationCardModel(QAbstractListModel):
    checked_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.views, self.bases, self.targets, self.checked, self.visible_uids = {}, {}, {}, {}, set(), []

    def set_visible(self, uids):
        self.beginResetModel()
        self.visible_uids = list(uids)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible_uids)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.visible_uids):
            return None
        uid = self.visible_uids[index.row()]
        row = self.rows[uid]
        if role == Qt.UserRole:
            return uid
        if role == Qt.CheckStateRole and uid in self.targets:
            return Qt.Checked if uid in self.checked else Qt.Unchecked
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole, Qt.ToolTipRole):
            ratio = review_ratio(row)
            rate = '未评估' if ratio is None else f'{ratio:.1%}'
            view = self.views[uid]
            return (localized_text(getattr(self, 'text_catalog', None), 'auto.features.inventory.warehouse.display.recommendation_cards.data.f2fad306b', '{value} · {value_2}\n最佳适配：{value_3} · {value_4} · {rate}\n', value=f"{view['display_name']}", value_2=f"{DECISION_NAMES[row['decision']]}", value_3=f"{best_role(row) or ui_enum_text(getattr(self, 'text_catalog', None), '未确认')}", value_4=f"{row.get('best_current_grade') or ui_enum_text(getattr(self, 'text_catalog', None), '未评估')}", rate=f'{rate}')
                    + (localized_text(getattr(self, 'text_catalog', None), 'auto.features.inventory.warehouse.display.recommendation_cards.data.f6c8e7ebc', '实际等级 {value}；主属性按满级展示\n', value=f"{view['level']}") if view.get('main_stats_at_max_level') else '')
                    + '\n'.join(f"{s['label']} {s['value']}" + (localized_text(getattr(self, 'text_catalog', None), 'auto.features.inventory.warehouse.display.recommendation_cards.data.f91818961', '（权重 {value}）', value=f"{s['weight']:g}") if 'weight' in s else '')
                                for s in [*view['main_stats'], *view['sub_stats']])
                    + '\n' + self.bases[uid])
        return None

    def flags(self, index):
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return flags | (Qt.ItemIsUserCheckable if index.data(Qt.UserRole) in self.targets else Qt.NoItemFlags)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        return self.set_checked(index.data(Qt.UserRole), value == Qt.Checked)

    def set_checked(self, uid, checked):
        if uid not in self.targets:
            return False
        self.checked.add(uid) if checked else self.checked.discard(uid)
        if uid in self.visible_uids:
            index = self.index(self.visible_uids.index(uid))
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.checked_changed.emit()
        return True


class RecommendationCardDelegate(QStyledItemDelegate):
    details_requested = Signal(str)
    CARD_HEIGHT = 294

    @staticmethod
    def check_rect(rect):
        return QRect(rect.left() + 12, rect.top() + 3, 90, 25)

    def sizeHint(self, option, index):
        return QSize(280, self.CARD_HEIGHT)

    def paint(self, painter, option, index):
        model, uid = index.model(), index.data(Qt.UserRole)
        row = model.rows[uid]
        painter.save()
        paint_warehouse_result_card(painter, option.rect.adjusted(0, 26, 0, 0), model.views[uid],
            grade=row.get('best_current_grade') or '', ratio_only=True, score_ratio=review_ratio(row),
            selected=bool(option.state & QStyle.State_Selected),
            footer_note=' · '.join(filter(None, [DECISION_NAMES[row['decision']], model.bases[uid]])),
            device_pixel_ratio=option.widget.devicePixelRatioF() if option.widget else 1.0, text_catalog=getattr(self, 'text_catalog', None))
        check = QStyleOptionButton()
        check.rect = self.check_rect(option.rect)
        check.text = '采用' if uid in model.targets else '无需操作'
        check.state = (QStyle.State_Enabled if uid in model.targets else QStyle.State_None)
        check.state |= QStyle.State_On if uid in model.checked else QStyle.State_Off
        check.palette = option.palette
        (option.widget.style() if option.widget else QApplication.style()).drawControl(QStyle.CE_CheckBox, check, painter)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            uid = index.data(Qt.UserRole)
            if self.check_rect(option.rect).contains(event.position().toPoint()):
                model.set_checked(uid, uid not in model.checked)
            else:
                self.details_requested.emit(uid)
            return True
        return super().editorEvent(event, model, option, index)

    def __init__(self, *args, text_catalog=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_catalog = resolve_text_catalog(text_catalog)



class RecommendationGrid(WarehouseGridView):
    details_requested = Signal(str)

    def __init__(self, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.inventory.init.self_2'))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(1, self.viewport().width() - 16)
        columns = max(1, width // 280)
        self.setGridSize(QSize(width // columns, RecommendationCardDelegate.CARD_HEIGHT))

    def keyPressEvent(self, event):
        index = self.currentIndex()
        if index.isValid() and event.key() == Qt.Key_Space:
            uid = index.data(Qt.UserRole)
            self.model().set_checked(uid, uid not in self.model().checked)
            event.accept()
        elif index.isValid() and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.details_requested.emit(index.data(Qt.UserRole))
            event.accept()
        else:
            super().keyPressEvent(event)
