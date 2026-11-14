# 冲突详情只解释冻结原因并导航修改规则，不提供装备状态裁决或采用入口。
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QScrollArea, QStyle, QVBoxLayout, QWidget

from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.equipment_management.rule_text import _preserve_rule_summary
from src.ui.common.text_catalog import TextActionButton, plain_label


class WarehouseConflictDialog(QDialog):
    edit_requested = Signal(object)

    def __init__(self, catalog, conflicts, parent=None):
        super().__init__(parent)
        self.text_catalog = catalog
        self.text = catalog.render
        self.rule_ids = tuple(dict.fromkeys(rule["rule_id"] for conflict in conflicts for rule in conflict["rules"]))
        self.setWindowTitle(self.text("warehouse.management.conflict.title"))
        self.setWindowModality(Qt.WindowModal)
        self.setStyleSheet(current_style_sheet())
        root = QVBoxLayout(self)
        intro = plain_label(self.text("warehouse.management.conflict.intro", count=len(conflicts)))
        intro.setWordWrap(True)
        root.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        for conflict in conflicts:
            body.addWidget(self._entry(conflict))
        body.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        close = TextActionButton(self.text("warehouse.management.conflict.close"))
        close.clicked.connect(self.reject)
        edit = TextActionButton(self.text("warehouse.management.conflict.edit"))
        edit.clicked.connect(lambda: self.edit_requested.emit(self.rule_ids))
        footer.addWidget(close)
        footer.addWidget(edit)
        root.addLayout(footer)
        fit_dialog_to_available_screen(self, QSize(780, 620))

    def _entry(self, conflict):
        panel = QFrame()
        panel.setObjectName("warehouseConflictEntry")
        panel.setStyleSheet(
            themed_style(
                "QFrame#warehouseConflictEntry{background:#161b22;border:1px solid #30363d;border-radius:8px;}"
            )
        )
        layout = QVBoxLayout(panel)
        header = QHBoxLayout()
        icon = plain_label()
        pixmap = QPixmap(conflict["icon_path"]) if conflict.get("icon_path") else QPixmap()
        if pixmap.isNull():
            pixmap = self.style().standardIcon(QStyle.SP_FileIcon).pixmap(52, 52)
        icon.setPixmap(pixmap.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        header.addWidget(icon)
        title = plain_label(
            self.text(
                "warehouse.management.conflict.item",
                name=conflict.get("name") or conflict["uid"],
                quality=self.text("warehouse.management.conflict.quality_" + conflict.get("quality", "Gold").lower()),
            )
        )
        title.setWordWrap(True)
        header.addWidget(title, 1)
        layout.addLayout(header)
        item = conflict.get("item") or {}
        state = "locked" if item.get("locked") else "discarded" if item.get("discarded") else "normal"
        reason = plain_label(
            self.text("warehouse.management.conflict.reason", state=self.text("warehouse.management.state." + state))
        )
        reason.setWordWrap(True)
        layout.addWidget(reason)
        stats = plain_label(
            self.text(
                "warehouse.management.conflict.stats",
                main=conflict.get("main") or "—",
                subs="、".join(f"{key} {value:g}" for key, value in conflict.get("sub_stats", {}).items()) or "—",
            )
        )
        stats.setWordWrap(True)
        layout.addWidget(stats)
        for rule in conflict["rules"]:
            action = self.text("warehouse.management.rules.action_" + rule["action"])
            label = plain_label(
                self.text(
                    "warehouse.management.conflict.rule",
                    name=rule.get("name") or self.text("warehouse.management.rules.unnamed"),
                    action=action,
                )
            )
            label.setWordWrap(True)
            layout.addWidget(label)
            detail = plain_label(_preserve_rule_summary({**rule.get("conditions", {}), "action": rule["action"]}, text_catalog=self.text_catalog))
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return panel

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self)
