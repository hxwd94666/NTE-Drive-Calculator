# 提供识别结果编辑和确认弹窗。
"""MainWindow methods for identification."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import STYLE
from src.models.equipment import Tape
from src.ui.widgets import SearchableComboBox

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_choose_identify_image_options', '_confirm_identify_tape_main_stats']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _choose_identify_image_options(self,path:Path):
    dlg=QDialog(self)
    dlg.setWindowTitle("选择鉴定类型")
    dlg.setMinimumSize(820,680)
    dlg.setStyleSheet(STYLE)
    layout=QVBoxLayout(dlg); layout.setSpacing(10)

    image_label=QLabel()
    image_label.setAlignment(Qt.AlignCenter)
    image_label.setMinimumHeight(430)
    pix=QPixmap(str(path))
    if not pix.isNull():
        image_label.setPixmap(pix.scaled(QSize(780,430),Qt.KeepAspectRatio,Qt.SmoothTransformation))
    layout.addWidget(image_label,1)

    type_row=QHBoxLayout(); type_row.addWidget(QLabel("装备类型"))
    type_group=QButtonGroup(dlg)
    drive_rb=QRadioButton("驱动"); tape_rb=QRadioButton("卡带")
    drive_rb.setChecked(True)
    type_group.addButton(drive_rb,0); type_group.addButton(tape_rb,1)
    type_row.addWidget(drive_rb); type_row.addWidget(tape_rb); type_row.addStretch()
    layout.addLayout(type_row)

    drive_row=QHBoxLayout(); drive_row.addWidget(QLabel("驱动形状"))
    shape_combo=SearchableComboBox()
    for sid in sorted([s for s in self._shape_areas.keys() if s!="TAPE_15"], key=lambda x:(self._shape_areas.get(x,0),x)):
        shape_combo.addItem(f"{sid} ({self._shape_areas.get(sid,0)}格)",sid)
    current_shape=self.ident_shape_combo.currentData() if hasattr(self,"ident_shape_combo") else None
    idx=shape_combo.findData(current_shape)
    if idx>=0: shape_combo.setCurrentIndex(idx)
    self._make_combo_searchable(shape_combo)
    drive_row.addWidget(shape_combo,1); layout.addLayout(drive_row)

    tape_row=QHBoxLayout(); tape_row.addWidget(QLabel("卡带套装"))
    set_combo=SearchableComboBox()
    for set_name in self.all_set_names:
        set_combo.addItem(set_name,set_name)
    current_set=self.ident_set_combo.currentData() if hasattr(self,"ident_set_combo") else None
    idx=set_combo.findData(current_set)
    if idx>=0: set_combo.setCurrentIndex(idx)
    self._make_combo_searchable(set_combo)
    tape_row.addWidget(set_combo,1)
    tape_row.addWidget(QLabel("默认主词条"))
    main_combo=SearchableComboBox()
    main_pool=self._get_tape_main_stats_pool()
    for stat_name in main_pool:
        main_combo.addItem(stat_name,stat_name)
    current_main=self.ident_main_combo.currentData() if hasattr(self,"ident_main_combo") else None
    idx=main_combo.findData(current_main)
    if idx>=0: main_combo.setCurrentIndex(idx)
    self._make_combo_searchable(main_combo)
    tape_row.addWidget(main_combo,1); layout.addLayout(tape_row)

    def sync_rows():
        is_tape=tape_rb.isChecked()
        for i in range(drive_row.count()):
            w=drive_row.itemAt(i).widget()
            if w: w.setVisible(not is_tape)
        for i in range(tape_row.count()):
            w=tape_row.itemAt(i).widget()
            if w: w.setVisible(is_tape)
    drive_rb.toggled.connect(sync_rows); tape_rb.toggled.connect(sync_rows); sync_rows()

    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    if dlg.exec()!=QDialog.Accepted:
        return None
    if tape_rb.isChecked():
        return {
            "type":"tape",
            "set_name":self._combo_data_or_resolved_text(set_combo,self.all_set_names),
            "main_stat":self._combo_data_or_resolved_text(main_combo,main_pool),
        }
    return {"type":"drive","shape_id":self._combo_data_or_resolved_text(shape_combo,self._shape_areas.keys()).split()[0]}

def _confirm_identify_tape_main_stats(self,items):
    tapes=[item for item in items if isinstance(item,Tape)]
    if not tapes:
        return True
    should_confirm=len(tapes)>1 or any((not item.main_stats) or ("未知" in str(item.main_stats)) for item in tapes)
    if not should_confirm:
        return True

    main_pool=self._get_tape_main_stats_pool()
    dlg=QDialog(self)
    dlg.setWindowTitle("确认卡带主词条")
    dlg.setMinimumSize(720,420)
    dlg.setStyleSheet(STYLE)
    layout=QVBoxLayout(dlg); layout.setSpacing(10)
    hint=QLabel("请为每个识别到的卡带分别确认主词条；可直接输入中文或拼音搜索。")
    hint.setStyleSheet("color:#8b949e;border:none")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    content=QWidget(); rows=QVBoxLayout(content); rows.setContentsMargins(0,0,0,0); rows.setSpacing(8)
    combos=[]
    fallback_main=self.ident_main_combo.currentData() or self.ident_main_combo.currentText()
    for idx,item in enumerate(tapes,1):
        frame=QFrame()
        frame.setStyleSheet("QFrame{background:#161b22;border:1px solid #21262d;border-radius:6px}")
        row=QHBoxLayout(frame); row.setContentsMargins(10,8,10,8); row.setSpacing(10)
        summary=", ".join(f"{k}{v:g}" for k,v in list(item.sub_stats.items())[:4])
        label=QLabel(f"卡带 {idx}  {item.set_name or ''}  {summary}")
        label.setWordWrap(True)
        row.addWidget(label,1)
        combo=SearchableComboBox()
        current=item.main_stats if item.main_stats and "未知" not in str(item.main_stats) else fallback_main
        added=set()
        if current:
            combo.addItem(str(current),str(current)); added.add(str(current))
        for stat_name in main_pool:
            if stat_name not in added:
                combo.addItem(stat_name,stat_name)
        combo.refresh_search_items()
        self._set_combo_data(combo,current)
        combo.setMinimumWidth(220)
        row.addWidget(combo)
        combos.append((item,combo))
        rows.addWidget(frame)
    rows.addStretch()
    scroll.setWidget(content)
    layout.addWidget(scroll,1)

    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    if dlg.exec()!=QDialog.Accepted:
        return False
    for item,combo in combos:
        item.main_stats=self._combo_data_or_resolved_text(combo,main_pool)
    return True
