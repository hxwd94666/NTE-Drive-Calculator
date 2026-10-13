# 配装槽位对话框只持有控件与冻结投影，所有读写交给显式任务owner。
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)
from shiboken6 import isValid

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.loadout_management import LoadoutMutation
from src.optimizer.contracts import EQUIP_SET_NAME, ROLE_EQUIPPED_TAPE


def _slot_suit_names(states, slot_id):
    pending = list((states or {}).values()) if isinstance(states, dict) else []
    while pending:
        state = pending.pop(0)
        if not isinstance(state, dict):
            continue
        pending.extend(child for child in (state.get("_role_slot_states") or ()) if isinstance(child, dict))
        if state.get("_loadout_slot_id") == slot_id:
            tape = state.get(ROLE_EQUIPPED_TAPE)
            name = str(tape.get(EQUIP_SET_NAME) or "").strip() if isinstance(tape, dict) else ""
            return (name,) if name else ()
    return ()


class LoadoutSlotDialog(QDialog):
    def __init__(self, window, controller, snapshot, operation, slot_id, role_name):
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._snapshot, self._operation = snapshot, operation
        self._label = role_name or snapshot.slot(slot_id).slot_name
        self._finished = False
        self.setWindowTitle(f"{self._label} · 配装槽位管理")
        self.setFixedWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(QLabel("管理槽位："))
        self.selector = QComboBox(self)
        row.addWidget(self.selector, 1)
        layout.addLayout(row)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(themed_style("QLabel{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px;}"))
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.create_button = QPushButton("新增槽位", self)
        self.rename_button = QPushButton("重命名", self)
        self.archive_button = QPushButton("删除槽位", self)
        self.archive_button.setStyleSheet(themed_style("QPushButton{color:#f85149}"))
        for button in (self.create_button, self.rename_button, self.archive_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.selector.currentIndexChanged.connect(self._update_selected)
        self.create_button.clicked.connect(lambda: self._edit("create"))
        self.rename_button.clicked.connect(lambda: self._edit("rename"))
        self.archive_button.clicked.connect(self._archive)
        controller.retiring.connect(self.reject)
        self.finished.connect(self._finish)
        self._populate(slot_id)
        fit_dialog_to_available_screen(self, QSize(360, self.sizeHint().height()))

    def _finish(self, _result):
        self._finished = True
        self.deleteLater()

    def _alive(self):
        return not self._finished and isValid(self)

    def _slot_label(self, slot):
        return self._label if slot.slot_key == "primary" and slot.slot_name == "主力" else slot.slot_name

    def _populate(self, selected_id):
        self.selector.blockSignals(True)
        self.selector.clear()
        for slot in self._snapshot.slots:
            suffix = "（已锁定）" if slot.locked else ""
            self.selector.addItem(self._slot_label(slot) + suffix, slot.slot_id)
        index = self.selector.findData(selected_id)
        self.selector.setCurrentIndex(max(0, index))
        self.selector.blockSignals(False)
        self._set_busy(False)
        self._update_selected()

    def _selected(self):
        return self._snapshot.slot(self.selector.currentData())

    def _update_selected(self, *_args):
        slot = self._selected()
        if slot is None:
            self.status.setText("没有可管理的配装槽位。")
            return
        status = "锁定" if slot.locked else "变动" if slot.changed else "已配装" if slot.plan_id else "未配装"
        suits = _slot_suit_names(getattr(self._window, "_saved_equipment_states", {}), slot.slot_id)
        self.status.setText(f"状态：{status}\n套装：{' / '.join(suits) if suits else '未装备'}")
        self.archive_button.setEnabled(len(self._snapshot.slots) > 1 and not slot.locked)
        self.archive_button.setToolTip("锁定方案不能删除槽位" if slot.locked else "至少保留一个配装槽位" if len(self._snapshot.slots) <= 1 else "")

    def _set_busy(self, busy):
        for widget in (self.selector, self.create_button, self.rename_button, self.archive_button):
            widget.setEnabled(not busy)

    def _edit(self, action):
        slot = self._selected()
        title = "新增配装槽位" if action == "create" else "重命名配装槽位"
        name, accepted = QInputDialog.getText(self, title, "槽位名称：",
            text=self._slot_label(slot) if action == "rename" and slot else "")
        if accepted and self._alive():
            self._commit(action, title, name=name)

    def _archive(self):
        slot = self._selected()
        if slot is None or len(self._snapshot.slots) <= 1:
            return
        answer = QMessageBox.question(self, "删除配装槽位",
            f"删除 [{self._slot_label(slot)}] 后，该槽位当前方案不再参与展示和装配；历史记录保留。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes and self._alive():
            self._commit("archive", "删除配装槽位")

    def _commit(self, action, title, *, name=""):
        slot = self._selected()
        slot_id = slot.slot_id if slot else None
        self._set_busy(True)
        window, label = self._window, self._label

        def failed(error):
            if self._alive():
                QMessageBox.warning(self, title, error)
                if self._alive():
                    self._reload(slot_id)

        def committed(result):
            window.refresh_saved_equipment_after_mutation(restore_role_name=label)
            if not self._alive():
                return
            # 单独发起只读刷新；失败不会再发写入，已有事务仍是成功。
            target_id = result.slot_id if action != "archive" else next(
                other.slot_id for other in self._snapshot.slots if other.slot_id != slot_id)
            self._reload(target_id)

        def projection_failed(error):
            window.invalidate_saved_equipment_cache()
            if self._alive():
                QMessageBox.warning(self, "配装已保存，页面刷新未完成", "请刷新页面，不要重复提交。\n" + error)
                if self._alive():
                    # 页面确认异常时先恢复读取；绝不以旧提交token再次执行写入。
                    self._reload(slot_id)

        if not self._controller.commit(self._operation,
                LoadoutMutation(action, self._snapshot, slot_id, name), committed, failed, projection_failed):
            if self._alive():
                self._set_busy(False)

    def _reload(self, selected_id):
        self._set_busy(True)

        def read_failed(error):
            if self._alive():
                self.status.setText("读取失败，请关闭后重新打开槽位管理。")
                QMessageBox.warning(self, "读取配装槽位", error)

        self._controller.prepare(lambda snapshot, operation: self._loaded(snapshot, operation, selected_id),
                                 read_failed, slot_id=selected_id)

    def _loaded(self, snapshot, operation, selected_id):
        if self._alive():
            self._snapshot, self._operation = snapshot, operation
            self._populate(selected_id)
