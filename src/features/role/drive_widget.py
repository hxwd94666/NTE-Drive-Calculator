# 构建角色驱动和卡带本体的空幕加成界面。
"""驱动相关 UI 组件：驱动加成面板、驱动详情弹窗、优化替换弹窗"""

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDialog,
    QScrollArea,
    QWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt
from src.ui.puzzle_board import PuzzleBoardWidget

from .core import (
    calc_equipment_bonus_stats,
    get_character_total_stats,
    calc_base_damage,
    calc_marginal_benefits,
    apply_margins_to_weights,
    get_valid_drives,
    is_empty_drive,
)
from .dao import load_real_inventory, load_my_roles, load_stats
from .equipment_import import set_bonus_from_tape_source, tape_equipment_from_source


def clear_layout(layout):
    """递归清除布局中的所有子项，但不删除 layout 本身"""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())
            item.layout().deleteLater()


def build_drive_group(
        parent_layout,
        window,
        role_name: str,
        role_data: dict,
        on_details_callback,
):
    group_drive = QGroupBox("空幕加成")
    drive_layout = QVBoxLayout(group_drive)
    drive_layout.setSpacing(8)

    group_drive._window = window
    group_drive._role_name = role_name
    group_drive._role_data = role_data
    group_drive._on_details_callback = on_details_callback

    _build_drive_group_content(group_drive)

    parent_layout.addWidget(group_drive)
    return group_drive


def _build_drive_group_content(group_drive):

    clear_layout(group_drive.layout())
    layout = group_drive.layout()

    window = group_drive._window
    role_name = group_drive._role_name
    role_data = group_drive._role_data
    on_details_callback = group_drive._on_details_callback

    drive_data = role_data.get("drive", {})
    all_drives = drive_data.get("drives", [])
    valid_drives = get_valid_drives(all_drives)
    total_drives = len(all_drives)
    valid_count = len(valid_drives)

    tape_data = role_data.get("tape", {})
    has_tape = isinstance(tape_data, dict) and bool(tape_data.get("uid")) and not str(tape_data.get("uid")).startswith("empty_")

    # ---- 顶部行：装备数量 + 直伤收益 ----
    top_row = QHBoxLayout()
    cnt_label = QLabel(f"已装配驱动: {valid_count}/{total_drives}    卡带: {'已装配' if has_tape else '未装配'}")
    top_row.addWidget(cnt_label)
    top_row.addStretch()
    margin_label = QLabel("直伤收益: 0.00%")
    margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px;")
    top_row.addWidget(margin_label)
    layout.addLayout(top_row)

    def _update_total_margin():
        if valid_count == 0:
            margin_label.setText("直伤收益: 0.00%")
            return
        try:
            # 不含驱动和卡带本体
            no_equipment_data = {k: v for k, v in role_data.items() if k not in ("drive", "tape")}
            stats_without = get_character_total_stats(no_equipment_data)
            damage_without = calc_base_damage(stats_without)

            # 只含有效驱动
            valid_role_data = role_data.copy()
            valid_role_data["drive"] = {
                "drives": valid_drives,
                "blueprint_layout": drive_data.get("blueprint_layout", [])
            }
            stats_with = get_character_total_stats(valid_role_data)
            damage_with = calc_base_damage(stats_with)

            if damage_without == 0:
                gain = 0.0
            else:
                gain = (damage_with / damage_without - 1) * 100
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        except Exception as e:
            margin_label.setText("直伤收益: 计算错误")
            print(f"计算驱动总直伤收益失败: {e}")

    group_drive._update_margin = _update_total_margin
    _update_total_margin()

    # 汇总属性只基于有效驱动
    valid_role_data = role_data.copy()
    valid_role_data["drive"] = {
        "drives": valid_drives,
        "blueprint_layout": drive_data.get("blueprint_layout", [])
    }
    valid_role_data["tape"] = tape_data if has_tape else {}
    calc_rows = calc_equipment_bonus_stats(valid_role_data)
    if calc_rows:
        info_group = QGroupBox("汇总属性（实时计算）")
        info_group.setStyleSheet(
            "QGroupBox{border:1px solid #30363d;border-radius:5px;padding:8px;}"
        )
        info_layout = QVBoxLayout(info_group)
        for stat, value in calc_rows:
            row = QHBoxLayout()
            row.addWidget(QLabel(stat))
            val_label = QLabel(f"+{value:.2f}")
            val_label.setStyleSheet("color:#58a6ff;font-weight:700;")
            row.addStretch()
            row.addWidget(val_label)
            info_layout.addLayout(row)
        layout.addWidget(info_group)
    else:
        layout.addWidget(QLabel("（暂无驱动/卡带，首次使用需要执行计算后在 配装 页面点击 导入 对应角色）"))

    btn_detail = QPushButton("查看驱动详情")
    btn_detail.setObjectName("btnSecondary")
    btn_detail.clicked.connect(on_details_callback)
    layout.addWidget(btn_detail)

    _update_total_margin()


