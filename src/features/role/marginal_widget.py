# 构建角色边际收益分析面板。
"""边际收益面板组件 - 可独立刷新"""

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QMessageBox,
    QCheckBox,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from .core import (
    get_character_total_stats,
    calc_marginal_benefits,
    filter_margins_by_weights,
    apply_margins_to_weights,
    get_valid_drives,
)
from .dao import load_stats

if TYPE_CHECKING:
    pass


class MarginalBenefitPanel:
    """
    边际收益面板
    包含直伤评分显示 + 边际收益表格 + 手动"设为权重"按钮 + 自动权重开关
    支持刷新
    """

    def __init__(
            self,
            parent_layout,
            window,
            role_name: str,
            role_data: dict,
            on_weight_changed_callback=None,  # 权重变化回调
    ):
        """
        Args:
            parent_layout: 父布局，面板将添加到这个布局中
            window: 主窗口对象（用于 QMessageBox 和保存）
            role_name: 角色名称
            role_data: 角色数据字典
            on_weight_changed_callback: 权重变化时的回调（用于刷新权重模块UI）
        """
        self.parent_layout = parent_layout
        self.window = window
        self.role_name = role_name
        self.role_data = role_data
        self.on_weight_changed_callback = on_weight_changed_callback

        # 缓存计算结果
        self.base_damage = 0.0
        self.margins = []
        self.group_box = None
        self.damage_label = None
        self.table = None
        self.set_weights_btn = None
        self.auto_switch = None

        # 自动设为权重开关（默认开启）
        self.auto_apply_enabled = True

        # 构建面板
        self.build()

    def build(self):
        """构建面板"""
        # 计算数据
        self._calculate()

        # 创建 GroupBox
        self.group_box = QGroupBox("边际收益（按每单位收益排序）")
        layout = QVBoxLayout(self.group_box)

        # ---- 标题行：直伤评分 + 自动开关 + 设为权重按钮 ----
        header_row = QHBoxLayout()

        # 直伤评分
        self.damage_label = QLabel(f"直伤评分 : {self.base_damage:.2f}")
        self.damage_label.setStyleSheet("font-weight: bold; color: #ffaa00; font-size: 14px;")
        header_row.addWidget(self.damage_label)

        header_row.addStretch()

        # 自动设为权重开关（默认开启）
        self.auto_switch = QPushButton("自动设为权重")
        self.auto_switch.setToolTip("点击切换自动权重更新")
        self.auto_switch.setObjectName("btnPrimary")
        self.auto_switch.setCheckable(True)
        self.auto_switch.setChecked(True)
        self.auto_switch.clicked.connect(self._on_auto_switch_click)
        self.auto_apply_enabled = True
        header_row.addWidget(self.auto_switch)

        # "设为权重"按钮（手动）
        self.set_weights_btn = QPushButton("设为权重")
        self.set_weights_btn.setObjectName("btnAction")
        self.set_weights_btn.clicked.connect(self._on_apply_weights)
        header_row.addWidget(self.set_weights_btn)

        layout.addLayout(header_row)

        # 表格
        if self.margins:
            self.table = self._create_table()
            layout.addWidget(self.table)

        layout.addStretch()

        # 添加到父布局
        self.parent_layout.addWidget(self.group_box)

    def _calculate(self):
        # 构建用于计算的 role_data，不改变原始对象
        calc_role_data = self.role_data.copy()
        if "drive" in calc_role_data:
            # 复制 drive 字典，避免修改原始数据
            calc_role_data["drive"] = calc_role_data["drive"].copy()
            drives = calc_role_data["drive"].get("drives", [])
            valid_drives = get_valid_drives(drives)
            # 只在计算副本中替换为有效驱动
            calc_role_data["drive"]["drives"] = valid_drives

        total_stats = get_character_total_stats(calc_role_data)
        self.base_damage, margins = calc_marginal_benefits(total_stats)
        weights = self.role_data.get("weights", {})
        self.margins = filter_margins_by_weights(margins, weights)

    def _create_table(self) -> QTableWidget:
        """创建边际收益表格"""
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["参数", "当前值", "1单位", "每单位提升"])
        table.setRowCount(len(self.margins))
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)

        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        for i, (name, cur_val, unit_val, gain) in enumerate(self.margins):
            table.setItem(i, 0, QTableWidgetItem(name))
            table.setItem(i, 1, QTableWidgetItem(cur_val))
            table.setItem(i, 2, QTableWidgetItem(unit_val))
            gain_item = QTableWidgetItem(f"{gain:.4f}%")
            table.setItem(i, 3, gain_item)

        header = table.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.Stretch)

        # 固定高度
        header_height = header.height()
        row_height = table.verticalHeader().defaultSectionSize()
        frame = table.frameWidth() * 2
        total_height = header_height + row_height * len(self.margins) + frame
        table.setFixedHeight(total_height)

        return table

    def _on_auto_switch_click(self, checked=None):
        """点击标签切换自动权重状态"""
        self.auto_apply_enabled = bool(checked)
        if self.auto_apply_enabled:
            self.auto_switch.setText("自动设为权重")
            self.auto_switch.setObjectName("btnPrimary")
            self.auto_switch.style().unpolish(self.auto_switch)
            self.auto_switch.style().polish(self.auto_switch)
            # 开启时立即应用权重
            self._apply_weights(silent=True)
        else:
            self.auto_switch.setText("手动设为权重")
            self.auto_switch.setObjectName("btnAction")
            self.auto_switch.style().unpolish(self.auto_switch)
            self.auto_switch.style().polish(self.auto_switch)

    def _apply_weights(self, silent=False):
        """应用权重（内部方法）"""
        stats_config = load_stats()
        alias_map = stats_config.get("benefit_alias_mapping", {})

        weights = self.role_data.setdefault("weights", {})
        updated = apply_margins_to_weights(weights, self.margins, alias_map)

        if updated > 0:
            self.window._my_role_dirty = True

            # 只触发权重变化回调（刷新权重模块UI）
            if self.on_weight_changed_callback:
                self.on_weight_changed_callback()
            if not silent:
                QMessageBox.information(
                    self.window,
                    "成功",
                    f"已更新 {updated} 个词条的权重，请点击右上角保存写入文件。"
                )

    def _on_apply_weights(self):
        """手动"设为权重"按钮点击事件"""
        stats_config = load_stats()
        alias_map = stats_config.get("benefit_alias_mapping", {})

        weights = self.role_data.setdefault("weights", {})
        updated = apply_margins_to_weights(weights, self.margins, alias_map)

        if updated == 0:
            QMessageBox.information(
                self.window,
                "提示",
                "当前权重中没有与边际收益匹配的词条，未能更新。"
            )
        else:
            self.window._my_role_dirty = True
            QMessageBox.information(
                self.window,
                "成功",
                f"已手动更新 {updated} 个词条的权重，请点击右上角保存写入文件。"
            )

            # 触发权重变化回调，刷新权重模块UI
            if self.on_weight_changed_callback:
                self.on_weight_changed_callback()

    def refresh(self):
        """刷新面板数据（外部调用）"""
        # 重新计算数据（不触发权重更新）
        self._calculate()

        # 更新直伤评分
        if self.damage_label:
            self.damage_label.setText(f"直伤评分 : {self.base_damage:.2f}")

        # 更新表格：初始为空、后续出现数据时也要能创建表格。
        if self.table:
            self.table.deleteLater()
            self.table = None
        if self.margins and self.group_box:
            layout = self.group_box.layout()
            self.table = self._create_table()
            layout.insertWidget(1, self.table)

        # 如果自动开关打开，应用权重（但不要再次调用 refresh）
        if self.auto_apply_enabled and self.margins:
            self._apply_weights(silent=True)
