"""边际收益面板组件 - 可独立刷新"""

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from .core import (
    get_character_total_stats,
    calc_marginal_benefits,
    filter_margins_by_weights,
    apply_margins_to_weights,
)
from .dao import load_stats, save_my_roles

if TYPE_CHECKING:
    # 避免循环导入，运行时不需要
    pass


class MarginalBenefitPanel:
    """
    边际收益面板
    包含直伤评分显示 + 边际收益表格 + "设为权重"按钮
    支持刷新
    """

    def __init__(self, parent_layout, window, role_name: str, role_data: dict):
        """
        Args:
            parent_layout: 父布局，面板将添加到这个布局中
            window: 主窗口对象（用于 QMessageBox 和保存）
            role_name: 角色名称
            role_data: 角色数据字典
        """
        self.parent_layout = parent_layout
        self.window = window
        self.role_name = role_name
        self.role_data = role_data

        # 缓存计算结果
        self.base_damage = 0.0
        self.margins = []
        self.group_box = None
        self.damage_label = None
        self.table = None
        self.set_weights_btn = None

        # 构建面板
        self.build()

    def build(self):
        """构建面板"""
        # 计算数据
        self._calculate()

        # 创建 GroupBox
        self.group_box = QGroupBox("边际收益（按每单位收益排序）")
        layout = QVBoxLayout(self.group_box)

        # 直伤评分
        self.damage_label = QLabel(f"直伤评分 : {self.base_damage:.2f}")
        self.damage_label.setStyleSheet("font-weight: bold; color: #ffaa00; font-size: 14px;")
        layout.addWidget(self.damage_label)

        # 表格
        if self.margins:
            self.table = self._create_table()
            layout.addWidget(self.table)

            # "设为权重"按钮
            self.set_weights_btn = QPushButton("设为权重")
            self.set_weights_btn.setObjectName("btnAction")
            self.set_weights_btn.clicked.connect(self._on_apply_weights)
            layout.addWidget(self.set_weights_btn)

        # 添加到父布局
        self.parent_layout.addWidget(self.group_box)

    def _calculate(self):
        """计算边际收益数据"""
        total_stats = get_character_total_stats(self.role_data)
        self.base_damage, margins = calc_marginal_benefits(total_stats)

        # 根据权重过滤
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

    def _on_apply_weights(self):
        """"设为权重"按钮点击事件"""
        stats_config = load_stats()
        alias_map = stats_config.get("benefit_alias_mapping", {})

        # 获取当前角色的权重数据（从 role_data 中，因为 data 可能已变化）
        weights = self.role_data.setdefault("weights", {})
        updated = apply_margins_to_weights(weights, self.margins, alias_map)

        if updated == 0:
            QMessageBox.information(
                self.window,
                "提示",
                "当前权重中没有与边际收益匹配的词条，未能更新。"
            )
        else:
            # 保存并刷新整个页面
            data = getattr(self.window, "_my_role_form_data", None)
            if data:
                save_my_roles(data)
                QMessageBox.information(
                    self.window,
                    "成功",
                    f"已更新 {updated} 个词条的权重！"
                )

    def refresh(self):
        """
        刷新面板数据
        当角色数据发生变化时调用此方法重新计算并更新UI
        """
        # 重新计算
        self._calculate()

        # 更新直伤评分
        if self.damage_label:
            self.damage_label.setText(f"直伤评分 : {self.base_damage:.2f}")

        # 更新表格
        if self.table:
            # 清除旧表格，重建
            self.table.deleteLater()
            if self.margins:
                self.table = self._create_table()
                # 插入到按钮之前
                if self.set_weights_btn:
                    layout = self.group_box.layout()
                    layout.insertWidget(
                        layout.indexOf(self.set_weights_btn),
                        self.table
                    )
            else:
                self.table = None
                # 如果 margins 为空，隐藏表格，但保留按钮
                if self.set_weights_btn:
                    self.set_weights_btn.setVisible(False)