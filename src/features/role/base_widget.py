# 构建角色基础属性、头像和等级编辑组件。
"""基础加成组件 - 包含头像、等级、基础属性和自定义属性"""

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QFrame,
    QWidget,
    QPushButton,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices

from .paths import get_roles_img_path
from src.app.theme import themed_style
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox


class BaseStatsWidget:
    """
    基础加成组件
    包含头像、等级下拉、基础属性编辑、自定义属性
    支持刷新和等级切换
    """

    BASE_KEYS = ["生命白值", "攻击力白值", "防御力白值", "暴击率%", "暴击伤害%"]

    def __init__(
        self,
        parent_layout,
        window,
        role_name: str,
        role_data: dict,
        on_data_changed_callback=None,
        on_level_changed_callback=None,
    ):
        self.parent_layout = parent_layout
        self.window = window
        self.role_name = role_name
        self.role_data = role_data
        self.on_data_changed_callback = on_data_changed_callback
        self.on_level_changed_callback = on_level_changed_callback

        self.group_base = None
        self.level_combo = None
        self.base_spins = {}

        self.build()

    def build(self):
        self.group_base = QGroupBox("基础加成")
        self.group_base.setStyleSheet("QGroupBox{font-weight:bold;}")
        base_layout = QVBoxLayout(self.group_base)
        base_layout.setSpacing(8)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        left_panel = QWidget()
        left_panel.setMinimumWidth(132)
        left_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        avatar_path = get_roles_img_path(self.role_name)
        if avatar_path.exists():
            pixmap = QPixmap(str(avatar_path))
            if not pixmap.isNull():
                avatar_label = QLabel()
                avatar_label.setMinimumSize(72, 72)
                avatar_label.setMaximumSize(96, 96)
                avatar_label.setScaledContents(True)
                avatar_label.setPixmap(pixmap)
                left_layout.addWidget(avatar_label, alignment=Qt.AlignHCenter)

        level_row = QHBoxLayout()
        level_row.setSpacing(6)

        level_label = QLabel("等级:")
        level_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        level_row.addWidget(level_label)

        level_sub_stats = self.role_data.get("level_sub_stats", {})
        available_levels = sorted(level_sub_stats.keys(), key=lambda x: int(x))
        if not available_levels:
            available_levels = ["1", "20", "30", "40", "50", "60", "70", "80"]

        self.level_combo = NoWheelComboBox()
        self.level_combo.addItems(available_levels)
        current_level = str(self.role_data.get("level", 70))
        if current_level in available_levels:
            self.level_combo.setCurrentText(current_level)
        else:
            self.level_combo.setCurrentIndex(0)
        self.level_combo.setMinimumWidth(72)
        self.level_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.level_combo.setStyleSheet("font-size:14px; padding:4px;")
        level_row.addWidget(self.level_combo)

        # 问号按钮
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.setMinimumSize(16, 16)
        help_btn.setMaximumSize(22, 22)
        help_btn.setStyleSheet("""
            QPushButton#btnHelp {
                background-color: #58a6ff;
                color: white;
                border-radius: 8px;
                font-weight: bold;
                font-size: 10px;
                border: none;
                padding: 0;
            }
            QPushButton#btnHelp:hover {
                background-color: #1f6feb;
            }
        """)
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.clicked.connect(self._show_info_dialog)
        level_row.addWidget(help_btn)
        left_layout.addLayout(level_row)

        # 攻略按钮（根据 guide 字段条件显示）
        guide_url = self.role_data.get("guide", "")
        if guide_url:
            guide_btn = QPushButton("攻略")
            guide_btn.setObjectName("btnGuide")
            guide_btn.setMinimumHeight(24)
            guide_btn.setStyleSheet("""
                QPushButton#btnGuide {
                    background-color: #ff6b6b;
                    color: white;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 12px;
                    padding: 2px 10px;
                    border: none;
                }
                QPushButton#btnGuide:hover {
                    background-color: #e03131;
                }
            """)
            guide_btn.setCursor(Qt.PointingHandCursor)
            guide_btn.clicked.connect(self._open_guide)
            left_layout.addWidget(guide_btn, alignment=Qt.AlignHCenter)

        left_layout.addStretch()
        content_row.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(14)
        stats_grid.setVerticalSpacing(8)
        right_layout.addLayout(stats_grid)
        content_row.addWidget(right_panel, 1)
        base_layout.addLayout(content_row)

        # ========== 基础属性列表 ==========
        sub_stats = self.role_data.get("sub_stats", {})
        self.base_spins = {}

        for index, key in enumerate(self.BASE_KEYS):
            grid_row = index // 2
            grid_col = (index % 2) * 2
            label = QLabel(key)
            label.setMinimumWidth(92)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

            spin = NoWheelDoubleSpinBox()
            spin.setRange(-999999, 999999)
            spin.setDecimals(2)
            spin.setMinimumWidth(110)

            # 初始值
            val = sub_stats.get(key, 0.0)
            if val == 0.0:
                lv = self.level_combo.currentText()
                lv_data = level_sub_stats.get(lv, {})
                val = lv_data.get(key, 0.0)
            spin.setValue(float(val))
            spin.editingFinished.connect(
                lambda k=key, s=spin: self._update_base_stat(k, s.value())
            )

            stats_grid.addWidget(label, grid_row, grid_col)
            stats_grid.addWidget(spin, grid_row, grid_col + 1)
            self.base_spins[key] = spin
        stats_grid.setColumnStretch(1, 1)
        stats_grid.setColumnStretch(3, 1)

        # ========== 分割线 ==========
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet(themed_style("background-color: #30363d; max-height: 1px;"))
        base_layout.addWidget(line)

        # 其他 sub_stats（排除基础属性）
        other_sub = {k: v for k, v in sub_stats.items() if k not in self.BASE_KEYS}
        if other_sub:
            other_label = QLabel("自定义属性")
            other_label.setStyleSheet("color: #888; font-size: 12px; margin-top: 4px;")
            base_layout.addWidget(other_label)
            self._add_custom_rows(base_layout, other_sub)

        # ---- 等级切换事件 ----
        self.level_combo.currentTextChanged.connect(self._on_level_changed)

        self.parent_layout.addWidget(self.group_base)

    def _add_custom_rows(self, parent_layout, custom_dict):
        """添加自定义属性行"""
        for key, val in custom_dict.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(key))

            spin = NoWheelDoubleSpinBox()
            spin.setRange(-999999, 999999)
            spin.setDecimals(2)
            spin.setValue(float(val))
            spin.editingFinished.connect(
                lambda k=key, s=spin: self._update_custom_stat(k, s.value())
            )

            row.addWidget(spin)
            row.addStretch()
            parent_layout.addLayout(row)

    def _update_base_stat(self, key, value):
        """更新基础属性"""
        sub_stats = self.role_data.setdefault("sub_stats", {})
        sub_stats[key] = value
        self._save_and_notify()

    def _update_custom_stat(self, key, value):
        """更新自定义属性"""
        sub_stats = self.role_data.setdefault("sub_stats", {})
        sub_stats[key] = value
        self._save_and_notify()

    def _on_level_changed(self, lv):
        """等级切换事件"""
        level_sub_stats = self.role_data.get("level_sub_stats", {})
        lv_data = level_sub_stats.get(lv, {})
        sub_stats = self.role_data.setdefault("sub_stats", {})

        for key in self.BASE_KEYS:
            val = lv_data.get(key, 0.0)
            if key in self.base_spins:
                self.base_spins[key].setValue(float(val))
            sub_stats[key] = val

        self.role_data["level"] = int(lv) if lv.isdigit() else lv
        self._save_and_notify()

        if self.on_level_changed_callback:
            self.on_level_changed_callback()

    def _save_and_notify(self):
        """标记未保存并触发回调。"""
        if self.on_data_changed_callback:
            self.on_data_changed_callback()

    def refresh(self):
        """刷新组件（外部数据变化后调用）"""
        sub_stats = self.role_data.get("sub_stats", {})
        for key in self.BASE_KEYS:
            if key in self.base_spins:
                val = sub_stats.get(key, 0.0)
                if val == 0.0:
                    lv = self.level_combo.currentText()
                    lv_data = self.role_data.get("level_sub_stats", {}).get(lv, {})
                    val = lv_data.get(key, 0.0)
                self.base_spins[key].setValue(float(val))

    def _show_info_dialog(self):
        """显示基础属性补充提示"""
        msg = QMessageBox(self.window)
        msg.setWindowTitle("关于基础属性")
        msg.setText(
            "部分角色的等级基础值（生命白值、攻击力白值、防御力白值）尚未完全获取。\n\n"
            "如果您有准确的数据，欢迎在 GitHub Issues 中提交补充。\n\n"
            "注意：角色攻击力白值需减去当前装备的弧盘的基础白值攻击力。"
        )
        msg.setIcon(QMessageBox.Information)
        msg.exec()

    def _open_guide(self):
        """打开攻略链接或显示攻略文本"""
        guide = self.role_data.get("guide", "")
        if not guide:
            return
        # 判断是否为 URL（以 http 或 https 开头）
        if guide.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(guide))
        else:
            # 否则当作文本弹窗显示
            msg = QMessageBox(self.window)
            msg.setWindowTitle(f"{self.role_name} 攻略")
            msg.setText(guide)
            msg.setIcon(QMessageBox.Information)
            msg.exec()
