# 验证鉴定预览在所属屏幕的有限工作区内展示并保留关闭入口。
from pathlib import Path
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QWidget
from src.features.identification import page


def test_preview_uses_owner_available_screen_and_keeps_close_reachable(monkeypatch, tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    available = QRect(900, 80, 640, 480)

    class Owner(QWidget):
        def screen(self):
            class Screen:
                def availableGeometry(self):
                    return available
            return Screen()

    owner = Owner()
    source = tmp_path / "synthetic.png"
    image = QImage(1600, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("gray"))
    assert image.save(str(source))
    observed = []

    def execute(dialog):
        dialog.layout().activate()
        buttons = dialog.findChild(QDialogButtonBox)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        observed.append((dialog.geometry(), dialog.rect().contains(close.mapTo(dialog, close.rect().center()))))
        close.click()
        assert dialog.result() == QDialog.DialogCode.Rejected
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", execute)
    try:
        page.show_identify_preview_image(owner, source, "")
        assert len(observed) == 1
        assert available.contains(observed[0][0])
        assert observed[0][1]
    finally:
        for dialog in owner.findChildren(QDialog):
            dialog.close()
        owner.close()
        owner.deleteLater()
        app.processEvents()
