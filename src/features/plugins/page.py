# 展示紧凑的游戏内显示卡片，并直接编辑各插件的显示选项。
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from src.app.theme import theme_color
from src.ui.common.enum_text import ui_enum_text


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
        # 自绘开关也必须显式呈现键盘焦点，不能只保留不可见的 checkbox 焦点状态。
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme_color("#58a6ff")), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 5, 5)


class PluginsPage(QWidget):
    def __init__(self, *, service, request_apply, show_detection, quick_wheel=None, parent=None, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.service, self.request_apply, self.show_detection = service, request_apply, show_detection
        self.quick_wheel = quick_wheel
        self.cards = {}
        self.option_boxes = {}
        self._refresh_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        hint = plain_label(resolve_text_catalog(text_catalog).render('ui.plugins.init.hint'))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        layout.addWidget(hint)

        self._add_card(
            layout, "cooldown", resolve_text_catalog(text_catalog).render('ui.plugins.init.self'), resolve_text_catalog(text_catalog).render('ui.plugins.init.self_2'),
            (),
        )
        self._add_card(
            layout, "enemy_bars", resolve_text_catalog(text_catalog).render('ui.plugins.init.self_3'), resolve_text_catalog(text_catalog).render('ui.plugins.init.self_4'),
            (("hp", "血量"), ("unbalance", "倾陷")),
        )

        if quick_wheel is not None:
            self.wheel_toggle, self.wheel_badge, wheel_layout = self._add_card(
                layout, "quick_wheel", resolve_text_catalog(text_catalog).render('ui.plugins.init.wheel_badge_wheel_layout'),
                resolve_text_catalog(text_catalog).render('ui.plugins.init.wheel_badge_wheel_layout_2'), (),
                on_toggle=self._toggle_wheel,
            )
            self.wheel_status = plain_label()
            self.wheel_status.setTextFormat(Qt.PlainText)
            self.wheel_status.setWordWrap(True)
            self.wheel_status.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
            wheel_layout.insertWidget(1, self.wheel_status)
            quick_wheel.changed.connect(self._refresh_wheel)
            self._refresh_wheel()

        self.notice = plain_label(resolve_text_catalog(text_catalog).render('ui.plugins.init.notice'))
        self.notice.setObjectName("pluginPageNotice")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
        layout.addWidget(self.notice)

        actions = QHBoxLayout()
        self.refresh_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.plugins.init.refresh_button'))
        self.refresh_button.clicked.connect(self._request_refresh)
        self.environment_button = TextActionButton(resolve_text_catalog(text_catalog).render('home.page.build_home_page.check'))
        self.environment_button.clicked.connect(self.show_detection)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.environment_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.refresh()

    def _toggle_wheel(self, enabled):
        try:
            self.quick_wheel.set_enabled(enabled)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.init.wheel_badge_wheel_layout'), ui_enum_text(self.text_catalog, str(error)))
        self._refresh_wheel()

    def _refresh_wheel(self):
        if self.quick_wheel is None:
            return
        self.wheel_toggle.blockSignals(True)
        self.wheel_toggle.setChecked(self.quick_wheel.enabled)
        self.wheel_toggle.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.refresh_wheel.wheel_toggle') if self.quick_wheel.enabled else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('teams.graduation.state.closed'))
        self.wheel_toggle.blockSignals(False)
        self.wheel_toggle.setEnabled(self.service.policy.allowed("native_load") or self.quick_wheel.enabled)
        text, tone = self._wheel_status_presentation(self.quick_wheel.status, self.quick_wheel.enabled)
        self._set_badge(self.wheel_badge, text, tone, self.quick_wheel.status, self.text_catalog)
        self.wheel_status.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.refresh_wheel.wheel_status', owned_count=f'{self.quick_wheel.owned_count}'))

    @staticmethod
    def _wheel_status_presentation(raw: str, checked: bool) -> tuple[str, str]:
        # 偏好与后验状态分开：关闭请求尚未确认时，不能因开关已关就显示成功。
        if raw == "关闭待确认" or "停用未确认" in raw:
            return "关闭待确认", "warning"
        if not checked and raw in {"未开启", "已关闭"}:
            return "已关闭", "neutral"
        if checked and raw == "已启用":
            return "已启用", "success"
        if raw == "正在启用":
            return "正在启用", "active"
        if raw.startswith(("等待", "账号已切换")):
            return "等待就绪", "warning"
        if raw.startswith("连接已恢复"):
            return "正在恢复", "active"
        return "状态异常", "error"

    def _add_card(self, layout, key: str, title: str, description: str, fields, *, on_toggle=None):
        frame = QFrame()
        frame.setObjectName("card")
        card = QVBoxLayout(frame)
        card.setContentsMargins(20, 14, 20, 14)
        card.setSpacing(9)

        row = QHBoxLayout()
        row.setSpacing(10)
        heading = plain_label(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        row.addWidget(heading)
        status = plain_label()
        status.setObjectName("statusBadge")
        row.addWidget(status)
        row.addStretch()
        toggle = _ToggleSwitch()
        toggle.setObjectName(f"plugin_{key}")
        toggle.setAccessibleName(title)
        if on_toggle is None:
            toggle.toggled.connect(lambda enabled, key=key: self._update(**{key: enabled}))
        else:
            toggle.toggled.connect(on_toggle)
        row.addWidget(toggle)
        card.addLayout(row)

        label = plain_label(description)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme_color('#8b949e')}")
        card.addWidget(label)
        options = QHBoxLayout()
        options.setSpacing(24)
        boxes = {}
        for field, text in fields:
            box = QCheckBox(ui_enum_text(self.text_catalog, text))
            box.toggled.connect(lambda enabled, field=field: self._update(**{field: enabled}))
            options.addWidget(box)
            boxes[field] = box
        options.addStretch()
        if fields:
            card.addLayout(options)
        # 轮盘由独立 controller 管理，不登记成 HUD 设置字段，也不参与 HUD 的刷新和提交。
        if on_toggle is None:
            self.cards[key] = toggle, status
            self.option_boxes[key] = boxes
        layout.addWidget(frame)
        return toggle, status, card

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
    def _set_badge(status: QLabel, text: str, tone: str, detail: str, catalog=None) -> None:
        # 状态判定用服务原文；只有展示文字经目录投影，原文不同于标签时作为诊断提示保留。
        status.setText(ui_enum_text(catalog, text))
        status.setToolTip(ui_enum_text(catalog, detail) if detail and detail != text else "")
        status.setProperty("tone", tone)
        status.style().unpolish(status)
        status.style().polish(status)

    def refresh(self, result=None):
        self._refresh_wheel()
        # 两张卡分别读取后验状态；设置已开不代表已显示，模式降级后仍允许关闭已开的卡。
        allowed = self.service.policy.allowed("native_load")
        settings = self.service.settings
        for key, (toggle, status) in self.cards.items():
            checked = getattr(settings, key)
            raw_status = self.service.status_for(key)
            toggle.blockSignals(True)
            toggle.setChecked(checked)
            toggle.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.refresh_wheel.wheel_toggle') if checked else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('teams.graduation.state.closed'))
            toggle.setEnabled(allowed or checked)
            toggle.blockSignals(False)
            text, tone = self._status_presentation(raw_status, checked)
            self._set_badge(status, text, tone, raw_status, self.text_catalog)
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
            or ui_enum_text(self.text_catalog, next((status for status in active_statuses if status not in known_statuses), ""))
            or (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.init.notice') if allowed else
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.refresh.notice'))
        )
        if result is not None and self._refresh_pending:
            self._refresh_pending = False
            self.refresh_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.init.refresh_button'))
            self.refresh_button.setEnabled(True)

    def _request_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.refresh_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.request_refresh.refresh_button'))
        self.refresh_button.setEnabled(False)
        self.request_apply()

    def _update(self, **changes):
        # 敌人卡至少显示一种状态；两子项全关时只关闭敌人卡，不联动冷却卡或战报。
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
            QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.plugins.update.qmessagebox'), ui_enum_text(self.text_catalog, str(error)))
        else:
            self.request_apply()
        self.refresh()