def refresh_drive_group(window, role_name: str):
    if not hasattr(window, "_drive_groups"):
        return
    group_drive = window._drive_groups.get(role_name)
    if group_drive:
        role_data = window._my_role_form_data.get(role_name, {})
        group_drive._role_data = role_data
        _build_drive_group_content(group_drive)


# ---------- 驱动详情弹窗 ----------

def show_drive_details(
        window,
        role_name: str,
        save_callback,
        refresh_callback,
        refresh_margin_callback=None,
        refresh_drive_callback=None,
):
    """显示驱动详情弹窗"""
    role_data = window._my_role_form_data.get(role_name)
    if not role_data:
        return

    drive_data = role_data.get("drive", {})
    bp = drive_data.get("blueprint_layout", [])
    all_drives = drive_data.get("drives", [])
    valid_drives = get_valid_drives(all_drives)

    dlg = QDialog(window)
    window._drive_detail_dlg = dlg
    dlg.setWindowTitle(f"{role_name} - 驱动详情")
    dlg.resize(1000, 700)

    root = QVBoxLayout(dlg)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)

    content = QWidget()
    layout = QVBoxLayout(content)

    # 保存状态
    window._drive_detail_state = {
        'dlg': dlg,
        'content': content,
        'layout': layout,
        'role_name': role_name,
        'bp': bp,
        'drives': all_drives,
        'valid_drives': valid_drives,
        'role_data': role_data,
        'save_callback': save_callback,
        'refresh_callback': refresh_callback,
        'refresh_margin_callback': refresh_margin_callback,
        'refresh_drive_callback': refresh_drive_callback,
    }

    _build_drive_detail_content(window, layout, role_name, bp, all_drives, valid_drives, role_data)

    layout.addStretch()
    scroll.setWidget(content)
    root.addWidget(scroll)
    dlg.exec()
    window._drive_detail_dlg = None
    window._drive_detail_state = None


def _calc_single_drive_margin(role_data: dict, drive_to_exclude) -> float:
    """
    计算单个驱动在整体配置中的直伤收益
    返回百分比值（如 5.23 表示 5.23%）
    """
    # 如果驱动为空，直接返回0
    if is_empty_drive(drive_to_exclude):
        return 0.0

    try:
        drive_data = role_data.get("drive", {})
        original_drives = drive_data.get("drives", [])
        # 获取有效驱动（排除空驱动）
        valid_drives = get_valid_drives(original_drives)

        # 过滤掉要排除的驱动（仅在有效驱动中排除）
        if drive_to_exclude:
            exclude_uid = drive_to_exclude.get("uid")
            if exclude_uid:
                filtered_drives = [d for d in valid_drives if d.get("uid") != exclude_uid]
            else:
                filtered_drives = [d for d in valid_drives if d is not drive_to_exclude]
        else:
            filtered_drives = valid_drives

        # 构造不含该驱动的角色数据
        no_drive_data = {k: v for k, v in role_data.items() if k != "drive"}
        no_drive_data["drive"] = {"drives": filtered_drives}

        stats_without = get_character_total_stats(no_drive_data)
        damage_without = calc_base_damage(stats_without)

        # 包含该驱动的伤害（包含所有有效驱动）
        # 注意：这里要包含该驱动（因为它是有效驱动），所以使用全部有效驱动
        stats_with = get_character_total_stats(role_data)
        damage_with = calc_base_damage(stats_with)

        if damage_without == 0:
            return 0.0
        return (damage_with / damage_without - 1) * 100
    except Exception as e:
        print(f"计算单个驱动直伤收益失败: {e}")
        return 0.0


