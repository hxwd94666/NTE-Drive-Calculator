# 绘制驱动盘拼图棋盘和形状图标。
"""Puzzle board and shape image widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from src.app import runtime
from src.app.theme import current_theme_name


class PuzzleBoardWidget(QWidget):
    SHAPE_HUE = {
        "H_2": 0, "V_2": 30, "H_3": 60, "V_3": 90,
        "L_3_TL": 120, "L_3_TR": 150, "L_3_BL": 180, "L_3_BR": 210,
        "H_4": 240, "V_4": 270, "Trap_4_H": 300, "Trap_4_V": 330,
        "TAPE_15": 50,
    }

    def __init__(self, matrix=None, cell_size=40, parent=None):
        super().__init__(parent)
        self.matrix = matrix or []
        self.cell_size = cell_size
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._recalc()

    def _recalc(self):
        if not self.matrix:
            self.setFixedSize(100, 100)
            return
        rows = len(self.matrix)
        cols = len(self.matrix[0]) if self.matrix else 0
        self.setFixedSize(cols * self.cell_size + 8, rows * self.cell_size + 8)

    def set_matrix(self, matrix):
        self.matrix = matrix
        self._recalc()
        self.update()

    def paintEvent(self, event):
        if not self.matrix:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        is_light = current_theme_name() == "light"
        rows, cols = len(self.matrix), len(self.matrix[0])
        for row in range(rows):
            for col in range(cols):
                x, y = col * self.cell_size + 4, row * self.cell_size + 4
                rect = QRect(x + 1, y + 1, self.cell_size - 2, self.cell_size - 2)
                cell = str(self.matrix[row][col]) if row < rows and col < len(self.matrix[row]) else "0"
                if cell in ("XX", "-1"):
                    border = QColor("#cf222e" if is_light else "#da3633")
                    fill = QColor(255, 235, 233, 210) if is_light else QColor(218, 54, 51, 40)
                    painter.setPen(QPen(border, 1.4 if is_light else 1))
                    painter.setBrush(fill)
                    painter.drawRoundedRect(rect, 4, 4)
                    painter.setPen(border)
                    painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
                    painter.drawText(rect, Qt.AlignCenter, "✕")
                elif cell in ("0", "0.0"):
                    painter.setPen(QPen(QColor("#d0d7de" if is_light else "#21262d"), 1.2 if is_light else 1))
                    painter.setBrush(QColor(246, 248, 250, 180) if is_light else QColor(13, 17, 23, 120))
                    painter.drawRoundedRect(rect, 4, 4)
                else:
                    hue = self.SHAPE_HUE.get(cell, abs(hash(cell)) % 360)
                    if is_light:
                        color = QColor.fromHsl(hue, 185, 220)
                        border = QColor.fromHsl(hue, 210, 135)
                        text_color = QColor.fromHsl(hue, 220, 70)
                        painter.setPen(QPen(border, 1.4))
                        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 215))
                    else:
                        color = QColor.fromHsl(hue, 180, 128)
                        border = QColor.fromHsl(hue, 220, 160)
                        text_color = border
                        painter.setPen(QPen(border, 1.5))
                        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 100))
                    painter.drawRoundedRect(rect, 4, 4)
                    painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Bold))
                    label = cell.replace("L_3_", "").replace("Trap_4_", "").replace("TAPE_", "T")
                    if is_light:
                        painter.setPen(QColor(255, 255, 255, 180))
                        painter.drawText(rect.adjusted(1, 1, 1, 1), Qt.AlignCenter, label)
                    painter.setPen(text_color)
                    painter.drawText(rect, Qt.AlignCenter, label)


_shape_pixmaps: dict[tuple[str, str], QPixmap] = {}


def get_shape_pixmap(shape_id: str, size=60, quality: str | None = None) -> QPixmap:
    key = (shape_id, quality or "")
    if key in _shape_pixmaps:
        return _shape_pixmaps[key].scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    path = runtime.TEMPLATE_DIR / f"{shape_id}_{quality}.png" if quality else runtime.TEMPLATE_DIR / f"{shape_id}.png"
    if quality and not path.exists():
        path = runtime.TEMPLATE_DIR / f"{shape_id}.png"
    if path.exists():
        pixmap = QPixmap(str(path))
        _shape_pixmaps[key] = pixmap
        return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return QPixmap()
