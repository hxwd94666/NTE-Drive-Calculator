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

from .core import calc_drive_bonus_stats, get_character_total_stats, calc_base_damage, get_valid_drives, is_empty_drive
from .dao import load_real_inventory, load_my_roles


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
    group_drive = QGroupBox("驱动加成")
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

    # ---- 顶部行：驱动数量 + 直伤收益 ----
    top_row = QHBoxLayout()
    cnt_label = QLabel(f"已装配驱动数量: {valid_count}/{total_drives}")
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
            # 不含驱动
            no_drive_data = {k: v for k, v in role_data.items() if k != "drive"}
            stats_without = get_character_total_stats(no_drive_data)
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
    calc_rows = calc_drive_bonus_stats(valid_role_data)
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


def _build_drive_detail_content(window, layout, role_name, bp, all_drives, valid_drives, role_data):
    """构建驱动详情弹窗的内容（可被刷新复用）"""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

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

    if all_drives:
        group = QGroupBox(f"驱动 ({len(all_drives)}个)")
        group_layout = QVBoxLayout(group)
        weights = role_data.get("weights", {})

        def _save_and_refresh():
            state = window._drive_detail_state
            if state and state.get('save_callback'):
                state['save_callback']()
            if state and state.get('refresh_drive_callback'):
                state['refresh_drive_callback']()
            if state and state.get('refresh_margin_callback'):
                state['refresh_margin_callback']()

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
        if idx is not None:
            new_entry = {
                "uid": new_drive["uid"],
                "shape_id": new_drive["shape_id"],
                "sub_stats": new_drive["sub_stats"],
                "quality": new_drive.get("quality", "Gold"),
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
                                "display_name": f"{od.get('shape_id', '')}-(空)"
                            }
                            other_drives[i] = empty_drive
                            break

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

        # 直伤收益标签
        margin_label = QLabel(f"直伤收益: {sim_margin:+.2f}%")
        margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 12px;")
        card_layout.addWidget(margin_label)

        if uid in user_map:
            user_label = QLabel(f"使用者: {', '.join(user_map[uid])}")
            user_label.setStyleSheet("color: #ff9800; font-size: 12px;")
            card_layout.addWidget(user_label)

        replace_btn = QPushButton("替换")
        replace_btn.setObjectName("btnAction")
        replace_btn.clicked.connect(lambda checked=False, nd=d: _replace_drive(nd))
        card_layout.addWidget(replace_btn, alignment=Qt.AlignRight)

        scroll_layout.addWidget(card_container)

    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    cand_layout.addWidget(scroll)
    main_layout.addWidget(cand_group)

    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dlg.accept)
    main_layout.addWidget(btn_close)

    dlg.exec()