def _main_stat_label(tape: dict) -> str:
    main_stats = tape.get("main_stats", {})
    if isinstance(main_stats, dict):
        return next(iter(main_stats.keys()), "")
    return str(main_stats or "")


def _calc_tape_margin(role_data: dict) -> float:
    tape = role_data.get("tape", {})
    if not isinstance(tape, dict) or not tape.get("uid") or str(tape.get("uid")).startswith("empty_"):
        return 0.0
    try:
        no_tape_data = {k: v for k, v in role_data.items() if k != "tape"}
        stats_without = get_character_total_stats(no_tape_data)
        damage_without = calc_base_damage(stats_without)
        stats_with = get_character_total_stats(role_data)
        damage_with = calc_base_damage(stats_with)
        if damage_without == 0:
            return 0.0
        return (damage_with / damage_without - 1) * 100
    except Exception as e:
        print(f"计算卡带直伤收益失败: {e}")
        return 0.0


def _calc_tape_replacement_margin(role_data: dict, candidate_tape: dict) -> float:
    """计算候选卡带在模拟替换后的直伤收益。"""
    try:
        sim_role_data = dict(role_data)
        sim_role_data["tape"] = candidate_tape
        sim_role_data["set_bonus"] = set_bonus_from_tape_source(candidate_tape)
        return _calc_tape_margin(sim_role_data)
    except Exception as e:
        print(f"计算候选卡带直伤收益失败: {e}")
        return 0.0


def _score_tape(window, role_name: str, tape: dict, weights: dict) -> tuple[float, str]:
    score = 0.0
    if hasattr(window, "_score_tape_dict"):
        score = window._score_tape_dict(
            _main_stat_label(tape),
            tape.get("sub_stats", {}) or {},
            weights,
            tape.get("quality", "Gold"),
        )
    grade = window._calc_grade(score, 15) if hasattr(window, "_calc_grade") else "D"
    return score, grade


def _role_scoring_weights(window, role_name: str, role_data: dict | None = None) -> dict:
    roles_db = getattr(window, "roles_db", {}) or {}
    role_config = roles_db.get(role_name, {}) if isinstance(roles_db, dict) else {}
    role_weights = role_config.get("weights") if isinstance(role_config, dict) else None
    base_weights = dict(role_weights) if isinstance(role_weights, dict) else {}

    if isinstance(role_data, dict) and base_weights:
        try:
            _base_damage, margins = calc_marginal_benefits(get_character_total_stats(role_data))
            if margins:
                dynamic_weights = dict(base_weights)
                stats_config = load_stats()
                alias_map = stats_config.get("benefit_alias_mapping", {})
                apply_margins_to_weights(dynamic_weights, margins, alias_map)
                return dynamic_weights
        except Exception:
            pass

    if base_weights:
        return base_weights
    return {}


