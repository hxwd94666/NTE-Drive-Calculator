# 展示紧凑的游戏内显示卡片，并直接编辑各插件的显示选项。
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from src.app.theme import theme_color


class _ToggleSwitch(QCheckBox):
    """Paint a compact switch while retaining checkbox keyboard semantics."""

    def sizeHint(self) -> QSize:
        return QSize(112, 30)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.5)
        track = QRectF(0, 4, 46, 22)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("#1f6feb" if self.isChecked() else "#30363d")))
        painter.drawRoundedRect(track, 11, 11)
        knob_x = 26 if self.isChecked() else 4
        painter.setBrush(QColor(theme_color("#f0f6fc")))
        painter.drawEllipse(QRectF(knob_x, 7, 16, 16))
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(QRectF(56, 0, self.width() - 56, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text())


class PluginsPage(QWidget):
    def __init__(self, *, service, request_apply, open_settings, parent=None):
        super().__init__(parent)
        self.service, self.request_apply, self.open_settings = service, request_apply, open_settings
        self.cards = {}
        self.option_boxes = {}
        self._refresh_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        hint = QLabel("在游戏中显示辅助信息，不影响战报。设置对所有账号生效。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        layout.addWidget(hint)

        self._add_card(
            layout, "cooldown", "技能冷却", "显示队伍技能倒计时和冷却遮罩。",
            (("ready_cue", "E/Q 就绪提示"),),
        )
        self._add_card(
            layout, "enemy_bars", "敌人状态", "显示敌人的战斗状态。",
            (("hp", "血量"), ("unbalance", "倾陷")),
        )

        self.notice = QLabel("退出 Calc 后停止显示；下次启动时恢复已保存设置。")
        self.notice.setObjectName("pluginPageNotice")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
        layout.addWidget(self.notice)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新状态")
        self.refresh_button.clicked.connect(self._request_refresh)
        self.environment_button = QPushButton("检测与部署")
        self.environment_button.clicked.connect(self._open_environment)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.environment_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.refresh()

    def _add_card(self, layout, key: str, title: str, description: str, fields) -> None:
        frame = QFrame()
        frame.setObjectName("card")
        card = QVBoxLayout(frame)
        card.setContentsMargins(20, 14, 20, 14)
        card.setSpacing(9)

        row = QHBoxLayout()
        row.setSpacing(10)
        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        row.addWidget(heading)
        status = QLabel()
        status.setObjectName("statusBadge")
        row.addWidget(status)
        row.addStretch()
        toggle = _ToggleSwitch()
        toggle.setObjectName(f"plugin_{key}")
        toggle.toggled.connect(lambda enabled, key=key: self._update(**{key: enabled}))
        row.addWidget(toggle)
        card.addLayout(row)

        label = QLabel(description)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme_color('#8b949e')}")
        card.addWidget(label)
        options = QHBoxLayout()
        options.setSpacing(24)
        boxes = {}
        for field, text in fields:
            box = QCheckBox(text)
            box.toggled.connect(lambda enabled, field=field: self._update(**{field: enabled}))
            options.addWidget(box)
            boxes[field] = box
        options.addStretch()
        card.addLayout(options)
        self.cards[key] = toggle, status
        self.option_boxes[key] = boxes
        layout.addWidget(frame)

    @staticmethod
    def _status_presentation(raw: str, checked: bool) -> tuple[str, str]:
        if not checked:
            if raw == "关闭显示待确认":
                return "正在关闭", "active"
            if raw == "关闭显示未确认，等待连接恢复":
                return "关闭待确认", "warning"
            return "已关闭", "neutral"
        if raw == "等待游戏":
            return "等待启动游戏", "warning"
        if raw == "运行中":
            return "显示中", "success"
        if raw == "等待可操作场景":
            return "等待进入可操作场景", "active"
        if raw == "设置已保存，等待应用":
            return "正在应用", "active"
        if raw == "未启用":
            return "等待连接", "active"
        if raw in {"当前模式不可启用", "连接已暂停"}:
            return raw, "warning"
        if "不支持" in raw or "更新配套" in raw or "需要更新" in raw:
            return "组件需更新", "error"
        if "失败" in raw or raw:
            return "连接异常", "error"
        return "等待连接", "active"

    @staticmethod
    def _set_badge(status: QLabel, text: str, tone: str, detail: str) -> None:
        status.setText(text)
        status.setToolTip(detail if detail and detail != text else "")
        status.setProperty("tone", tone)
        status.style().unpolish(status)
        status.style().polish(status)

    def refresh(self, result=None):
        allowed = self.service.policy.allowed("native_load")
        settings = self.service.settings
        for key, (toggle, status) in self.cards.items():
            checked = getattr(settings, key)
            raw_status = self.service.status_for(key)
            toggle.blockSignals(True)
            toggle.setChecked(checked)
            toggle.setText("已开启" if checked else "已关闭")
            toggle.setEnabled(allowed or checked)
            toggle.blockSignals(False)
            text, tone = self._status_presentation(raw_status, checked)
            self._set_badge(status, text, tone, raw_status)
            for field, box in self.option_boxes[key].items():
                box.blockSignals(True)
                box.setChecked(getattr(settings, field))
                box.setEnabled(allowed)
                box.blockSignals(False)

        active_statuses = [
            self.service.status_for(key) for key in self.cards
            if getattr(settings, key)
        ]
        known_statuses = {
            "", "未启用", "等待游戏", "运行中", "等待可操作场景",
            "设置已保存，等待应用", "当前模式不可启用", "连接已暂停",
        }
        self.notice.setText(
            self.service.load_error
            or next((status for status in active_statuses if status not in known_statuses), "")
            or ("退出 Calc 后停止显示；下次启动时恢复已保存设置。" if allowed else
                "当前模式不支持插件；请切换到中风险或开发模式。")
        )
        self.environment_button.setText("检测与部署" if allowed else "工作模式设置")
        if result is not None and self._refresh_pending:
            self._refresh_pending = False
            self.refresh_button.setText("刷新状态")
            self.refresh_button.setEnabled(True)

    def _request_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.refresh_button.setText("正在刷新…")
        self.refresh_button.setEnabled(False)
        self.request_apply()

    def _open_environment(self) -> None:
        target = "deployment" if self.service.policy.allowed("native_load") else "mode"
        self.open_settings(target)

    def _update(self, **changes):
        projected_hp = changes.get("hp", self.service.settings.hp)
        projected_unbalance = changes.get("unbalance", self.service.settings.unbalance)
        if changes.get("enemy_bars") is True and not (projected_hp or projected_unbalance):
            changes["hp"] = True
            projected_hp = True
        if (self.service.settings.enemy_bars and not (projected_hp or projected_unbalance)
                and ("hp" in changes or "unbalance" in changes)):
            changes["enemy_bars"] = False
        try:
            self.service.update(**changes)
        except (PermissionError, OSError, ValueError) as error:
            QMessageBox.warning(self, "插件设置", str(error))
        else:
            self.request_apply()
        self.refresh()
