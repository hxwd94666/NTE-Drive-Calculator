# 扫描解析待补录装备的人工修正。
"""Dialog helpers for completing partially parsed scan items."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
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

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.stat_catalog import StatCatalog
from src.integrations.vision.recognition.resources import RecognitionResources
from src.integrations.vision.recognition.model_resources import OcrModelResourceError

def _stat_pool(config_dir: Path, resources=None) -> list[str]:
    catalog = resources.stat_catalog() if resources is not None else StatCatalog.from_config_dir(config_dir)
    pool = set(catalog.gold_base_values.keys())
    pool.update(catalog.tape_stat_values.keys())
    return sorted(stat for stat in pool if stat)


class ManualRecoveryDialog(QDialog):
    def __init__(self, records: list[dict], stat_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("补录未识别词条")
        self.resize(760, 520)
        self.records = deepcopy(records or [])
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
        fit_dialog_to_available_screen(self)

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

    def filled_values(self) -> tuple[tuple[str, float], ...]:
        """只提取展示草稿，不修改解析事实，也不把未完成行伪装成装备。"""
        for _record, _combo, value in self.rows:
            value.interpretText()
        return tuple((combo.currentText(), float(value.value())) for _record, combo, value in self.rows)

    def restore_filled_values(self, values: tuple[tuple[str, float], ...]) -> None:
        if len(values) != len(self.rows):
            return
        for (_record, combo, value), (stat_name, number) in zip(self.rows, values):
            combo.setEditText(stat_name)
            value.setValue(number)

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


class ManualRecoverySession:
    """扫描 owner 的单份未提交草稿；不保存数据库、截图或跨进程缓存。"""

    def __init__(self) -> None:
        self._identity = None
        self._values: tuple[tuple[str, float], ...] = ()
        self._dialog: ManualRecoveryDialog | None = None
        self._revision = 0
        self._closed = False

    @property
    def revision(self) -> int:
        """公开当前撤销代次，供模态返回后的同 owner 交付核对。"""
        return self._revision

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def has_draft(self) -> bool:
        return self._identity is not None and bool(self._values)

    def cancel(self) -> None:
        # 硬停止/账号切换既撤销草稿，也终结模态循环；旧 finally 不得再写回。
        self._revision += 1
        self._identity = None
        self._values = ()
        if self._dialog is not None:
            self._dialog.reject()

    def close(self) -> None:
        self._closed = True
        self.cancel()

    @staticmethod
    def _draft_identity(scope, resources, records, stat_names):
        if scope is None or not isinstance(resources, RecognitionResources):
            return None
        profile = resources.image_profile
        if (profile is None or profile.ocr_resources is None
                or frozenset(entry.shape_id for entry in profile.template_files) != resources.shape_ids):
            return None
        try:
            profile.ocr_resources.require_model_binding()
        except OcrModelResourceError:
            return None
        for record in records:
            digest = record.get("image_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                return None
        # 比较整个不可变资源，不只比路径或语言名：包含静态、字典、模型、形状及布局。
        return scope, resources, deepcopy(records), tuple(stat_names)

    def complete(self, parent, stats: dict, config_dir: Path, *, scope=None,
                 is_current: Callable[[], bool] = lambda: True) -> list[dict] | None:
        records = deepcopy((stats or {}).get("pending_manual_items") or [])
        if self._closed or not is_current():
            self.cancel()
            return None
        if not records:
            self.cancel()
            return []
        if self._dialog is not None:
            return None
        resources = stats.get("recognition_resources")
        stat_names = _stat_pool(Path(config_dir), resources)
        identity = self._draft_identity(scope, resources, records, stat_names)
        if identity is None or identity != self._identity:
            self._identity, self._values = identity, ()
        revision = self._revision
        dialog = ManualRecoveryDialog(records, stat_names, parent)
        dialog.restore_filled_values(self._values)
        self._dialog = dialog
        try:
            result = dialog.exec()
            if revision != self._revision or self._closed or not is_current():
                self.cancel()
                return None
            # 关闭与“跳过”只保留预填值；再次显式接受才返回完整装备给正式提交门禁。
            if identity is not None:
                self._values = dialog.filled_values()
            return dialog.completed_items() if result == QDialog.Accepted else None
        finally:
            self._dialog = None
            dialog.deleteLater()


def complete_pending_manual_items(parent, stats: dict, config_dir: Path, *,
                                  session: ManualRecoverySession | None = None, scope=None,
                                  is_current: Callable[[], bool] = lambda: True) -> list[dict] | None:
    owner = session if session is not None else ManualRecoverySession()
    return owner.complete(parent, stats, config_dir, scope=scope, is_current=is_current)