def _build_drive_detail_content(window, layout, role_name, bp, all_drives, valid_drives, role_data):
    """构建驱动详情弹窗的内容（可被刷新复用）"""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    def _save_and_refresh():
        state = window._drive_detail_state
        if state and state.get('save_callback'):
            state['save_callback']()
        if state and state.get('refresh_drive_callback'):
            state['refresh_drive_callback']()
        if state and state.get('refresh_margin_callback'):
            state['refresh_margin_callback']()

    if bp:
        group = QGroupBox("拼图图纸")
        group_layout = QVBoxLayout(group)

        row = QHBoxLayout()
        row.addWidget(
            PuzzleBoardWidget(bp),
            0,
            Qt.AlignTop
        )
        if hasattr(window, "_bonus_summary_widget"):
            row.addWidget(
                window._bonus_summary_widget(
                    role_name,
                    None,
                    valid_drives
                ),
                0,
                Qt.AlignTop
            )
        row.addStretch()
        group_layout.addLayout(row)
        layout.addWidget(group)

    tape_data = role_data.get("tape", {})
    if isinstance(tape_data, dict) and tape_data.get("uid"):
        group = QGroupBox("卡带")
        group_layout = QVBoxLayout(group)
        weights = _role_scoring_weights(window, role_name, role_data)
        score, grade = _score_tape(window, role_name, tape_data, weights)
        tape_margin = _calc_tape_margin(role_data)

        if hasattr(window, "_equip_card"):
            card = window._equip_card(
                tape_data.get("set_name") or tape_data.get("display_name", "卡带"),
                _main_stat_label(tape_data),
                tape_data.get("sub_stats", {}) or {},
                None,
                tape_data.get("uid", ""),
                weights,
                (score, grade),
                tape_data.get("quality", "Gold"),
                is_changed=bool(tape_data.get("is_changed")),
            )
            group_layout.addWidget(card)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        margin_label = QLabel(f"直伤收益: {tape_margin:+.2f}%")
        margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 12px;")
        bottom_row.addWidget(margin_label)
        optimize_btn = QPushButton("替换")
        optimize_btn.setObjectName("btnAction")
        optimize_btn.setFixedWidth(60)
        optimize_btn.clicked.connect(
            lambda checked=False, tape=tape_data, rn=role_name, w=weights:
            _show_tape_optimization(window, rn, tape, w, _save_and_refresh)
        )
        bottom_row.addWidget(optimize_btn)
        group_layout.addLayout(bottom_row)
        layout.addWidget(group)

    if all_drives:
        group = QGroupBox(f"驱动 ({len(all_drives)}个)")
        group_layout = QVBoxLayout(group)
        weights = _role_scoring_weights(window, role_name, role_data)

        for d in all_drives:
            quality = d.get("quality", "Gold")
            # 计算评分（空驱动 sub_stats 为空，得分应为0或能正常处理）
            if hasattr(window, "_score_drive_dict"):
                score = window._score_drive_dict(
                    d.get("sub_stats", {}),
                    d.get("shape_id", ""),
                    weights,
                    quality,
                )
                grade = window._calc_grade(
                    score,
                    window._shape_areas.get(
                        d.get("shape_id", ""),
                        3,
                    ),
                )
            else:
                score = 0
                grade = "-"

            # 计算直伤收益（空驱动返回0）
            margin_gain = _calc_single_drive_margin(role_data, d)

            # 创建卡片容器
            drive_container = QWidget()
            drive_container_layout = QVBoxLayout(drive_container)
            drive_container_layout.setContentsMargins(0, 0, 0, 0)
            drive_container_layout.setSpacing(4)

            # ---------- 统一卡片渲染（不再区分是否为空驱动） ----------
            if hasattr(window, "_equip_card"):
                card = window._equip_card(
                    d.get("shape_id", ""),
                    "",
                    d.get("sub_stats", {}),   # 空驱动就是 {}
                    d.get("shape_id", ""),
                    d.get("uid", ""),
                    weights,
                    (score, grade),
                    quality,
                    is_changed=bool(d.get("is_changed")),
                )
                drive_container_layout.addWidget(card)

            # 底部行：直伤收益 + 优化按钮（始终显示）
            bottom_row = QHBoxLayout()
            bottom_row.addStretch()

            margin_label = QLabel(f"直伤收益: {margin_gain:+.2f}%")
            margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 12px;")
            bottom_row.addWidget(margin_label)

            # 优化按钮：无论是否空驱动都显示
            optimize_btn = QPushButton("优化")
            optimize_btn.setObjectName("btnAction")
            optimize_btn.setFixedWidth(60)
            optimize_btn.clicked.connect(
                lambda checked=False, drive=d, rn=role_name, w=weights:
                _show_drive_optimization(window, rn, drive, w, _save_and_refresh)
            )
            bottom_row.addWidget(optimize_btn)

            drive_container_layout.addLayout(bottom_row)
            group_layout.addWidget(drive_container)

        layout.addWidget(group)


