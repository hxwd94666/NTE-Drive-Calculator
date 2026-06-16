# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.theme import GRADE_BGS, GRADE_COLORS
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_equipment_compare_signature', '_same_equipment_by_ocr', '_page_equipment', '_refresh_equip',
           '_clear_all_equipment', '_delete_role_equipment', '_import_to_my_role', '_save_eq']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _equipment_compare_signature(self, item):
    return equipment_compare_signature(item)


def _same_equipment_by_ocr(self, left: Path, right: Path):
    return self._scan_lifecycle().same_equipment_by_ocr(left, right)


def _page_equipment(self):
    page = QWidget();
    l = QVBoxLayout(page);
    l.setContentsMargins(20, 16, 20, 16);
    l.setSpacing(8)
    sh = QHBoxLayout();
    sh.addWidget(QLabel("搜索"))
    self.equip_search = QLineEdit();
    self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）...");
    self.equip_search.setClearButtonEnabled(True)
    self.equip_search.textChanged.connect(self._refresh_equip);
    sh.addWidget(self.equip_search)
    clear_btn = QPushButton("清空配装");
    clear_btn.setObjectName("btnDanger");
    clear_btn.clicked.connect(self._clear_all_equipment)
    sh.addWidget(clear_btn);
    l.addLayout(sh)
    scroll = QScrollArea();
    scroll.setWidgetResizable(True)
    self.equip_content = QWidget();
    self.equip_content_layout = QVBoxLayout(self.equip_content);
    scroll.setWidget(self.equip_content)
    l.addWidget(scroll, 1);
    return page


