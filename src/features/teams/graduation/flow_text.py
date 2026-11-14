# 可选择的只读纯文本随内容自然增高，只由所属结果窗口滚动，不建立内层文本视口。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


class FlowReadOnlyText(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setText(text)