def refresh_drive_detail_content(window):
    """刷新驱动详情弹窗的内容（优化替换后调用）"""
    state = getattr(window, '_drive_detail_state', None)
    if not state:
        return

    dlg = state.get('dlg')
    if not dlg or not dlg.isVisible():
        return

    role_name = state['role_name']
    role_data = window._my_role_form_data.get(role_name)
    if not role_data:
        return

    drive_data = role_data.get("drive", {})
    bp = drive_data.get("blueprint_layout", [])
    all_drives = drive_data.get("drives", [])
    valid_drives = get_valid_drives(all_drives)

    state['bp'] = bp
    state['drives'] = all_drives
    state['valid_drives'] = valid_drives
    state['role_data'] = role_data

    layout = state['layout']
    _build_drive_detail_content(window, layout, role_name, bp, all_drives, valid_drives, role_data)


def _show_tape_optimization(
        window,
        role_name,
        current_tape,
        weights,
        on_save_refresh_callback,
):
    """卡带替换弹窗。"""
    all_items = load_real_inventory()
    if not all_items:
        QMessageBox.warning(window, "错误", "real_inventory.json 不存在或格式错误")
        return

    my_roles_data = load_my_roles()
    user_map = {}
    for rn, rdata in my_roles_data.items():
        if rn == role_name:
            continue
        tape = rdata.get("tape", {}) if isinstance(rdata, dict) else {}
        uid = tape.get("uid") if isinstance(tape, dict) else ""
        if uid:
            user_map.setdefault(uid, []).append(rn)

    role_data = window._my_role_form_data.get(role_name, {})
    current_uid = current_tape.get("uid", "")
    current_set = current_tape.get("set_name", "")
    equipped_uids = {
        str(role_data.get("tape", {}).get("uid", "") or ""),
        *[str(d.get("uid", "") or "") for d in role_data.get("drive", {}).get("drives", []) or []],
    }

    candidates = []
    for item in all_items:
        if item.get("item_type") != "tape" and item.get("shape_id") != "TAPE_15":
            continue
        if current_set and item.get("set_name") != current_set:
            continue
        uid = item.get("uid", "")
        if not uid or uid == current_uid or uid in equipped_uids:
            continue
        tape_entry = tape_equipment_from_source(item)
        if tape_entry:
            score, _grade = _score_tape(window, role_name, tape_entry, weights)
            candidates.append((score, tape_entry, item))

    candidates.sort(key=lambda item: item[0], reverse=True)
    final = list(candidates[:20])
    unassigned_count = sum(1 for _, tape, _raw in final if tape.get("uid", "") not in user_map)
    if unassigned_count < 3:
        for entry in candidates[20:]:
            if entry[1].get("uid", "") not in user_map:
                final.append(entry)
                unassigned_count += 1
                if unassigned_count >= 3:
                    break

    if not final:
        QMessageBox.information(window, "替换", "没有可替换的同套装卡带")
        return

    dlg = QDialog(window)
    dlg.setWindowTitle(f"替换卡带 - {current_set or '全部套装'}")
    dlg.resize(850, 650)
    main_layout = QVBoxLayout(dlg)

    current_group = QGroupBox("当前卡带")
    current_layout = QVBoxLayout(current_group)
    current_score, current_grade = _score_tape(window, role_name, current_tape, weights)
    if hasattr(window, "_equip_card"):
        current_layout.addWidget(
            window._equip_card(
                current_tape.get("set_name") or current_tape.get("display_name", "卡带"),
                _main_stat_label(current_tape),
                current_tape.get("sub_stats", {}) or {},
                None,
                current_uid,
                weights,
                (current_score, current_grade),
                current_tape.get("quality", "Gold"),
            )
        )
    main_layout.addWidget(current_group)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QWidget()
    scroll_layout = QVBoxLayout(scroll_widget)

    def _replace_tape(new_tape, raw_item):
        new_tape["is_changed"] = True
        role_data["tape"] = new_tape
        role_data["set_bonus"] = set_bonus_from_tape_source(new_tape)
        dirty_equipment_roles = {role_name}
        new_uid = new_tape.get("uid", "")

        if new_uid in user_map:
            for other_role in user_map[new_uid]:
                other_role_data = window._my_role_form_data.get(other_role, {})
                old_tape = other_role_data.get("tape", {})
                if isinstance(old_tape, dict) and old_tape.get("uid") == new_uid:
                    other_role_data["tape"] = {
                        "uid": f"empty_{new_uid}",
                        "display_name": "空卡带",
                        "shape_id": "TAPE_15",
                        "set_name": old_tape.get("set_name", ""),
                        "quality": "Gold",
                        "main_stats": {},
                        "sub_stats": {},
                        "is_changed": True,
                    }
                    other_role_data["set_bonus"] = {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8}
                    dirty_equipment_roles.add(other_role)

        if not hasattr(window, "_my_role_equipment_dirty_roles"):
            window._my_role_equipment_dirty_roles = set()
        window._my_role_equipment_dirty_roles.update(dirty_equipment_roles)

        dlg.accept()
        on_save_refresh_callback()
        refresh_drive_detail_content(window)

    for score, tape, raw_item in final:
        grade = window._calc_grade(score, 15) if hasattr(window, "_calc_grade") else "-"
        tape_margin = _calc_tape_replacement_margin(role_data, tape)
        card_container = QWidget()
        card_layout = QVBoxLayout(card_container)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)
        if hasattr(window, "_equip_card"):
            card_layout.addWidget(
                window._equip_card(
                    tape.get("set_name") or tape.get("display_name", "卡带"),
                    _main_stat_label(tape),
                    tape.get("sub_stats", {}) or {},
                    None,
                    tape.get("uid", ""),
                    weights,
                    (score, grade),
                    tape.get("quality", "Gold"),
                )
            )
        replace_btn = QPushButton("替换")
        replace_btn.setObjectName("btnAction")
        replace_btn.clicked.connect(lambda checked=False, t=tape, r=raw_item: _replace_tape(t, r))
        action_row = QHBoxLayout()
        margin_label = QLabel(f"直伤收益: {tape_margin:+.2f}%")
        margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 12px;")
        action_row.addWidget(margin_label)
        action_row.addStretch()
        action_row.addWidget(replace_btn)
        card_layout.addLayout(action_row)
        if tape.get("uid", "") in user_map:
            user_label = QLabel(f"使用者: {', '.join(user_map[tape.get('uid', '')])}")
            user_label.setStyleSheet("color: #ff9800; font-size: 12px;")
            card_layout.addWidget(user_label)
        scroll_layout.addWidget(card_container)

    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    main_layout.addWidget(scroll)
    close_btn = QPushButton("关闭")
    close_btn.clicked.connect(dlg.accept)
    main_layout.addWidget(close_btn)
    dlg.exec()


