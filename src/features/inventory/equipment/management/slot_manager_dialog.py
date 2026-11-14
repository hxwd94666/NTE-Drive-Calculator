# 配装槽位对话框只持有控件与冻结投影，所有读写交给显式任务owner。
from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QInputDialog, QMessageBox, QVBoxLayout,
)
from shiboken6 import isValid

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.loadouts.loadout_management import LoadoutMutation
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
    def __init__(self, window, controller, snapshot, operation, slot_id, role_name, *, text_catalog=None):
        super().__init__(window)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._window = window
        self._controller = controller
        self._snapshot, self._operation = snapshot, operation
        self._label = role_name or snapshot.slot(slot_id).slot_name
        self._finished = False
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.inventory.init.self', label=f'{self._label}'))
        self.setFixedWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.inventory.init.caption')))
        self.selector = QComboBox(self)
        row.addWidget(self.selector, 1)
        layout.addLayout(row)
        self.status = plain_label(self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(themed_style("QLabel{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px;}"))
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.create_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.create_button'), self)
        self.rename_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.rename_button'), self)
        self.archive_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.inventory.init.archive_button'), self)
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
            self.status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.update_selected.status_2'))
            return
        status = "锁定" if slot.locked else "变动" if slot.changed else "已配装" if slot.plan_id else "未配装"
        suits = _slot_suit_names(getattr(self._window, "_saved_equipment_states", {}), slot.slot_id)
        self.status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.update_selected.status', status=ui_enum_text(getattr(self, 'text_catalog', None), f'{status}'), value=f"{(' / '.join(suits) if suits else '未装备')}"))
        self.archive_button.setEnabled(len(self._snapshot.slots) > 1 and not slot.locked)
        self.archive_button.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.update_selected.archive_button') if slot.locked else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.update_selected.archive_button_2') if len(self._snapshot.slots) <= 1 else ""))

    def _set_busy(self, busy):
        for widget in (self.selector, self.create_button, self.rename_button, self.archive_button):
            widget.setEnabled(not busy)

    def _edit(self, action):
        slot = self._selected()
        title = resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.management.slot_manager_dialog.edit.text') if action == "create" else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('frontend.inventory.equipment.management.slot_manager_dialog.edit.text_2')
        name, accepted = QInputDialog.getText(self, title, plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.edit.name_accepted')),
            text=self._slot_label(slot) if action == "rename" and slot else "")
        if accepted and self._alive():
            self._commit(action, title, name=name)

    def _archive(self):
        slot = self._selected()
        if slot is None or len(self._snapshot.slots) <= 1:
            return
        answer = QMessageBox.question(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.archive.answer'),
            plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.archive.answer_2', value=f'{self._slot_label(slot)}')),
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
                QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('loadouts.actions.persistence_projection_failure.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.persistence_projection_failure.qmessagebox') + error))
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
                self.status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.read_failed.status'))
                QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.read_failed.qmessagebox'), error)

        self._controller.prepare(lambda snapshot, operation: self._loaded(snapshot, operation, selected_id),
                                 read_failed, slot_id=selected_id)

    def _loaded(self, snapshot, operation, selected_id):
        if self._alive():
            self._snapshot, self._operation = snapshot, operation
            self._populate(selected_id)