def _refresh_equip(self):
    while self.equip_content_layout.count():
        it = self.equip_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()
    eq = self.equipped_state;
    all_roles = sorted(eq.keys())
    filt = self.equip_search.text().strip() if hasattr(self, 'equip_search') else ""
    shown = 0
    for role_name in all_roles:
        if filt and not _match_pinyin(role_name, filt): continue
        rd = eq.get(role_name, {})
        if not isinstance(rd, dict): continue
        shown += 1;
        wts = self.roles_db.get(role_name, {}).get("weights", {})

        total_score = 0.0;
        total_area = 0
        tape_data = rd.get("equipped_tape")
        if tape_data:
            t_q = tape_data.get("quality", "Gold")
            t_s = self._score_tape_dict(tape_data.get("main_stats", ""), tape_data.get("sub_stats", {}), wts, t_q)
            total_score += t_s;
            total_area += 15
        for d in rd.get("equipped_drives", []):
            d_area = self._shape_areas.get(d.get("shape_id", ""), 3)
            d_q = d.get("quality", "Gold")
            d_s = self._score_drive_dict(d.get("sub_stats", {}), d.get("shape_id", ""), wts, d_q)
            total_score += d_s;
            total_area += d_area
        total_grade = self._calc_grade(total_score, total_area)
        gc = GRADE_COLORS.get(total_grade, "#58a6ff");
        gbg = GRADE_BGS.get(total_grade, f"{gc}15")

        grp = QGroupBox("");
        grp.setStyleSheet(
            "QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}")
        gl = QVBoxLayout(grp);
        gl.setSpacing(10)
        role_hdr = QHBoxLayout();
        role_hdr.setSpacing(8)
        rnl = QLabel(role_name)
        rnl.setStyleSheet(
            f"font-size:15px;font-weight:800;color:#4dd0e1;border:1px solid #4dd0e1;border-radius:7px;padding:4px 14px;background:#4dd0e122")
        role_hdr.addWidget(rnl)
        _sm = rd.get("strategy_mode", "")
        if _sm:
            _ml = {"role_priority": "角色优先", "drive_priority": "驱动优先", "global_optimal": "全局最优",
                   "update_mode": "增量更新"}.get(_sm, _sm)
            sml = QLabel(_ml);
            sml.setStyleSheet("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
            role_hdr.addWidget(sml)
        role_hdr.addStretch()
        # Score
        sf = QFrame()
        sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        slb = QHBoxLayout(sf);
        slb.setSpacing(6);
        slb.setContentsMargins(4, 0, 4, 0)
        sv = QLabel(f"{total_score:.1f}");
        sv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
        slb.addWidget(QLabel("评分"));
        slb.addWidget(sv);
        role_hdr.addWidget(sf)
        # Grade
        gf = QFrame()
        gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        glb = QHBoxLayout(gf);
        glb.setSpacing(6);
        glb.setContentsMargins(4, 0, 4, 0)
        gv = QLabel(total_grade);
        gv.setStyleSheet(f"font-size:15px;font-weight:800;color:{gc};border:none")
        glb.addWidget(QLabel("评级"));
        glb.addWidget(gv);
        role_hdr.addWidget(gf)
        del_btn = QPushButton("删除");
        del_btn.setObjectName("btnDanger")
        del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
        role_hdr.addWidget(del_btn)
        import_btn = QPushButton("导入")
        import_btn.setObjectName("btnPrimary")  # 蓝色主按钮样式
        import_btn.clicked.connect(lambda _, rn=role_name: self._import_to_my_role(rn))
        role_hdr.addWidget(import_btn)
        gl.addLayout(role_hdr);
        gl.addSpacing(6)

        bp = rd.get("blueprint_layout", [])
        drives = rd.get("equipped_drives", [])
        if bp:
            gl.addWidget(self._section_label("拼图图纸:"))
            bp_row = QHBoxLayout();
            bp_row.setSpacing(44)
            bp_row.addWidget(PuzzleBoardWidget(bp), 0, Qt.AlignTop)
            bp_row.addWidget(self._bonus_summary_widget(role_name, tape_data, drives), 0, Qt.AlignTop)
            bp_row.addStretch(1)
            gl.addLayout(bp_row)
        if tape_data:
            t_q = tape_data.get("quality", "Gold")
            t_s = self._score_tape_dict(tape_data.get("main_stats", ""), tape_data.get("sub_stats", {}), wts, t_q)
            t_g = self._calc_grade(t_s, 15)
            gl.addWidget(self._section_label("卡带:"))
            gl.addWidget(self._equip_card(tape_data.get("set_name", ""), tape_data.get("main_stats", ""),
                                          tape_data.get("sub_stats", {}), None, tape_data.get("uid", ""), wts,
                                          (t_s, t_g), t_q))
        if drives:
            gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
            for d in drives:
                d_q = d.get("quality", "Gold")
                d_s = self._score_drive_dict(d.get("sub_stats", {}), d.get("shape_id", ""), wts, d_q)
                d_g = self._calc_grade(d_s, self._shape_areas.get(d.get("shape_id", ""), 3))
                gl.addWidget(self._equip_card(d.get("shape_id", ""), "", d.get("sub_stats", {}), d.get("shape_id", ""),
                                              d.get("uid", ""), wts, (d_s, d_g), d_q))
        self.equip_content_layout.addWidget(grp)
    if shown == 0:
        ph = QLabel("暂无已保存的配装。请先执行分配并保存。");
        ph.setStyleSheet("color:#6e7681;padding:24px");
        ph.setAlignment(Qt.AlignCenter);
        self.equip_content_layout.addWidget(ph)
    self.equip_content_layout.addStretch()


def _clear_all_equipment(self):
    if not self.equipped_state:
        QMessageBox.information(self, "清空配装", "当前没有已保存的配装。")
        return
    ret = QMessageBox.question(
        self,
        "清空配装",
        "确定要清空所有角色的已保存配装吗？\n这会解除增量扫描中的装备锁定。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    self.equipped_state = {}
    self._save_eq()
    self._refresh_equip()
    logger.success("已清空所有角色配装")


def _delete_role_equipment(self, role_name: str):
    if role_name not in self.equipped_state:
        self._refresh_equip()
        return
    ret = QMessageBox.question(
        self,
        "删除角色配装",
        f"确定要删除 [{role_name}] 的已保存配装吗？\n该角色占用的驱动/卡带将不再被锁定。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    self.equipped_state.pop(role_name, None)
    self._save_eq()
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


def _import_to_my_role(self, role_name: str):
    """将当前角色的配装（蓝图布局和驱动列表）导入到 my_roles.json 的 drive 字段"""
    eq = self.equipped_state.get(role_name)
    if not eq:
        QMessageBox.warning(self, "导入失败", f"未找到角色 [{role_name}] 的配装数据。")
        return

    bp_layout = eq.get("blueprint_layout", [])
    drives = eq.get("equipped_drives", [])

    if not bp_layout and not drives:
        QMessageBox.information(self, "导入", f"[{role_name}] 当前配装中蓝图和驱动均为空，无需导入。")
        return

    my_roles_path = runtime.USER_CONFIG_DIR / "my_roles.json"

    try:
        # 1. 读取最新文件内容（避免覆盖他人的修改）
        if my_roles_path.exists():
            with open(my_roles_path, "r", encoding="utf-8") as f:
                my_roles = json.load(f)
        else:
            # 如果没有生成要复制一下先
            my_role_model_path = runtime.CONFIG_DIR / "my_roles_model.json"
            import shutil
            shutil.copy(my_role_model_path, my_roles_path)
            with open(my_roles_path, "r", encoding="utf-8") as f:
                my_roles = json.load(f)

        logger.debug(my_roles)
        # 2. 更新目标角色的 drive 字段（保留原有其他字段）
        role_entry = my_roles.setdefault(role_name, {})
        drive_data = role_entry.setdefault("drive", {})
        drive_data["blueprint_layout"] = bp_layout
        drive_data["drives"] = drives
        # 确保 extra_shape_buffs 和 info 存在（不覆盖已有值）
        drive_data.setdefault("extra_shape_buffs", 0)
        # 重新计算 info（汇总驱动属性）
        info = {}
        for d in drives:
            for stats in (d.get("main_stats", {}), d.get("sub_stats", {})):
                for k, v in stats.items():
                    info[k] = info.get(k, 0.0) + float(v)
        drive_data["info"] = info

        # 3. 写入文件
        with open(my_roles_path, "w", encoding="utf-8") as f:
            json.dump(my_roles, f, ensure_ascii=False, indent=4)

        # 4. 同步更新内存中的角色编辑数据（如果角色页面已加载）
        if hasattr(self, "_my_role_form_data") and self._my_role_form_data is not None:
            if role_name not in self._my_role_form_data:
                self._my_role_form_data[role_name] = {}
            self._my_role_form_data[role_name]["drive"] = drive_data

        # 5. 提示成功
        QMessageBox.information(
            self, "导入成功",
            f"[{role_name}] 的配装已导入到 my_roles.json\n蓝图数量：{len(bp_layout)}\n驱动数量：{len(drives)}"
        )
        logger.success(f"已导入 {role_name} 的配装（{len(bp_layout)} 蓝图，{len(drives)} 驱动）")

    except Exception as e:
        QMessageBox.critical(self, "导入错误", f"操作失败：{str(e)}")
        logger.error(f"导入配装失败: {e}")


def _save_eq(self):
    with open(runtime.USER_CONFIG_DIR / "equipped_state.json", "w", encoding="utf-8") as f: json.dump(
        self.equipped_state, f, ensure_ascii=False, indent=4); logger.success("装备状态已保存")