# ---------- 优化替换弹窗 ----------

def _show_drive_optimization(
        window,
        role_name,
        current_drive,
        weights,
        on_save_refresh_callback,
):
    """驱动优化替换弹窗"""
    all_drives = load_real_inventory()
    if not all_drives:
        QMessageBox.warning(window, "错误", "real_inventory.json 不存在或格式错误")
        return

    my_roles_data = load_my_roles()
    user_map = {}
    for rn, rdata in my_roles_data.items():
        if rn == role_name:
            continue
        drives = rdata.get("drive", {}).get("drives", [])
        for d in drives:
            uid = d.get("uid")
            if uid:
                if uid not in user_map:
                    user_map[uid] = []
                user_map[uid].append(rn)

    current_shape = current_drive.get("shape_id", "")
    current_uid = current_drive.get("uid", "")

    role_data = window._my_role_form_data.get(role_name, {})
    equipped_drives = role_data.get("drive", {}).get("drives", [])
    equipped_uids = {d.get("uid", "") for d in equipped_drives}

    if hasattr(window, "_score_drive_dict"):
        current_score = window._score_drive_dict(
            current_drive.get("sub_stats", {}),
            current_shape,
            weights,
            current_drive.get("quality", "Gold")
        )
    else:
        current_score = 0

    # 计算当前驱动的直伤收益
    current_margin = _calc_single_drive_margin(role_data, current_drive)

    candidates = []
    for d in all_drives:
        if d.get("shape_id") == current_shape and d.get("uid") not in equipped_uids and d.get("uid") != current_uid:
            candidates.append(d)

    if not candidates:
        QMessageBox.information(window, "优化", "没有可替换的驱动")
        return

    candidate_scores = []
    for d in candidates:
        score = window._score_drive_dict(
            d.get("sub_stats", {}),
            d.get("shape_id", ""),
            weights,
            d.get("quality", "Gold")
        )
        candidate_scores.append((score, d))

    candidate_scores.sort(key=lambda x: x[0], reverse=True)

    final = list(candidate_scores[:20])
    unassigned_count = sum(1 for _, d in final if d.get("uid", "") not in user_map)
    if unassigned_count < 3:
        for s, d in candidate_scores[20:]:
            if d.get("uid", "") not in user_map:
                final.append((s, d))
                unassigned_count += 1
                if unassigned_count >= 3:
                    break

    if not final:
        QMessageBox.information(window, "优化", "没有更好的驱动（或符合条件）")
        return

    def _replace_drive(new_drive):
        drives_list = role_data["drive"]["drives"]
        idx = next((i for i, d in enumerate(drives_list) if d.get("uid") == current_uid), None)
        if idx is None:
            QMessageBox.warning(window, "替换失败", "当前驱动已不存在，请刷新后重试。")
            return
        dirty_equipment_roles = {role_name}
        new_entry = {
            "uid": new_drive["uid"],
            "shape_id": new_drive["shape_id"],
            "sub_stats": new_drive["sub_stats"],
            "quality": new_drive.get("quality", "Gold"),
            "is_changed": True,
            "display_name": f"{new_drive['shape_id']}-" + "|".join(
                f"{k}_{v}" for k, v in new_drive["sub_stats"].items()
            )
        }
        drives_list[idx] = new_entry

        new_uid = new_drive["uid"]
        if new_uid in user_map:
            for other_role in user_map[new_uid]:
                other_drives = window._my_role_form_data.get(other_role, {}).get("drive", {}).get("drives", [])
                for i, od in enumerate(other_drives):
                    if od.get("uid") == new_uid:
                        empty_drive = {
                            "uid": f"empty_{new_uid}",
                            "shape_id": od.get("shape_id", ""),
                            "sub_stats": {},
                            "quality": "Gold",
                            "is_changed": True,
                            "display_name": f"{od.get('shape_id', '')}-(空)"
                        }
                        other_drives[i] = empty_drive
                        dirty_equipment_roles.add(other_role)
                        break
        if not hasattr(window, "_my_role_equipment_dirty_roles"):
            window._my_role_equipment_dirty_roles = set()
        window._my_role_equipment_dirty_roles.update(dirty_equipment_roles)

        dlg.accept()
        on_save_refresh_callback()
        refresh_drive_detail_content(window)

    # ---------- 构建弹窗 ----------
    dlg = QDialog(window)
    dlg.setWindowTitle(f"优化替换 - {current_shape}")
    dlg.resize(850, 650)
    main_layout = QVBoxLayout(dlg)

    # 当前驱动
    cur_group = QGroupBox("当前驱动")
    cur_layout = QVBoxLayout(cur_group)
    if hasattr(window, "_equip_card"):
        cur_card = window._equip_card(
            current_shape,
            "",
            current_drive.get("sub_stats", {}),
            current_shape,
            current_uid,
            weights,
            (current_score,
             window._calc_grade(current_score, window._shape_areas.get(current_shape, 3)) if hasattr(window,
                                                                                                     "_calc_grade") else "-"),
            current_drive.get("quality", "Gold")
        )
        cur_layout.addWidget(cur_card)
    else:
        cur_layout.addWidget(QLabel(f"UID: {current_uid} Score: {current_score:.2f}"))

    # 当前驱动的直伤收益
    cur_margin_label = QLabel(f"直伤收益: {current_margin:+.2f}%")
    cur_margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px; margin-top: 4px;")
    cur_layout.addWidget(cur_margin_label)

    main_layout.addWidget(cur_group)

    # 候选驱动
    cand_group = QGroupBox(f"可替换驱动 ({len(final)})")
    cand_layout = QVBoxLayout(cand_group)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QWidget()
    scroll_layout = QVBoxLayout(scroll_widget)

    for score, d in final:
        quality = d.get("quality", "Gold")
        uid = d.get("uid", "")
        grade = window._calc_grade(score, window._shape_areas.get(current_shape, 3)) if hasattr(window,
                                                                                                "_calc_grade") else "-"

        # ---- 计算该候选驱动的直伤收益 ----
        # 1. 用新驱动替换当前驱动后的角色数据（只使用有效驱动）
        sim_role_data = {k: v for k, v in role_data.items() if k != "drive"}
        bp = role_data.get("drive", {}).get("blueprint_layout", [])
        # 获取当前有效驱动（排除空驱动）
        current_valid_drives = get_valid_drives(equipped_drives)
        # 移除当前驱动（如果当前驱动有效）
        sim_drives = [drive for drive in current_valid_drives if drive.get("uid") != current_uid]
        # 添加候选驱动（候选驱动肯定不是空驱动）
        sim_drives.append({
            "uid": d["uid"],
            "shape_id": d["shape_id"],
            "sub_stats": d["sub_stats"],
            "quality": d.get("quality", "Gold"),
        })
        sim_role_data["drive"] = {"drives": sim_drives, "blueprint_layout": bp}

        # 2. 包含该候选驱动的伤害
        stats_with = get_character_total_stats(sim_role_data)
        damage_with = calc_base_damage(stats_with)

        # 3. 排除该候选驱动后的伤害
        exclude_drive_data = {k: v for k, v in sim_role_data.items() if k != "drive"}
        candidate_uid = d["uid"]
        exclude_drives = [drive for drive in sim_drives if drive.get("uid") != candidate_uid]
        exclude_drive_data["drive"] = {"drives": exclude_drives, "blueprint_layout": bp}
        stats_without = get_character_total_stats(exclude_drive_data)
        damage_without = calc_base_damage(stats_without)

        if damage_without == 0:
            sim_margin = 0.0
        else:
            sim_margin = (damage_with / damage_without - 1) * 100

        # 创建卡片容器...
        card_container = QWidget()
        card_layout = QVBoxLayout(card_container)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)

        if hasattr(window, "_equip_card"):
            card = window._equip_card(
                d.get("shape_id", ""),
                "",
                d.get("sub_stats", {}),
                d.get("shape_id", ""),
                d.get("uid", ""),
                weights,
                (score, grade),
                quality,
            )
            card_layout.addWidget(card)
        else:
            card_layout.addWidget(QLabel(f"UID: {uid} Score: {score:.2f}"))

        replace_btn = QPushButton("替换")
        replace_btn.setObjectName("btnAction")
        replace_btn.clicked.connect(lambda checked=False, nd=d: _replace_drive(nd))
        action_row = QHBoxLayout()
        margin_label = QLabel(f"直伤收益: {sim_margin:+.2f}%")
        margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 12px;")
        action_row.addWidget(margin_label)
        action_row.addStretch()
        action_row.addWidget(replace_btn)
        card_layout.addLayout(action_row)

        if uid in user_map:
            user_label = QLabel(f"使用者: {', '.join(user_map[uid])}")
            user_label.setStyleSheet("color: #ff9800; font-size: 12px;")
            card_layout.addWidget(user_label)

        scroll_layout.addWidget(card_container)

    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    cand_layout.addWidget(scroll)
    main_layout.addWidget(cand_group)

    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dlg.accept)
    main_layout.addWidget(btn_close)

    dlg.exec()
