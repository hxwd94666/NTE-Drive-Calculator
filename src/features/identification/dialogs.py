# 提供识别结果编辑和确认弹窗。
"""MainWindow methods for identification."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import current_style_sheet, themed_style
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.models.equipment import Tape
from src.ui.widgets import SearchableComboBox

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_choose_identify_image_options', '_confirm_identify_tape_main_stats']

AUTO_CHOICE = "__auto__"


def group_shape_ids_by_area(shape_areas: dict) -> dict[int, list[str]]:
    grouped = {2: [], 3: [], 4: []}
    for shape_id, area in shape_areas.items():
        if shape_id == "TAPE_15":
            continue
        if area in grouped:
            grouped[area].append(shape_id)
    for shape_ids in grouped.values():
        shape_ids.sort()
    return grouped


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _choose_identify_image_options(self,path:Path):
    dlg=QDialog(self)
    dlg.setWindowTitle("选择鉴定类型")
    dlg.setMinimumSize(900,720)
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setSpacing(10)

    image_label=QLabel()
    image_label.setAlignment(Qt.AlignCenter)
    image_label.setMinimumHeight(320)
    pix=QPixmap(str(path))
    if not pix.isNull():
        image_label.setPixmap(pix.scaled(QSize(840,320),Qt.KeepAspectRatio,Qt.SmoothTransformation))
    layout.addWidget(image_label,1)

    type_row=QHBoxLayout(); type_row.addWidget(QLabel("装备类型"))
    type_group=QButtonGroup(dlg)
    drive_rb=QRadioButton("驱动"); tape_rb=QRadioButton("卡带")
    drive_rb.setChecked(True)
    type_group.addButton(drive_rb,0); type_group.addButton(tape_rb,1)
    type_row.addWidget(drive_rb); type_row.addWidget(tape_rb); type_row.addStretch()
    layout.addLayout(type_row)

    drive_widget=QWidget()
    drive_layout=QVBoxLayout(drive_widget)
    drive_layout.setContentsMargins(0,0,0,0)
    drive_layout.setSpacing(6)
    drive_layout.addWidget(QLabel("驱动形状"))
    selected_shape={"value":None}
    shape_buttons=[]

    def set_selected_shape(shape_id):
        selected_shape["value"]=shape_id
        for btn,sid in shape_buttons:
            if sid==shape_id:
                btn.setStyleSheet(themed_style("QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}"))
            else:
                btn.setStyleSheet(themed_style("QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}"))

    grouped_shapes=group_shape_ids_by_area(self._shape_areas)
    for area in (2,3,4):
        shape_row=QHBoxLayout()
        shape_row.setSpacing(8)
        title=QLabel(f"{area}型")
        title.setFixedWidth(36)
        shape_row.addWidget(title)
        for sid in grouped_shapes.get(area,[]):
            btn=QPushButton(sid)
            btn.setToolTip(sid)
            btn.setMinimumSize(84,54)
            pixmap = warehouse_shape_pixmap(sid, "Gold")
            if not pixmap.isNull():
                btn.setIcon(QIcon(pixmap))
                btn.setIconSize(QSize(32,32))
            btn.clicked.connect(lambda _checked=False, shape_id=sid: set_selected_shape(shape_id))
            shape_buttons.append((btn,sid))
            shape_row.addWidget(btn)
        shape_row.addStretch()
        drive_layout.addLayout(shape_row)
    current_shape=self.ident_shape_combo.currentData() if hasattr(self,"ident_shape_combo") else None
    initial_shape=current_shape if current_shape in self._shape_areas and current_shape!="TAPE_15" else None
    if initial_shape is None:
        all_shapes=[sid for area in (2,3,4) for sid in grouped_shapes.get(area,[])]
        initial_shape=all_shapes[0] if all_shapes else None
    if initial_shape:
        set_selected_shape(initial_shape)
    layout.addWidget(drive_widget)

    tape_row=QHBoxLayout(); tape_row.addWidget(QLabel("卡带套装"))
    set_combo=SearchableComboBox()
    set_combo.addItem("自动识别",AUTO_CHOICE)
    for set_name in self.all_set_names:
        set_combo.addItem(set_name,set_name)
    self._make_combo_searchable(set_combo)
    tape_row.addWidget(set_combo,1)
    tape_row.addWidget(QLabel("主词条"))
    main_combo=SearchableComboBox()
    main_pool=self._get_tape_main_stats_pool()
    main_combo.addItem("自动识别",AUTO_CHOICE)
    for stat_name in main_pool:
        main_combo.addItem(stat_name,stat_name)
    self._make_combo_searchable(main_combo)
    tape_row.addWidget(main_combo,1); layout.addLayout(tape_row)

    def sync_rows():
        is_tape=tape_rb.isChecked()
        drive_widget.setVisible(not is_tape)
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
        set_name=None if set_combo.currentData()==AUTO_CHOICE else self._combo_data_or_resolved_text(set_combo,self.all_set_names)
        main_stat=None if main_combo.currentData()==AUTO_CHOICE else self._combo_data_or_resolved_text(main_combo,main_pool)
        return {
            "type":"tape",
            "set_name":set_name,
            "main_stat":main_stat,
        }
    return {"type":"drive","shape_id":selected_shape["value"]}

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
    dlg.setStyleSheet(current_style_sheet())
    layout=QVBoxLayout(dlg); layout.setSpacing(10)
    hint=QLabel("请为每个识别到的卡带分别确认主词条；可直接输入中文或拼音搜索。")
    hint.setStyleSheet(themed_style("color:#8b949e;border:none"))
    hint.setWordWrap(True)
    layout.addWidget(hint)

    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    content=QWidget(); rows=QVBoxLayout(content); rows.setContentsMargins(0,0,0,0); rows.setSpacing(8)
    combos=[]
    fallback_main=self.ident_main_combo.currentData() or self.ident_main_combo.currentText()
    for idx,item in enumerate(tapes,1):
        frame=QFrame()
        frame.setStyleSheet(themed_style("QFrame{background:#161b22;border:1px solid #21262d;border-radius:6px}"))
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
