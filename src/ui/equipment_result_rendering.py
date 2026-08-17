# 渲染计算配装结果中的角色、图纸与装备卡片。
"""Result-only portion of the shared equipment presentation component."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.domain.allocation_rating import loadout_total_grade
from src.optimizer.contracts import (
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_CHANGED_UIDS,
    PLAN_SCORE,
    PLAN_VALID,
    plan_drives,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.warehouse_visual_catalog import representative_module_item_id
from src.ui.puzzle_board import PuzzleBoardWidget
from src.features.allocation.results_diff_view import _diff_value


@lru_cache(maxsize=256)
def _game_ui_equipment_icon(
    asset_root: str,
    kind: str,
    item_id: str,
) -> str | None:
    """Resolve a packaged official equipment image once per item ID."""
    if not item_id:
        return None
    icon_path = GameUiAssetCatalog(Path(asset_root)).inventory_item_icon(kind, item_id)
    return str(icon_path) if icon_path is not None else None


def _equipment_item_icon_path(
    item,
    kind: str,
    asset_root: str | Path,
) -> str | None:
    """Read a projected official item ID without requiring a legacy model change."""
    item_id = str(_diff_value(item, "item_id", "") or "")
    if not item_id:
        official = _diff_value(item, "official", {})
        if isinstance(official, dict):
            item_id = str(official.get("item_id") or "")
    return _game_ui_equipment_icon(str(asset_root), kind, item_id)


def _representative_drive_pixmap(
    asset_root: str | Path,
    shape_id: str,
    quality: str,
) -> QPixmap:
    quality_text = str(quality or "").casefold()
    if any(
        token in quality_text
        for token in ("gold", "golden", "orange", "金", "橙")
    ):
        quality_key = "gold"
    elif "purple" in quality_text or "紫" in quality_text:
        quality_key = "purple"
    elif "blue" in quality_text or "蓝" in quality_text:
        quality_key = "blue"
    elif "green" in quality_text or "绿" in quality_text:
        quality_key = "green"
    else:
        quality_key = quality_text or "gold"
    item_id = representative_module_item_id(str(shape_id or ""), quality_key)
    icon_path = _game_ui_equipment_icon(
        str(asset_root),
        "module",
        item_id,
    )
    return QPixmap(icon_path) if icon_path else QPixmap()


def _render_results(self, plan):
    locked_roles = tuple(sorted(getattr(self, "_locked_role_names", ()) or ()))
    if not plan and not locked_roles:
        return
    self.result_card.setVisible(True)
    while self.result_content_layout.count():
        it = self.result_content_layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
    mode_labels = {"role_priority": "角色优先", "global_optimal": "全局最优", "update_mode": "增量更新"}
    mode_name = mode_labels.get(getattr(self, "_pending_strat", ""), "")
    plan_diffs = getattr(self, "allocation_plan_diff", {}) or {}
    if locked_roles:
        locked_label = QLabel(
            "已保留锁定方案："
            + "、".join(locked_roles)
            + "。其真实装备已在评分、词条筛选和求解前排除，本次不会覆盖这些角色。"
        )
        locked_label.setWordWrap(True)
        locked_label.setStyleSheet(
            themed_style(
                "color:#ffcc66;border:1px solid #8b6b25;border-radius:7px;"
                "background:rgba(255,204,102,0.08);padding:8px 10px"
            )
        )
        self.result_content_layout.addWidget(locked_label)
    if not plan:
        return
    for role, p in plan.items():
        if not p or not p.get(PLAN_VALID):
            reason = str((p or {}).get("reason") or "无法凑齐图纸所需的卡带或驱动")
            failure = QLabel(f"❌ {role}: 无有效配装方案\n原因：{reason}")
            failure.setWordWrap(True)
            failure.setStyleSheet(themed_style("color:#f85149;padding:8px 2px"))
            self.result_content_layout.addWidget(failure)
            continue
        role_diff = plan_diffs.get(role, {}) or {}
        added_uids = set(role_diff.get(DIFF_ADDED_UIDS, set()) or set())
        changed_uids = set(p.get(PLAN_CHANGED_UIDS, set()) or set()) if isinstance(p, dict) else set()
        total_score = p.get(PLAN_SCORE, 0)
        total_grade = loadout_total_grade(total_score)
        gc = GRADE_COLORS.get(total_grade, "#58a6ff")
        gbg = theme_rgba(gc, 0.10)

        grp = QGroupBox("")
        grp.setStyleSheet(
            themed_style(
                "QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"
            )
        )
        gl = QVBoxLayout(grp)
        gl.setSpacing(10)
        # Role header: name + score + grade side by side, compact
        role_hdr = QHBoxLayout()
        role_hdr.setSpacing(8)
        # Role name with different color from stat blocks - use teal/cyan tone
        header_height = 34
        rnl = QLabel(role)
        rnl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rnl.setFixedHeight(header_height)
        rnl.setStyleSheet(
            f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:0 14px;background:{theme_rgba('#4dd0e1', 0.10)}"
        )
        role_hdr.addWidget(rnl)
        if role_diff.get(DIFF_CHANGED):
            diff_btn = QPushButton("变动")
            diff_btn.setFixedSize(76, header_height)
            diff_btn.setStyleSheet(
                themed_style(
                    "QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px}QPushButton:hover{background:#388bfd}"
                )
            )
            diff_btn.clicked.connect(lambda _checked=False, rn=role, d=role_diff: self._show_plan_diff_dialog(rn, d))
            role_hdr.addWidget(diff_btn)
        if mode_name:
            ml = QLabel(mode_name)
            ml.setStyleSheet(
                themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px")
            )
            role_hdr.addWidget(ml)
        role_hdr.addStretch()
        # Score badge (separate)
        sf = QFrame()
        sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        slb = QHBoxLayout(sf)
        slb.setSpacing(6)
        slb.setContentsMargins(4, 0, 4, 0)
        sv = QLabel(f"{total_score:.1f}")
        sv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
        slb.addWidget(QLabel("评分"))
        slb.addWidget(sv)
        role_hdr.addWidget(sf)
        # Grade badge (separate)
        gf = QFrame()
        gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
        glb = QHBoxLayout(gf)
        glb.setSpacing(6)
        glb.setContentsMargins(4, 0, 4, 0)
        gv = QLabel(total_grade)
        gv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
        glb.addWidget(QLabel("评级"))
        glb.addWidget(gv)
        role_hdr.addWidget(gf)
        gl.addLayout(role_hdr)
        gl.addSpacing(6)

        board = p.get(PLAN_BLUEPRINT, {}).get("board", [])
        role_cfg = self.roles_db.get(role, {})
        wts = role_cfg.get("weights", {})
        main_wts = role_cfg.get("main_weights")

        tape = p.get(PLAN_ASSIGNED_TAPE)
        drives = plan_drives(p)
        if board:
            gl.addWidget(self._section_label("拼图图纸:"))
            bp_row = QHBoxLayout()
            bp_row.setSpacing(18)
            bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            bp_row.addWidget(PuzzleBoardWidget(board), 0, Qt.AlignTop)
            compare_with_saved = bool(role_diff.get(DIFF_CHANGED))
            bp_row.addWidget(
                self._role_bonus_summary_panel(
                    role,
                    tape,
                    drives,
                    compare_with_saved=compare_with_saved,
                    priority_stats=self._role_stat_priority_stats(role),
                ),
                1 if compare_with_saved else 0,
                Qt.AlignTop,
            )
            gl.addLayout(bp_row)
            gl.addSpacing(8)

        if tape:
            t_score = tape.role_scores.get(role, 0) if hasattr(tape, "role_scores") else 0
            t_grade = self._calc_grade(t_score, 15)
            tape_uid = str(_diff_value(tape, "uid", "") or "")
            tape_changed = bool(_diff_value(tape, "is_changed", False) or tape_uid in changed_uids)
            gl.addWidget(self._section_label("卡带:"))
            gl.addWidget(
                self._equip_card(
                    tape.set_name,
                    tape.main_stats,
                    tape.sub_stats,
                    None,
                    tape.uid,
                    wts,
                    (t_score, t_grade),
                    tape.quality,
                    is_new=(tape_uid in added_uids and not tape_changed),
                    is_changed=tape_changed,
                    is_discarded=bool(getattr(tape, "discarded", False)),
                    main_weights=main_wts,
                    card_variant="result",
                    item_icon_path=_equipment_item_icon_path(
                        tape, "core", Path(self.app_context.paths.asset_dir) / "game_ui"
                    ),
                    main_value=getattr(tape, "main_value", None),
                )
            )
        else:
            missing_core = QLabel(
                "卡带缺失："
                + str(
                    p.get("missing_core_reason")
                    or "当前固定快照没有满足角色约束且可唯一分配的卡带"
                )
                + "（驱动方案仍可保存）"
            )
            missing_core.setWordWrap(True)
            missing_core.setStyleSheet(
                themed_style(
                    "color:#ffcc66;border:1px solid #8b6b25;border-radius:7px;"
                    "background:rgba(255,204,102,0.08);padding:8px 10px"
                )
            )
            gl.addWidget(missing_core)

        if drives:
            gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
            for d in drives:
                score = d.role_scores.get(role, 0) if hasattr(d, "role_scores") else 0
                grade = self._calc_grade(score, d.area)
                mvp_tag = f" 👑第{d.pick_order}顺位" if getattr(d, "is_mvp", False) else ""
                drive_uid = str(_diff_value(d, "uid", "") or "")
                drive_changed = bool(_diff_value(d, "is_changed", False) or drive_uid in changed_uids)
                gl.addWidget(
                    self._equip_card(
                        d.shape_id,
                        "",
                        d.sub_stats,
                        d.shape_id,
                        d.uid + mvp_tag,
                        wts,
                        (score, grade),
                        d.quality,
                        is_new=(drive_uid in added_uids and not drive_changed),
                        is_changed=drive_changed,
                        is_discarded=bool(getattr(d, "discarded", False)),
                        is_duplicate_drive=bool(getattr(d, "is_duplicate_drive", False)),
                        card_variant="result",
                    )
                )
        self.result_content_layout.addWidget(grp)
    self.result_content_layout.addStretch()
