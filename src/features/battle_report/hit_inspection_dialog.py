# 统一公式与 Buff 详情窗口的紧凑布局、滚动区域和原始字段弹窗。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLayout, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen
from .hit_inspection_widget import HitInspectionWidget


class HitInspectionDialog(QDialog):
    def __init__(self, parent=None, *, game_ui_asset_root=None):
        super().__init__(parent)
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(0, 0, 0, 0)
        self.inspection = HitInspectionWidget(self, game_ui_asset_root=game_ui_asset_root)
        layout.addWidget(self.inspection)
        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        self._technical_dialog = QDialog(self)
        self._technical_dialog.setWindowTitle("完整公式与原始字段")
        technical_layout = QVBoxLayout(self._technical_dialog)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        technical_layout.addWidget(self.detail)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self._technical_dialog.hide)
        technical_layout.addWidget(close)
        self.inspection.technical_requested.connect(self.show_technical)

    def show_technical(self):
        fit_dialog_to_available_screen(self._technical_dialog, QSize(900, 580))
        self._technical_dialog.show()
        self._technical_dialog.raise_()

    def fit_overview(self):
        fit_dialog_to_available_screen(self, QSize(940, 660))

    def hideEvent(self, event):
        self._technical_dialog.hide()
        if self.inspection._popup is not None:
            self.inspection._popup.hide()
        super().hideEvent(event)
