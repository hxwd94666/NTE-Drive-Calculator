# 在设置中展示性能开关、服务耗时曲线和排错记录状态。
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QTabWidget, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.settings.performance.trace_view import PerformanceTraceView


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
SERVICE_NAMES.update({prefix + key: owner + label for prefix, owner in
                      (("snapshot.", "采集 · "), ("user.snapshot.", "账号读取 · "))
                      for key, label in _READ_STAGES.items()})


class CostPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(130)
        self.setAccessibleName("服务平均单次耗时曲线，单位毫秒")

    def paintEvent(self, event):
        # 无样本保留间断，既有服务耗时不冒充 FPS 或零耗时。
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.palette().text().color())
        values = [value for _, value in self.points if value is not None]
        if not values:
            painter.drawText(self.rect(), Qt.AlignCenter, "等待新增调用；没有样本不记为零")
            return
        maximum = max(max(values), 0.001)
        painter.drawText(8, 18, f"近 120 次观测 · 平均单次耗时 · 纵轴上限 {maximum:.3f} ms")
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
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("性能详情")
        self.controller = controller
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        overview = QWidget()
        tabs.addTab(overview, '实时概览')
        self.trace_view = PerformanceTraceView(controller)
        tabs.addTab(self.trace_view, '细分采样')
        layout = QVBoxLayout(overview)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        note = QLabel("FPS/帧时间来自游戏提交呈现的间隔；1% Low 为最近 30 秒慢帧平均。"
                      "Calc 耗时当前覆盖原生分发和 HUD，不代表全部组件开销。"
                      "下方曲线是服务平均单次耗时，嵌套服务不能相加。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.service = QComboBox()
        self.service.setAccessibleName("曲线服务")
        self.service.setPlaceholderText("等待可用服务计时")
        layout.addWidget(self.service)
        self.plot = CostPlot()
        layout.addWidget(self.plot)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["服务", "区间调用数", "平均单次 ms", "来源累计最大 ms"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.path = QLabel()
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path)
        row = QHBoxLayout()
        folder = QPushButton("打开性能日志目录")
        folder.clicked.connect(self.open_logs)
        row.addWidget(folder)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.service.currentIndexChanged.connect(lambda: self.render(controller.snapshot()))

    def open_logs(self):
        directory = self.controller.log_dir / "performance"
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.path.setText("尚未生成性能日志；开启采集排错并勾选“同时记录性能”后保存。")

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(820, 570))

    def render(self, value):
        self.trace_view.render(value.get('trace', {}))
        overlay = {"off": "悬浮窗已关闭", "closing": "正在关闭显示", "visible": "悬浮窗已绘制", "waiting": "已请求显示，等待游戏绘制",
                   "unsupported": "当前组件不支持性能悬浮窗，需要配套更新", "rejected": "当前游戏版本不支持性能绘制",
                   "unconfirmed": "悬浮窗状态未确认，等待连接恢复"}.get(value.get("overlay_state"), "等待组件")
        summary = "四项悬浮窗及其日志已关闭。" if not value["enabled"] and value.get("overlay_state") == "off" else overlay + "；" + status_text(value)
        frame_error = value.get("frames", {}).get("frame_error")
        if value["enabled"] and frame_error:
            summary += "；" + frame_error
        self.status.setText(value.get("preference_error") or summary + trace_status_suffix(value))
        metrics = value.get("frames", {})
        def number(key):
            v = metrics.get(key)
            return f"{v / 1000:.2f}" if type(v) is int else "--"
        self.metrics.setText(f"FPS  {number('fps_milli')}    帧时间  {number('frame_us')} ms    "
                             f"1% Low  {number('low_milli')} FPS    Calc 耗时（已覆盖）  {number('cost_us')} ms/帧")
        rows = value["rows"]
        selected = self.service.currentData()
        names = list(rows)
        if names != [self.service.itemData(i) for i in range(self.service.count())]:
            self.service.blockSignals(True)
            self.service.clear()
            for name in names:
                self.service.addItem(SERVICE_NAMES.get(name, name), name)
            self.service.setCurrentIndex(max(0, self.service.findData(selected)))
            self.service.blockSignals(False)
        self.service.setEnabled(bool(names))
        selected = self.service.currentData()
        self.plot.points = [(t, data.get(selected)) for t, data in value["history"]]
        self.plot.update()
        self.table.setRowCount(len(rows))
        for index, (name, row) in enumerate(rows.items()):
            cells = (SERVICE_NAMES.get(name, name),
                     str(row["interval_calls"]) if row["interval_calls"] is not None else "—",
                     f'{row["mean_ms"]:.3f}' if row["mean_ms"] is not None else "—",
                     f'{row["max_us"]/1000:.3f}' if row["calls"] else "—")
            for col, cell in enumerate(cells):
                self.table.setItem(index, col, QTableWidgetItem(cell))
        self.path.setText(value["log_error"] or value["log_path"] or "当前未记录（实时查看不自动落盘）；已有文件可从目录查看。")


def trace_status_suffix(value):
    trace = value.get('trace', {})
    if trace.get('running'):
        return '；细分采样正在运行并单独记录'
    if trace.get('state') == 'saved':
        return '；细分采样已停止并保存'
    if trace.get('state') in {'failed', 'unconfirmed'}:
        return '；细分采样未完整确认，请查看细分采样页'
    return ''


def status_text(value):
    text = STATE_TEXT[value["state"]]
    if value["automatic"]:
        text += " · 由采集排错开启"
    if value["log_error"]:
        text += " · " + value["log_error"]
    return text


class PerformanceCard(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.dialog = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.toggle = QCheckBox("显示游戏内性能")
        self.toggle.setToolTip("显示 FPS、帧时间、1% Low 与 Calc 组件耗时；不启动同步或战报。")
        self.toggle.clicked.connect(controller.set_enabled)
        row.addWidget(self.toggle)
        details = QPushButton("性能详情…")
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
            self.dialog = PerformanceDetails(self.controller, self)
        self.dialog.render(self.controller.snapshot())
        self.dialog.show()
        self.dialog.raise_()
