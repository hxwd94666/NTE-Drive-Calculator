# 以明确 ID 编辑本机语言偏好，慢文件操作复用提交通道且不触发自动重启。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.features.settings.language.controller import LanguageActionResult, LanguageController
from src.services.preferences.language_settings_service import LanguageSettingsService
from src.ui.widgets import NoWheelComboBox


class LanguageCard(QGroupBox):
    def __init__(self, service: LanguageSettingsService, parent=None):
        super().__init__(parent)
        self._controller = LanguageController(service, self)
        selection = self._controller.selection
        self._catalog = selection.catalog
        self.setTitle(self._catalog.render("settings.language.title"))
        self._closing = False
        self._controller.started.connect(self._started)
        self._controller.completed.connect(self._completed)
        self._controller.settled.connect(self._settled)
        layout = QVBoxLayout(self)
        self.description = self._label("settings.language.scope")
        layout.addWidget(self.description)
        layout.addWidget(self._label("settings.language.label"))
        self.combo = NoWheelComboBox()
        self.combo.setAccessibleName(self._catalog.render("settings.language.label"))
        for catalog in self._controller.choices():
            self.combo.addItem(catalog.display_name, catalog.locale)
        self.combo.setCurrentIndex(max(0, self.combo.findData(self._controller.preferred_locale())))
        layout.addWidget(self.combo)
        layout.addWidget(self._label("settings.language.restart"))
        row = QHBoxLayout()
        self.save_button = QPushButton(self._catalog.render("settings.language.save"))
        self.restore_button = QPushButton(self._catalog.render("settings.language.restore"))
        self.install_button = QPushButton(self._catalog.render("settings.language.install"))
        self.save_button.setObjectName("btnPrimary")
        self.save_button.clicked.connect(self._save)
        self.restore_button.clicked.connect(self._restore)
        self.install_button.clicked.connect(self._choose_file)
        for button in self._buttons():
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        if selection.warning_code:
            self.status.setText(self._catalog.render("settings.language.fallback", reason=self._reason(selection.warning_code, preference=True)))
        layout.addWidget(self.status)

    def _label(self, key):
        label = QLabel(self._catalog.render(key))
        # 译文永远是纯文本，不能通过 QLabel 自动识别为富文本。
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        return label

    def _reason(self, code, *, preference=False):
        # 展示简短译文，错误码仍留在 Controller 的结构化结果中。
        if preference and code == "invalid_locale":
            code = "invalid_preferences"
        kind = {"invalid_preferences": "preferences",
                "file_operation_failed": "file", "operation_failed": "operation"}.get(code, "patch")
        return self._catalog.render(f"settings.language.error.{kind}")

    def _buttons(self):
        return self.save_button, self.restore_button, self.install_button

    def is_running(self):
        return self._controller.is_running()

    def _started(self):
        self.combo.setEnabled(False)
        for button in self._buttons():
            button.setEnabled(False)
        self.status.setText(self._catalog.render("common.operation.processing"))

    def _completed(self, result: LanguageActionResult, installed):
        if result.error_code:
            self.status.setText(self._catalog.render("common.operation.failed", reason=self._reason(result.error_code)))
            return
        catalog = result.catalog
        if catalog is None:
            self._unexpected_failure("")
            return
        index = self.combo.findData(catalog.locale)
        if index < 0:
            self.combo.addItem(catalog.display_name, catalog.locale)
            index = self.combo.count() - 1
        else:
            self.combo.setItemText(index, catalog.display_name)
        self.combo.setCurrentIndex(index)
        key = "settings.language.installed" if installed else "settings.language.saved"
        self.status.setText(self._catalog.render(key, language=catalog.display_name))

    def _unexpected_failure(self, _error):
        self.status.setText(self._catalog.render("common.operation.failed", reason=self._reason("operation_failed")))

    def _settled(self, _success):
        if self._closing:
            self.close()
            return
        self.combo.setEnabled(True)
        for button in self._buttons():
            button.setEnabled(True)

    def _save(self):
        self._controller.select(self.combo.currentData())

    def _restore(self):
        self._controller.restore_default()

    def _choose_file(self):
        path, _filter = QFileDialog.getOpenFileName(self, self._catalog.render("settings.language.file_title"),
                                                  "", self._catalog.render("settings.language.file_filter"))
        if path:
            self._controller.install(path)

    def closeEvent(self, event):
        self._closing = True
        self._controller.close()
        if self.is_running():
            event.ignore()
            return
        super().closeEvent(event)
