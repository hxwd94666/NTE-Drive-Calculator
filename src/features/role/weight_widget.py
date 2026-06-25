"""词条权重相关 UI 组件"""

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QInputDialog,
    QMessageBox,
    QWidget,
)
from src.ui.widgets import NoWheelDoubleSpinBox
from .dao import load_stats


def build_weight_group(
    parent_layout,
    window,
    role_name: str,
    role_data: dict,
    on_save_callback,
    on_margin_refresh_callback=None,
):
    """
    构建词条权重 QGroupBox 并添加到 parent_layout
    """
    group_weights = QGroupBox("词条权重")
    main_layout = QVBoxLayout(group_weights)
    main_layout.setSpacing(8)

    # 存储必要信息
    group_weights._window = window
    group_weights._role_name = role_name
    group_weights._role_data = role_data
    group_weights._on_save_callback = on_save_callback
    group_weights._on_margin_refresh_callback = on_margin_refresh_callback

    # ---- 顶部行：标题 + 添加按钮（固定，不刷新） ----
    top_row = QHBoxLayout()
    top_row.addWidget(QLabel("词条权重:"))
    top_row.addStretch()
    add_btn = QPushButton("+ 添加词条")
    add_btn.setObjectName("btnAction")
    add_btn.clicked.connect(lambda: _add_weight(group_weights))
    top_row.addWidget(add_btn)
    main_layout.addLayout(top_row)

    # ---- 容器：用于存放权重行（刷新时只重建此容器） ----
    container = QWidget()
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(4)
    group_weights._container = container
    group_weights._container_layout = container_layout
    main_layout.addWidget(container)

    # 构建初始内容
    _build_weight_group_content(group_weights)

    parent_layout.addWidget(group_weights)
    return group_weights


def _build_weight_group_content(group_weights):
    """构建权重组的内容（只填充容器，不碰顶部行）"""
    container_layout = group_weights._container_layout

    # 清空容器布局中的所有子项
    while container_layout.count():
        item = container_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())

    role_data = group_weights._role_data
    weights_dict = role_data.get("weights", {})

    # 渲染权重行
    for key in sorted(weights_dict.keys()):
        val = weights_dict[key]
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel(key))

        spin = NoWheelDoubleSpinBox()
        spin.setRange(0, 10)
        spin.setSingleStep(0.05)
        spin.setDecimals(3)
        spin.setValue(float(val))
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(
            lambda v, k=key: _update_weight_value(group_weights, k, v)
        )
        row.addWidget(spin)

        del_btn = QPushButton("×")
        del_btn.setObjectName("btnSm")
        del_btn.setFixedSize(28, 28)
        del_btn.clicked.connect(
            lambda checked=False, k=key: _delete_weight(group_weights, k)
        )
        row.addWidget(del_btn)

        container_layout.addLayout(row)

    # 添加弹簧撑开
    container_layout.addStretch()


def _update_weight_value(group_weights, key, value):
    role_data = group_weights._role_data
    role_data.setdefault("weights", {})[key] = value
    on_save = group_weights._on_save_callback
    if on_save:
        on_save()
    on_margin = group_weights._on_margin_refresh_callback
    if on_margin:
        on_margin()


def _delete_weight(group_weights, key):
    role_data = group_weights._role_data
    if key in role_data.get("weights", {}):
        del role_data["weights"][key]
        on_save = group_weights._on_save_callback
        if on_save:
            on_save()
        _refresh_weight_group(group_weights)
        on_margin = group_weights._on_margin_refresh_callback
        if on_margin:
            on_margin()


def _add_weight(group_weights):
    window = group_weights._window
    role_data = group_weights._role_data
    on_save = group_weights._on_save_callback
    on_margin = group_weights._on_margin_refresh_callback

    stats = load_stats()
    pool = sorted(stats.get("weight_pool", []))
    weights_dict = role_data.get("weights", {})
    existing = set(weights_dict.keys())
    available = [s for s in pool if s not in existing]

    if not available:
        QMessageBox.information(window, "提示", "所有词条已添加。")
        return

    wt, ok = QInputDialog.getItem(window, "添加词条", "选择词条:", available, 0, False)
    if ok and wt.strip():
        role_data.setdefault("weights", {})[wt.strip()] = 0.5
        if on_save:
            on_save()
        _refresh_weight_group(group_weights)
        if on_margin:
            on_margin()


def _refresh_weight_group(group_weights):
    """刷新权重组内容（内部使用）"""
    window = group_weights._window
    role_name = group_weights._role_name
    role_data = window._my_role_form_data.get(role_name, {})
    group_weights._role_data = role_data
    _build_weight_group_content(group_weights)


def refresh_weight_group(window, role_name: str):
    if not hasattr(window, "_weight_groups"):
        return
    group_weights = window._weight_groups.get(role_name)
    if group_weights:
        _refresh_weight_group(group_weights)


# 辅助函数：清除布局
def clear_layout(layout):
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())