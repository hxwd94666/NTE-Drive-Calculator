# 在设置中展示性能开关、服务耗时曲线和排错记录状态。
from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import QPointF, QSize, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QTabWidget, QCheckBox, QComboBox, QDialog, QHBoxLayout, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.settings.performance.trace_view import PerformanceTraceView
from src.ui.common.enum_text import ui_enum_text


STATE_TEXT = {
    "off": "已关闭", "waiting": "等待性能数据；等待原生组件，不会自动启动同步或战报",
    "unsupported": "当前组件未提供服务耗时计数",
    "unavailable": "当前模式或暂停状态不允许原生性能观测",
    "collecting": "正在观测性能",
    "fault": "性能读取失败；下次采样重试",
}
SERVICE_NAMES = {"snapshot_pulse": "快照周期", "snapshot_read": "快照分批读取"}
SERVICE_NAMES.update({
    "snapshot.clock_roots_before": "战斗时钟 · 查询前身份校验",
    "snapshot.clock_function": "战斗时钟 · 函数校验",
    "snapshot.clock_queries": "战斗时钟 · 单次暂停查询",
    "snapshot.clock_roots_after": "战斗时钟 · 查询后身份复核",
})
_READ_STAGES = {
    "job_step": "读取批次", "step_precheck": "读取前校验", "reader_step": "数据读取",
    "step_postcheck": "读取后校验", "character_init": "角色初始化", "character_validate": "角色校验",
    "forks": "弧盘", "equipment_index": "装备索引", "character_rows": "角色条目",
    "character_base": "角色基础", "skills": "技能", "skill_query": "技能等级查询",
    "awakening": "觉醒", "awakening_definitions": "觉醒定义", "slots": "装备槽位",
    "other_fields": "其他养成", "related_equipment": "关联装备", "row_finalize": "条目收尾",
    "verify": "完整性复核", "domain_roots": "数据根读取", "domain_identity": "数据身份", "domain_clone": "角色存档",
}
_STAGE_OWNERS = (("user.snapshot.", "账号读取"), ("snapshot.", "采集"))
SERVICE_NAMES.update({prefix + key: f"{owner} · {label}" for prefix, owner in _STAGE_OWNERS
                      for key, label in _READ_STAGES.items()})
_CLOCK_PREFIX = "战斗时钟 · "


def service_label(name, catalog=None):
    """服务计时名按“归属 · 阶段”分段投影译文；未登记的协议名原样显示。"""
    for prefix, owner in _STAGE_OWNERS:
        stage = name[len(prefix):] if name.startswith(prefix) else None
        if stage in _READ_STAGES:
            return f"{ui_enum_text(catalog, owner)} · {ui_enum_text(catalog, _READ_STAGES[stage])}"
    text = SERVICE_NAMES.get(name, name)
    if text.startswith(_CLOCK_PREFIX):
        return f"{ui_enum_text(catalog, '战斗时钟')} · {ui_enum_text(catalog, text[len(_CLOCK_PREFIX):])}"
    return ui_enum_text(catalog, text)


