# 展示手动细分性能采样、服务选择与最近窗口的阶段耗时。
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView)
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
    def __init__(self, controller, parent=None, *, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.controller = controller
        layout = QVBoxLayout(self)
        note = plain_label(resolve_text_catalog(text_catalog).render('ui.settings.init.note_2'))
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        self.choices = []
        for i, label in enumerate(LABELS):
            box = QCheckBox(ui_enum_text(self.text_catalog, label))
            box.setToolTip(SERVICES[i])
            box.setChecked(i == 0)
            grid.addWidget(box, i // 4, i % 4)
            self.choices.append(box)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.start = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.start'))
        self.stop = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.stop'))
        self.start.clicked.connect(self.begin)
        self.stop.clicked.connect(controller.stop_trace)
        row.addWidget(self.start)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = plain_label(resolve_text_catalog(text_catalog).render('ui.settings.init.status'))
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([resolve_text_catalog(text_catalog).render('ui.settings.init.table_5'), resolve_text_catalog(text_catalog).render('ui.settings.init.table_6'), 'P95 ms', 'P99 ms', resolve_text_catalog(text_catalog).render('ui.settings.init.table_7')])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

    def begin(self):
        try:
            self.controller.start_trace(sum(1 << i for i, box in enumerate(self.choices) if box.isChecked()))
        except (PermissionError, ValueError) as error:
            self.status.setText(ui_enum_text(self.text_catalog, str(error)))

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
        catalog = getattr(self, 'text_catalog', None)
        text = resolve_text_catalog(catalog).render('frontend.settings.performance.trace_view.render.text', state=f'{ui_enum_text(catalog, state)}', value=f"{value.get('written', 0)}", value_2=f"{value.get('dropped', 0)}")
        if value.get('error'):
            text += '\n' + ui_enum_text(catalog, value['error'])
        if value.get('log_path'):
            text += '\n' + ui_enum_text(catalog, 'Calc 摘要：') + value['log_path']
        if value.get('path'):
            text += '\n' + ui_enum_text(catalog, '原始采样：') + value['path']
        self.status.setText(text)
        rows = []
        for summary in value.get('summaries', []):
            source = summary['source']
            for phase, result in summary['phases_us'].items():
                if result['n']:
                    label = ui_enum_text(getattr(self, 'text_catalog', None), PHASE_LABELS).get(phase, phase)
                    if phase == 'body' and source != 'hud_frame':
                        label = ui_enum_text(catalog, '本次服务总计')
                    rows.append((f'{ui_enum_text(catalog, LABELS[SERVICES.index(source)])} / {label}', result))
        self.table.setRowCount(len(rows))
        for i, (name, result) in enumerate(rows):
            cells = [name, str(result['n']), *(f"{result[k]/1000:.3f}" for k in ('p95', 'p99', 'max'))]
            for j, cell in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(cell))
