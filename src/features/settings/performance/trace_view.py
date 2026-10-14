# 展示手动细分性能采样、服务选择与最近窗口的阶段耗时。
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt

from src.integrations.performance.journals import SERVICES

LABELS = ('HUD 每帧', 'HUD 事件', 'HUD 数值事件', '同步轮询', '快照读取', '战斗时钟', '战斗观察')
PHASE_LABELS = {'original': '游戏原始回调（不属 Calc）', 'gate': '入口检查', 'inbox': '事件合并',
                'discovery': '对象发现', 'setup': '绘制准备', 'boss': '敌人状态', 'team': '队伍 HUD',
                'nameplate': '名称标签', 'infoLog': '诊断日志', 'draw': '本方绘制总计',
                'body': '整个回调（含原始回调）', 'outside': '回调外间隔', 'gap': '相邻回调间隔',
                'teamCooldowns': '冷却查询', 'teamTextures': '图标资源', 'teamMirror': '原生控件更新',
                'teamCanvas': '队伍 Canvas 绘制'}


class PerformanceTraceView(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        note = QLabel('细分采样 · 记录已运行功能的分阶段耗时；最多 120 秒 / 64 MiB。'
                      '结果是各服务最近一组最多 600 次回调，嵌套阶段不能相加，也不是游戏帧率。')
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        self.choices = []
        for i, label in enumerate(LABELS):
            box = QCheckBox(label)
            box.setToolTip(SERVICES[i])
            box.setChecked(i == 0)
            grid.addWidget(box, i // 4, i % 4)
            self.choices.append(box)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.start = QPushButton('开始细分采样')
        self.stop = QPushButton('停止并保存')
        self.start.clicked.connect(self.begin)
        self.stop.clicked.connect(controller.stop_trace)
        row.addWidget(self.start)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = QLabel('尚未采样')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['服务 / 阶段', '样本数', 'P95 ms', 'P99 ms', '最大 ms'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

    def begin(self):
        try:
            self.controller.start_trace(sum(1 << i for i, box in enumerate(self.choices) if box.isChecked()))
        except (PermissionError, ValueError) as error:
            self.status.setText(str(error))

    def render(self, value):
        # 保存、失败和停止未确认分别展示；嵌套阶段不在界面相加。
        running = value.get('running', False)
        self.start.setEnabled(value.get('allowed', False) and not running)
        self.stop.setEnabled(running)
        for box in self.choices:
            box.setEnabled(not running)
        state = {'off': '尚未采样', 'starting': '正在开启', 'collecting': '正在采样',
                 'saved': '已停止并保存', 'failed': '采样未完整完成', 'unconfirmed': '停止未确认'}.get(value.get('state'), '等待组件')
        if running and value.get('stopping'):
            state = '正在停止并落盘'
        text = f"{state} · 已写入 {value.get('written', 0)} 条 · 丢失 {value.get('dropped', 0)} 条"
        if value.get('error'):
            text += '\n' + value['error']
        if value.get('log_path'):
            text += '\nCalc 摘要：' + value['log_path']
        if value.get('path'):
            text += '\n原始采样：' + value['path']
        self.status.setText(text)
        rows = []
        for summary in value.get('summaries', []):
            source = summary['source']
            for phase, result in summary['phases_us'].items():
                if result['n']:
                    label = PHASE_LABELS.get(phase, phase)
                    if phase == 'body' and source != 'hud_frame':
                        label = '本次服务总计'
                    rows.append((f'{LABELS[SERVICES.index(source)]} / {label}', result))
        self.table.setRowCount(len(rows))
        for i, (name, result) in enumerate(rows):
            cells = [name, str(result['n']), *(f"{result[k]/1000:.3f}" for k in ('p95', 'p99', 'max'))]
            for j, cell in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(cell))
