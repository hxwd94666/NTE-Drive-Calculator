# 装配后半程以紧凑角色列表展示冻结分数和核对状态，诊断折叠且底部动作独立。
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.ui.common.image_scaling import asset_pixmap
from src.ui.roles.role_portrait import custom_role_portrait
from src.ui.common.text_catalog import plain_label


class _DetailsButton(QPushButton):
    """Enter 只切换详情，不落到结果窗的默认关闭按钮。"""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class AssemblyStatusView(QWidget):
    details_expanded = Signal(bool)

    def __init__(self, parent, text):
        super().__init__(parent)
        self.text = text
        self._rows = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = plain_label("", self)
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet(themed_style("color:#f0f6fc;font-size:16px;font-weight:700;"))
        layout.addWidget(self.heading)
        self.summary = plain_label("", self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.facts = plain_label("", self)
        self.facts.setWordWrap(True)
        layout.addWidget(self.facts)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            themed_style(
                "QProgressBar{border:0;background:#30363d;border-radius:4px;min-height:8px;max-height:8px;}"
                "QProgressBar::chunk{background:#58a6ff;border-radius:4px;}"
            )
        )
        layout.addWidget(self.progress_bar)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget(scroll)
        self.content.setObjectName("assemblyRows")
        self.content.setStyleSheet(themed_style("QWidget#assemblyRows{background:#0d1117;}"))
        self.rows_layout = QVBoxLayout(self.content)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        scroll.setWidget(self.content)
        layout.addWidget(scroll, 1)
        self.details_toggle = _DetailsButton(self.content)
        self.details_toggle.setObjectName("assemblyDetailsToggle")
        self.details_toggle.setAccessibleName(text("assembly.status.details"))
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAutoDefault(False)
        self.details_toggle.setFocusPolicy(Qt.StrongFocus)
        self.details_toggle.setMinimumHeight(42)
        self.details_toggle.setStyleSheet(themed_style(
            "QPushButton#assemblyDetailsToggle{background:#161b22;border:1px solid transparent;border-radius:4px;}"
            "QPushButton#assemblyDetailsToggle:hover{background:#21262d;}"
            "QPushButton#assemblyDetailsToggle:focus{border:1px solid #58a6ff;}"
            "QPushButton#assemblyDetailsToggle QLabel{background:transparent;border:0;color:#c9d1d9;}"
        ))
        details_bar = QHBoxLayout(self.details_toggle)
        details_bar.setContentsMargins(12, 8, 12, 8)
        self.details_title = plain_label(text("assembly.status.details"), self.details_toggle)
        self.details_title.setWordWrap(True)
        details_bar.addWidget(self.details_title, 1)
        self.details_action = plain_label("", self.details_toggle)
        self.details_action.setWordWrap(True)
        details_bar.addWidget(self.details_action)
        self.details_arrow = plain_label("", self.details_toggle)
        self.details_arrow.setFixedWidth(18)
        self.details_arrow.setAlignment(Qt.AlignCenter)
        details_bar.addWidget(self.details_arrow)
        # 子标签不截获鼠标；标题、空白和右侧状态均属于同一个键盘可达按钮。
        for label in (self.details_title, self.details_action, self.details_arrow):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.details = plain_label("", self.content)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.setStyleSheet(themed_style("padding:10px 12px;color:#8b949e;border-left:1px solid #30363d;"))
        self.details.hide()
        self.details_toggle.toggled.connect(self._toggle_details)
        self._toggle_details(False)
        self.rows_layout.addWidget(self.details_toggle)
        self.rows_layout.addWidget(self.details)
        self.rows_layout.addStretch()
        self.notice = plain_label(text("assembly.status.stop_notice"), self)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(
            themed_style("background:#101d2f;color:#a7ccff;padding:10px;border-left:3px solid #58a6ff;")
        )
        layout.addWidget(self.notice)

    def _toggle_details(self, checked):
        self.details.setVisible(checked)
        action = self.text("assembly.status.details_collapse" if checked else "assembly.status.details_expand")
        self.details_action.setText(action)
        self.details_arrow.setText("⌃" if checked else "⌄")
        self.details_toggle.setAccessibleDescription(action)
        self.details_expanded.emit(checked)

    def set_details(self, message, *, summary):
        # 摘要已在顶部显示；只移除完全相同的首行，截断摘要的完整原文仍留在详情中。
        first, separator, rest = str(message).partition("\n")
        details = rest.strip() if separator and first == summary else str(message).strip()
        if details == summary:
            details = ""
        self.details.setText(details)
        self.details_toggle.setChecked(False)
        self._toggle_details(False)
        self.details_toggle.setVisible(bool(details))

    def set_targets(self, targets):
        for widget, _state, _detail in self._rows.values():
            self.rows_layout.removeWidget(widget)
            widget.deleteLater()
        self._rows.clear()
        owner = self.window().parentWidget()
        paths = getattr(getattr(owner, "app_context", None), "paths", None)
        asset_root = (
            getattr(paths, "equipment_allocation_asset_root", None)
            or getattr(owner, "game_ui_asset_root", None)
            or bundled_game_ui_asset_root()
        )
        assets = GameUiAssetCatalog(asset_root)
        for index, target in enumerate(targets):
            row = QFrame(self.content)
            row.setObjectName("assemblyRoleRow")
            row.setStyleSheet(
                themed_style(
                    "QFrame#assemblyRoleRow{background:transparent;border:0;border-bottom:1px solid #30363d;}"
                    "QFrame#assemblyRoleRow QLabel{background:transparent;border:0;}"
                )
            )
            body = QHBoxLayout(row)
            body.setContentsMargins(0, 10, 0, 10)
            avatar = plain_label("", row)
            avatar.setFixedSize(44, 44)
            avatar.setAccessibleName(self.text("selector.portrait", role=target.role_name))
            path = assets.character_icon(target.character_id) if target.character_id is not None else None
            portrait = (
                asset_pixmap(path, 40, device_pixel_ratio=self.devicePixelRatioF())
                if path
                else custom_role_portrait(40, self.devicePixelRatioF())
            )
            if portrait.isNull():
                portrait = custom_role_portrait(40, self.devicePixelRatioF())
            avatar.setPixmap(portrait)
            body.addWidget(avatar)
            labels = QVBoxLayout()
            name = plain_label(
                self.text("assembly.status.role_slot", role=target.role_name, slot=target.slot_name), row
            )
            name.setWordWrap(True)
            labels.addWidget(name)
            score = self.text("selector.score_unknown") if target.score is None else f"{target.score:.2f}"
            score_label = plain_label(self.text("assembly.status.score", score=score), row)
            score_label.setWordWrap(True)
            labels.addWidget(score_label)
            detail = plain_label("", row)
            detail.setWordWrap(True)
            labels.addWidget(detail)
            body.addLayout(labels, 1)
            state = plain_label("", row)
            state.setWordWrap(True)
            body.addWidget(state, 0, Qt.AlignRight | Qt.AlignVCenter)
            self.rows_layout.insertWidget(index, row)
            self._rows[target.slot_id] = row, state, detail

    def render(self, model, *, automatic=False):
        total = len(model.rows)
        summary = self.text(
            "assembly.status.visual_counts" if automatic else "assembly.status.counts",
            dispatched=model.dispatched,
            confirmed=model.confirmed,
            partial=model.partial,
            total=total,
        )
        outcomes = self.text(
            "assembly.status.outcomes",
            skipped=model.count_state("skipped"),
            not_started=model.count_state("pending", "not_started"),
            unknown=model.count_state("unknown"),
            failed=model.count_state("failed"),
        )
        self.facts.setText(summary + "\n" + outcomes)
        self.progress_bar.setRange(0, max(1, total))
        # 极速装配的操作进度与后验确认独立；等待新观测不能把已完成的派发进度归零。
        self.progress_bar.setValue(model.confirmed if automatic else model.dispatched)
        self.progress_bar.setAccessibleName(self.text(
            "assembly.status.verifying" if automatic else "assembly.status.dispatch_progress"))
        for row in model.rows:
            widgets = self._rows.get(row.target.slot_id)
            if widgets is None:
                continue
            _widget, state, detail = widgets
            key = "visual_confirmed" if automatic and row.state == "confirmed" else row.state
            state.setText(self.text("assembly.status." + key))
            virtual, missing = row.skip_counts
            skipped = (
                self.text("assembly.status.skip_counts", virtual=virtual, missing=missing) if virtual or missing else ""
            )
            detail.setText("\n".join(part for part in (row.detail, skipped) if part))
            detail.setVisible(bool(row.detail or skipped))
