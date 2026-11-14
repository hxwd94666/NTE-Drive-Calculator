# 倒带各培养模式共用百分比输入：双击全选数值，完整输入确认后才提交偏好。
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDoubleSpinBox


class RewindPercentInput(QDoubleSpinBox):
    """输入新百分比时覆盖整个旧数值，不将小数点两侧当成独立编辑片段。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 保持父树归属，且不在首个数字时触发写偏好或后缀重排。
        self.setKeyboardTracking(False)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if (watched is self.lineEdit() and event.type() == QEvent.MouseButtonDblClick
                and event.button() == Qt.LeftButton):
            self.selectAll()
            event.accept()
            return True
        return super().eventFilter(watched, event)
