# 队伍页的响应式分组与可折叠设置，只调整展示，不发起业务操作。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from src.app.theme import themed_style


class ResponsiveGrid(QWidget):
    """按可用宽度换行；重排保留控件及其信号，不重建任务或选择状态。"""

    def __init__(self, widgets, *, columns=3, minimum=220, parent=None):
        super().__init__(parent)
        self._widgets = tuple(widgets)
        self._maximum_columns, self._minimum = columns, minimum
        self._columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._reflow(1)

    def _reflow(self, columns=None):
        columns = columns or max(1, min(self._maximum_columns, (self.width() + 8) // (self._minimum + 8)))
        if self._columns == columns:
            return
        for index in range(self._maximum_columns):
            self.grid.setColumnStretch(index, 0)
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for index, widget in enumerate(self._widgets):
            self.grid.addWidget(widget, index // columns, index % columns)
        for index in range(columns):
            self.grid.setColumnStretch(index, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()


class Disclosure(QWidget):
    """默认收起次要设置；键盘可展开，展开本身不读取、不保存也不计算。"""

    def __init__(self, title, content, *, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QToolButton(self)
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setFocusPolicy(Qt.StrongFocus)
        self.toggle.setStyleSheet(themed_style(
            "QToolButton{color:#8b949e;background:transparent;border:1px solid transparent;"
            "border-radius:5px;padding:6px;text-align:left;}"
            "QToolButton:hover{color:#58a6ff;}QToolButton:focus{border-color:#58a6ff;}"
            "QToolButton:checked{color:#58a6ff;}"))
        layout.addWidget(self.toggle, 0, Qt.AlignLeft)
        layout.addWidget(content)
        self.content = content
        self.toggle.toggled.connect(self._expanded)
        self._expanded(False)

    def _expanded(self, expanded):
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)
        self.toggle.setAccessibleDescription("已展开" if expanded else "已收起")
        self.updateGeometry()
