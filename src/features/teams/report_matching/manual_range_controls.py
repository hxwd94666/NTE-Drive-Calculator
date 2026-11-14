# 将战报与精确时段输入排成紧凑自适应表单；只持有控件，不选择战报或发起校验。
from src.ui.common.tooltip_policy import bind_elided_combo_tooltip
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QSizePolicy, QWidget

from src.ui.common.text_catalog import TextActionButton, plain_label


class ManualRangeControls(QWidget):
    height_changed = Signal(int)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.report, self.scope = QComboBox(self), QComboBox(self)
        self.report.addItem(text("teams.report.manual.placeholder"), None)
        self.start, self.end = QDoubleSpinBox(self), QDoubleSpinBox(self)
        self.check = TextActionButton(text("teams.report.manual.check"), self)
        self.labels = {}
        for key, control in (("report", self.report), ("scope", self.scope), ("start", self.start), ("end", self.end)):
            label = plain_label(text("teams.report.manual." + key), self)
            label.setWordWrap(True)
            label.setBuddy(control)
            label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            self.labels[key] = label
            control.setAccessibleName(text("teams.report.manual." + key))
            control.setMinimumWidth(0)
            control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for combo, key in ((self.report, "report"), (self.scope, "scope")):
            combo.setAccessibleName(text("teams.report.manual." + key + "_accessible"))
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            bind_elided_combo_tooltip(combo)
        for control in (self.start, self.end):
            # 协议仍保留微秒精度；只收紧展示宽度，不改变原轴数值或秒到微秒的换算。
            control.setRange(0, 86400)
            control.setDecimals(6)
            control.setSuffix(text("teams.report.manual.seconds"))
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 2)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self._wide = None
        self._reflow(True)

    def _reflow(self, wide):
        if self._wide == wide:
            return
        self._wide = wide
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(6):
            self.grid.setColumnStretch(column, 0)
        if wide:
            positions = (
                ("report", self.report, 0, 0, 1, 3),
                ("scope", self.scope, 0, 4, 5, 1),
                ("start", self.start, 1, 0, 1, 1),
                ("end", self.end, 1, 2, 3, 1),
            )
            self.grid.setColumnStretch(1, 1)
            self.grid.setColumnStretch(3, 1)
            self.grid.addWidget(self.check, 1, 5, alignment=Qt.AlignRight)
        else:
            self.grid.setColumnStretch(1, 1)
            self.grid.setColumnStretch(3, 1)
            self.grid.addWidget(self.check, 1, 3, alignment=Qt.AlignRight)
            positions = (
                ("report", self.report, 0, 0, 1, 3),
                ("scope", self.scope, 1, 0, 1, 2),
                ("start", self.start, 2, 0, 1, 1),
                ("end", self.end, 2, 2, 3, 1),
            )
        for key, control, row, label_column, field_column, span in positions:
            self.grid.addWidget(self.labels[key], row, label_column)
            self.grid.addWidget(control, row, field_column, 1, span)
        self.updateGeometry()
        self.grid.activate()
        self.height_changed.emit(self.minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 700)
