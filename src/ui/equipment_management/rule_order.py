# 拥有规则展示排序的长按手势、源行反馈、插入落点与取消清理。
from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QSizePolicy
from shiboken6 import isValid

from src.app.theme import themed_style


class RuleOrderController(QObject):
    """编辑规则执行顺序草稿；首条命中动作由领域层按保存后的列表判定。"""

    def __init__(self, scroll, rows, move, *, contains=lambda point: True, text_catalog=None):
        super().__init__(scroll)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._scroll, self._rows, self._move = scroll, rows, move
        self._contains = contains
        self._rule = self._point = None
        self.target_index = None
        viewport = scroll.viewport()
        # 反馈浮在 viewport 上，不参与行布局、不截获鼠标，长列表滚动时也不会改变落点。
        self.indicator = QFrame(viewport)
        self.indicator.setObjectName("preserveRuleInsertionIndicator")
        self.indicator.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.indicator'))
        self.indicator.setStyleSheet(
            themed_style("QFrame#preserveRuleInsertionIndicator{background:#58a6ff;border:0;border-radius:2px;}")
        )
        self.hint = plain_label(viewport)
        self.hint.setObjectName("preserveRuleDragHint")
        self.hint.setStyleSheet(
            themed_style(
                "QLabel#preserveRuleDragHint{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;"
                "border-radius:5px;padding:5px 9px;font-weight:700;}"
            )
        )
        for widget in (self.indicator, self.hint):
            widget.setAttribute(Qt.WA_TransparentForMouseEvents)
            widget.hide()
        viewport.installEventFilter(self)
        scroll.verticalScrollBar().valueChanged.connect(self._render_preview)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and self._rule is not None:
            self._render_preview()
        return super().eventFilter(watched, event)

    def _destination(self, rule, point):
        viewport = self._scroll.viewport()
        if point is None or not viewport.rect().contains(viewport.mapFromGlobal(point)) or not self._contains(point):
            return None
        rows = list(self._rows())
        source = next((i for i, (value, _row) in enumerate(rows) if value is rule), -1)
        if source < 0:
            return None
        remaining = [row for value, row in rows if value is not rule]
        # 预览和释放共用这一事实源：先排除源身份，再按其他行中心划分前/后插入区。
        target = sum(point.y() > row.mapToGlobal(row.rect().center()).y() for row in remaining)
        if not remaining:
            gap = 0
        elif target == len(remaining):
            gap = remaining[-1].mapTo(viewport, remaining[-1].rect().bottomLeft()).y() + 4
        elif target == 0:
            gap = remaining[0].mapTo(viewport, remaining[0].rect().topLeft()).y() - 4
        else:
            above = remaining[target - 1].mapTo(viewport, remaining[target - 1].rect().bottomLeft()).y()
            below = remaining[target].mapTo(viewport, remaining[target].rect().topLeft()).y()
            gap = (above + below) // 2
        return source, target, gap

    @staticmethod
    def _source_feedback(row, active):
        if isValid(row) and bool(row.property("dragSource")) != active:
            row.setProperty("dragSource", active)
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()

    def preview(self, rule, point):
        self._rule, self._point = rule, point
        viewport = self._scroll.viewport()
        local = viewport.mapFromGlobal(point)
        if viewport.rect().contains(local) and (local.y() < 24 or local.y() > viewport.height() - 24):
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.value() + (-bar.singleStep() if local.y() < 24 else bar.singleStep()))
        # 滚动后重算实际行坐标；不复用滚动前的插入索引。
        self._render_preview()

    def _render_preview(self, *_args):
        if self._rule is None:
            return
        for rule, row in self._rows():
            self._source_feedback(row, rule is self._rule)
        destination = self._destination(self._rule, self._point)
        self.target_index = destination[1] if destination is not None else None
        viewport = self._scroll.viewport()
        if destination is None:
            suffix = resolve_text_catalog(self.text_catalog).render("ui.rules.drag_cancel")
            self.indicator.hide()
        else:
            source, target, gap = destination
            suffix = resolve_text_catalog(self.text_catalog).render("ui.rules.drag_position", position=target + 1) if target != source else resolve_text_catalog(self.text_catalog).render("ui.rules.drag_unchanged")
            self.indicator.setGeometry(0, max(0, min(gap - 2, viewport.height() - 4)), viewport.width(), 4)
            self.indicator.setVisible(target != source)
            self.indicator.raise_()
        name = self.hint.fontMetrics().elidedText(
            str(self._rule.get("name") or "未命名规则"), Qt.ElideRight, max(80, viewport.width() // 3)
        )
        self.hint.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment_management.render_preview.hint', name=f'{name}', suffix=ui_enum_text(getattr(self, 'text_catalog', None), f'{suffix}')))
        self.hint.adjustSize()
        self.hint.move(max(0, viewport.width() - self.hint.width() - 6), 6)
        self.hint.show()
        self.hint.raise_()

    def cancel(self):
        self._rule = self._point = self.target_index = None
        self.indicator.hide()
        self.hint.hide()
        for _rule, row in self._rows():
            self._source_feedback(row, False)

    def drop(self, rule, point):
        destination = self._destination(rule, point)
        self.cancel()
        if destination is not None:
            source, target, _gap = destination
            self._move(source, target - source)

    def move(self, rule, offset):
        index = next((i for i, (value, _row) in enumerate(self._rows()) if value is rule), -1)
        self._move(index, offset)


class RuleOrderHandle(QPushButton):
    """长按才启动拖动；原生焦点及 Ctrl+方向键始终可用。"""

    def __init__(self, order, rule, *, text_catalog=None):
        super().__init__("≡")
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.order, self.rule = order, rule
        self._pressed = self._armed = False
        self.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.self_2', value=f"{rule.get('name') or '未命名规则'}"))
        self.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.equipment_management.init.self_3')))
        self.setCursor(Qt.OpenHandCursor)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._hold = QTimer(self)
        self._hold.timeout.connect(self._arm)

    def _arm(self):
        if self._pressed:
            self._armed = True
            self.setCursor(Qt.ClosedHandCursor)
            self._hold.setInterval(60)
            self.order.preview(self.rule, self._point)

    def _cancel_drag(self):
        self._hold.stop()
        self._pressed = self._armed = False
        self.setDown(False)
        self.setCursor(Qt.OpenHandCursor)
        self.order.cancel()

    def event(self, event):
        if event.type() in (QEvent.UngrabMouse, QEvent.WindowDeactivate, QEvent.Hide) and hasattr(self, "_hold"):
            self._cancel_drag()
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed, self._armed = True, False
            self._point = event.globalPosition().toPoint()
            self._hold.start(QApplication.startDragTime())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed and self._armed:
            self._point = event.globalPosition().toPoint()
            self.order.preview(self.rule, self._point)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # 原生释放可能内部触发 UngrabMouse；先冻结合法手势再清瞬态，保留这次正确落点。
        armed = self._armed
        self._cancel_drag()
        super().mouseReleaseEvent(event)
        if armed and event.button() == Qt.LeftButton:
            self.order.drop(self.rule, event.globalPosition().toPoint())

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.order.move(self.rule, -1 if event.key() == Qt.Key_Up else 1)
            event.accept()
        elif event.key() == Qt.Key_Escape and self._pressed:
            self._cancel_drag()
            event.accept()
        else:
            super().keyPressEvent(event)