class CostPlot(QWidget):
    def __init__(self, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.points = []
        self.setMinimumHeight(130)
        self.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.settings.init.self_2'))

    def paintEvent(self, event):
        # 无样本保留间断，既有服务耗时不冒充 FPS 或零耗时。
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.palette().text().color())
        values = [value for _, value in self.points if value is not None]
        if not values:
            painter.drawText(self.rect(), Qt.AlignCenter, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.paintevent.painter_2'))
            return
        maximum = max(max(values), 0.001)
        painter.drawText(8, 18, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.paintevent.painter', maximum=f'{maximum:.3f}'))
        area = self.rect().adjusted(12, 30, -12, -16)
        first, last = self.points[0][0], self.points[-1][0]
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        previous = None
        for timestamp, value in self.points:
            if value is None:
                previous = None
                continue
            point = QPointF(area.left() + area.width() * (timestamp-first) / max(last-first, 1),
                            area.bottom() - area.height() * value / maximum)
            if previous is not None:
                painter.drawLine(previous, point)
            painter.drawEllipse(point, 2, 2)
            previous = point


class PerformanceDetails(QDialog):
    def __init__(self, controller, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.settings.init.self_3'))
        self.controller = controller
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        overview = QWidget()
        tabs.addTab(overview, resolve_text_catalog(text_catalog).render('ui.settings.init.tabs'))
        self.trace_view = PerformanceTraceView(controller, text_catalog=text_catalog)
        tabs.addTab(self.trace_view, resolve_text_catalog(text_catalog).render('ui.settings.init.tabs_2'))
        layout = QVBoxLayout(overview)
        self.status = plain_label()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.metrics = plain_label()
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        note = plain_label(resolve_text_catalog(text_catalog).render('ui.settings.init.note'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.service = QComboBox()
        self.service.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.settings.init.service'))
        self.service.setPlaceholderText(resolve_text_catalog(text_catalog).render('ui.settings.init.service_2'))
        layout.addWidget(self.service)
        self.plot = CostPlot(text_catalog=text_catalog)
        layout.addWidget(self.plot)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([resolve_text_catalog(text_catalog).render('ui.settings.init.table'), resolve_text_catalog(text_catalog).render('ui.settings.init.table_2'), resolve_text_catalog(text_catalog).render('ui.settings.init.table_3'), resolve_text_catalog(text_catalog).render('ui.settings.init.table_4')])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.path = plain_label()
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path)
        row = QHBoxLayout()
        folder = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.folder'))
        folder.clicked.connect(self.open_logs)
        row.addWidget(folder)
        close = TextActionButton(resolve_text_catalog(text_catalog).render('common.close'))
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.service.currentIndexChanged.connect(lambda: self.render(controller.snapshot()))

    def open_logs(self):
        directory = self.controller.log_dir / "performance"
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.path.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.open_logs.path'))

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(820, 570))

    def render(self, value):
        self.trace_view.render(value.get('trace', {}))
        overlay = {"off": "悬浮窗已关闭", "closing": "正在关闭显示", "visible": "悬浮窗已绘制", "waiting": "已请求显示，等待游戏绘制",
                   "unsupported": "当前组件不支持性能悬浮窗，需要配套更新", "rejected": "当前游戏版本不支持性能绘制",
                   "unconfirmed": "悬浮窗状态未确认，等待连接恢复"}.get(value.get("overlay_state"), "等待组件")
        catalog = getattr(self, 'text_catalog', None)
        summary = (ui_enum_text(catalog, "四项悬浮窗及其日志已关闭。") if not value["enabled"] and value.get("overlay_state") == "off"
                   else ui_enum_text(catalog, overlay) + "；" + status_text(value, catalog))
        frame_error = value.get("frames", {}).get("frame_error")
        if value["enabled"] and frame_error:
            summary += "；" + ui_enum_text(catalog, frame_error)
        self.status.setText(ui_enum_text(catalog, value.get("preference_error")) or summary + trace_status_suffix(value, catalog))
        metrics = value.get("frames", {})
        def number(key):
            v = metrics.get(key)
            return f"{v / 1000:.2f}" if type(v) is int else "--"
        self.metrics.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render.metrics', value=f"{number('fps_milli')}", value_2=f"{number('frame_us')}", value_3=f"{number('low_milli')}", value_4=f"{number('cost_us')}"))
        rows = value["rows"]
        selected = self.service.currentData()
        names = list(rows)
        if names != [self.service.itemData(i) for i in range(self.service.count())]:
            self.service.blockSignals(True)
            self.service.clear()
            for name in names:
                self.service.addItem(service_label(name, catalog), name)
            self.service.setCurrentIndex(max(0, self.service.findData(selected)))
            self.service.blockSignals(False)
        self.service.setEnabled(bool(names))
        selected = self.service.currentData()
        self.plot.points = [(t, data.get(selected)) for t, data in value["history"]]
        self.plot.update()
        self.table.setRowCount(len(rows))
        for index, (name, row) in enumerate(rows.items()):
            cells = (service_label(name, catalog),
                     str(row["interval_calls"]) if row["interval_calls"] is not None else "—",
                     f'{row["mean_ms"]:.3f}' if row["mean_ms"] is not None else "—",
                     f'{row["max_us"]/1000:.3f}' if row["calls"] else "—")
            for col, cell in enumerate(cells):
                self.table.setItem(index, col, QTableWidgetItem(cell))
        self.path.setText(value["log_error"] or value["log_path"] or resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render.path'))


def trace_status_suffix(value, catalog=None):
    trace = value.get('trace', {})
    if trace.get('running'):
        return '；' + ui_enum_text(catalog, '细分采样正在运行并单独记录')
    if trace.get('state') == 'saved':
        return '；' + ui_enum_text(catalog, '细分采样已停止并保存')
    if trace.get('state') in {'failed', 'unconfirmed'}:
        return '；' + ui_enum_text(catalog, '细分采样未完整确认，请查看细分采样页')
    return ''


def status_text(value, catalog=None):
    text = ui_enum_text(catalog, STATE_TEXT[value["state"]])
    if value["automatic"]:
        text += " · " + ui_enum_text(catalog, "由采集排错开启")
    if value["log_error"]:
        text += " · " + ui_enum_text(catalog, value["log_error"])
    return text


class PerformanceCard(QWidget):
    def __init__(self, controller, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.controller = controller
        self.dialog = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.toggle = QCheckBox(resolve_text_catalog(text_catalog).render('ui.settings.init.toggle'))
        self.toggle.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.settings.init.toggle_2')))
        self.toggle.clicked.connect(controller.set_enabled)
        row.addWidget(self.toggle)
        details = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.details'))
        details.clicked.connect(self.show_details)
        row.addWidget(details)
        row.addStretch()
        layout.addLayout(row)
        controller.changed.connect(self.render)
        self.render()

    def render(self):
        value = self.controller.snapshot()
        self.toggle.setChecked(value.get("overlay", False))
        if self.dialog and self.dialog.isVisible():
            self.dialog.render(value)

    def show_details(self):
        if self.dialog is None:
            self.dialog = PerformanceDetails(self.controller, self, text_catalog=getattr(self, 'text_catalog', None))
        self.dialog.render(self.controller.snapshot())
        self.dialog.show()
        self.dialog.raise_()
