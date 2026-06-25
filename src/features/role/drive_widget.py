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

from .core import calc_drive_bonus_stats
from .dao import load_real_inventory, load_my_roles


def build_drive_group(
    parent_layout,
    window,
    role_name: str,
    role_data: dict,
    on_details_callback,
):
    """
    构建驱动加成 QGroupBox 并添加到 parent_layout
    返回创建的 QGroupBox 引用，用于后续刷新
    """
    group_drive = QGroupBox("驱动加成")
    drive_layout = QVBoxLayout(group_drive)
    drive_layout.setSpacing(8)

    # 存储必要信息以便刷新
    group_drive._window = window
    group_drive._role_name = role_name
    group_drive._role_data = role_data
    group_drive._on_details_callback = on_details_callback

    # 构建内容
    _build_drive_group_content(group_drive)

    parent_layout.addWidget(group_drive)
    return group_drive


def _build_drive_group_content(group_drive):
    """构建驱动块的内容（可被刷新复用）"""
    # 清除现有内容（保留布局）
    layout = group_drive.layout()
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    window = group_drive._window
    role_name = group_drive._role_name
    role_data = group_drive._role_data
    on_details_callback = group_drive._on_details_callback

    drive_data = role_data.get("drive", {})
    drives = drive_data.get("drives", [])
    cnt_label = QLabel(f"已装配驱动数量: {len(drives)}")
    layout.addWidget(cnt_label)

    calc_rows = calc_drive_bonus_stats(role_data)
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
        layout.addWidget(QLabel("（暂无驱动/卡带，无法计算汇总属性）"))

    btn_detail = QPushButton("查看驱动详情")
    btn_detail.setObjectName("btnSecondary")
    btn_detail.clicked.connect(on_details_callback)
    layout.addWidget(btn_detail)


def refresh_drive_group(window, role_name: str):
    """
    刷新指定角色的驱动块内容（原地刷新，不重建页面）
    """
    if not hasattr(window, "_drive_groups"):
        return
    group_drive = window._drive_groups.get(role_name)
    if group_drive:
        # 更新 role_data 引用
        role_data = window._my_role_form_data.get(role_name, {})
        group_drive._role_data = role_data
        _build_drive_group_content(group_drive)


def show_drive_details(
    window,
    role_name: str,
    save_callback,
    refresh_callback,
    refresh_margin_callback=None,
    refresh_drive_callback=None,
):
    """
    显示驱动详情弹窗
    """
    role_data = window._my_role_form_data.get(role_name)
    if not role_data:
        return

    drive_data = role_data.get("drive", {})
    bp = drive_data.get("blueprint_layout", [])
    drives = drive_data.get("drives", [])

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
        'drives': drives,
        'role_data': role_data,
        'save_callback': save_callback,
        'refresh_callback': refresh_callback,
        'refresh_margin_callback': refresh_margin_callback,
        'refresh_drive_callback': refresh_drive_callback,
    }

    _build_drive_detail_content(window, layout, role_name, bp, drives, role_data)

    layout.addStretch()
    scroll.setWidget(content)
    root.addWidget(scroll)
    dlg.exec()
    window._drive_detail_dlg = None
    window._drive_detail_state = None


def _build_drive_detail_content(window, layout, role_name, bp, drives, role_data):
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
                    drives
                ),
                0,
                Qt.AlignTop
            )

        row.addStretch()
        group_layout.addLayout(row)
        layout.addWidget(group)

    if drives:
        group = QGroupBox(f"驱动 ({len(drives)}个)")
        group_layout = QVBoxLayout(group)
        weights = role_data.get("weights", {})

        def _save_and_refresh():
            # 保存数据
            state = window._drive_detail_state
            if state and state.get('save_callback'):
                state['save_callback']()
            # 刷新驱动块（不重建页面）
            if state and state.get('refresh_drive_callback'):
                state['refresh_drive_callback']()
            # 刷新边际收益
            if state and state.get('refresh_margin_callback'):
                state['refresh_margin_callback']()

        for d in drives:
            quality = d.get("quality", "Gold")
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

            drive_container = QWidget()
            drive_container_layout = QVBoxLayout(drive_container)
            drive_container_layout.setContentsMargins(0, 0, 0, 0)
            drive_container_layout.setSpacing(4)

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
                drive_container_layout.addWidget(card)

            optimize_btn = QPushButton("优化")
            optimize_btn.setObjectName("btnAction")
            optimize_btn.setFixedWidth(60)
            optimize_btn.clicked.connect(
                lambda checked=False, drive=d, rn=role_name, w=weights:
                _show_drive_optimization(window, rn, drive, w, _save_and_refresh)
            )
            drive_container_layout.addWidget(optimize_btn, alignment=Qt.AlignRight)

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
    drives = drive_data.get("drives", [])

    state['bp'] = bp
    state['drives'] = drives
    state['role_data'] = role_data

    layout = state['layout']
    _build_drive_detail_content(window, layout, role_name, bp, drives, role_data)


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

        # 关闭优化弹窗
        dlg.accept()

        # 执行保存并刷新回调（保存数据、刷新驱动块、刷新边际收益）
        on_save_refresh_callback()

        # 刷新驱动详情弹窗内容
        refresh_drive_detail_content(window)

    dlg = QDialog(window)
    dlg.setWindowTitle(f"优化替换 - {current_shape}")
    dlg.resize(800, 600)
    main_layout = QVBoxLayout(dlg)

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
             window._calc_grade(current_score, window._shape_areas.get(current_shape, 3)) if hasattr(window, "_calc_grade") else "-"),
            current_drive.get("quality", "Gold")
        )
        cur_layout.addWidget(cur_card)
    else:
        cur_layout.addWidget(QLabel(f"UID: {current_uid} Score: {current_score:.2f}"))
    main_layout.addWidget(cur_group)

    cand_group = QGroupBox(f"可替换驱动 ({len(final)})")
    cand_layout = QVBoxLayout(cand_group)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QWidget()
    scroll_layout = QVBoxLayout(scroll_widget)

    for score, d in final:
        quality = d.get("quality", "Gold")
        uid = d.get("uid", "")
        grade = window._calc_grade(score, window._shape_areas.get(current_shape, 3)) if hasattr(window, "_calc_grade") else "-"

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
            scroll_layout.addWidget(card)
        else:
            scroll_layout.addWidget(QLabel(f"UID: {uid} Score: {score:.2f}"))

        if uid in user_map:
            user_label = QLabel(f"使用者: {', '.join(user_map[uid])}")
            user_label.setStyleSheet("color: #ff9800; font-size: 12px; margin-left: 10px;")
            scroll_layout.addWidget(user_label)

        replace_btn = QPushButton("替换")
        replace_btn.setObjectName("btnAction")
        replace_btn.clicked.connect(lambda checked=False, nd=d: _replace_drive(nd))
        scroll_layout.addWidget(replace_btn)

    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    cand_layout.addWidget(scroll)
    main_layout.addWidget(cand_group)

    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dlg.accept)
    main_layout.addWidget(btn_close)

    dlg.exec()