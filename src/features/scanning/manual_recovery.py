# 扫描解析待补录装备的人工修正。
"""Dialog helpers for completing partially parsed scan items."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.stat_catalog import StatCatalog
def _stat_pool(config_dir: Path) -> list[str]:
    catalog = StatCatalog.from_config_dir(config_dir)
    pool = set(catalog.gold_base_values.keys())
    pool.update(catalog.tape_stat_values.keys())
    return sorted(stat for stat in pool if stat)


class ManualRecoveryDialog(QDialog):
    def __init__(self, records: list[dict], stat_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("补录未识别词条")
        self.resize(760, 520)
        self.records = list(records or [])
        self.stat_names = list(stat_names or [])
        self.rows = []

        root = QVBoxLayout(self)
        root.addWidget(QLabel("以下装备已识别到 3 条有效副词条，请补录缺失的 1 条后入库。"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)

        for index, record in enumerate(self.records, 1):
            content_layout.addWidget(self._build_record_group(index, record))
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setText("补录入库")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button:
            cancel_button.setText("跳过")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_record_group(self, index: int, record: dict) -> QGroupBox:
        item = dict(record.get("item") or {})
        item_type = "驱动" if item.get("item_type") == "drive" else "卡带"
        title = f"{index}. {record.get('filename', '')} · {item_type}"
        group = QGroupBox(title)
        layout = QVBoxLayout(group)

        meta = []
        if item.get("item_type") == "drive":
            meta.append(f"形状：{item.get('shape_id', '未知')}")
        else:
            meta.append(f"套装：{item.get('set_name', '未知')}")
            meta.append(f"主词条：{item.get('main_stats', '未知')}")
        meta.append(f"品质：{item.get('quality', '未知')}")
        layout.addWidget(QLabel("；".join(meta)))

        stats = item.get("sub_stats") or {}
        stat_text = "；".join(f"{name} {value:g}" for name, value in stats.items()) or "无"
        label = QLabel(f"已识别：{stat_text}")
        label.setWordWrap(True)
        layout.addWidget(label)

        form = QFormLayout()
        combo = QComboBox()
        combo.setEditable(True)
        existing = set(stats.keys())
        for stat_name in self.stat_names:
            if stat_name not in existing:
                combo.addItem(stat_name)
        value = QDoubleSpinBox()
        value.setRange(-99999.0, 99999.0)
        value.setDecimals(3)
        value.setSingleStep(0.1)
        value.setValue(0.0)
        form.addRow("缺失词条", combo)
        form.addRow("数值", value)
        layout.addLayout(form)

        preview_row = QHBoxLayout()
        preview_row.addStretch()
        path = str(record.get("image_path") or "")
        open_btn = QPushButton("打开截图")
        open_btn.setEnabled(bool(path))
        open_btn.clicked.connect(lambda _checked=False, p=path: self._open_path(p))
        preview_row.addWidget(open_btn)
        layout.addLayout(preview_row)

        self.rows.append((record, combo, value))
        return group

    def _open_path(self, path: str) -> None:
        if not path:
            return
        try:
            import os

            os.startfile(path)
        except Exception as exc:
            QMessageBox.warning(self, "打开截图失败", str(exc))

    def completed_items(self) -> list[dict] | None:
        items = []
        for record, combo, value in self.rows:
            item = dict(record.get("item") or {})
            stats = dict(item.get("sub_stats") or {})
            stat_name = combo.currentText().strip()
            if not stat_name:
                QMessageBox.warning(self, "补录未完成", "请选择缺失词条。")
                return None
            if stat_name in stats:
                QMessageBox.warning(self, "补录重复", f"{stat_name} 已存在，请选择真正缺失的词条。")
                return None
            stats[stat_name] = float(value.value())
            item["sub_stats"] = stats
            items.append(item)
        return items

    def accept(self) -> None:
        if self.completed_items() is None:
            return
        super().accept()


def complete_pending_manual_items(parent, stats: dict, config_dir: Path) -> list[dict] | None:
    records = list((stats or {}).get("pending_manual_items") or [])
    if not records:
        return []
    dialog = ManualRecoveryDialog(records, _stat_pool(Path(config_dir)), parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    items = dialog.completed_items() or []
    return items
