# 构建主窗口框架、一级导航和页面切换行为。
"""MainWindow shell and primary navigation helpers."""
from __future__ import annotations
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from src.app.theme import theme_color
from src.features.teams.page import TeamDevelopmentNotice
from src.features.official_role.page import (
    build_official_role_page,
    refresh_official_role_page,
    deactivate_official_role_page,
)
from src.features.inventory.equipment.display.display_view import build_equipment_mode_switch
from src.ui.main_window.navigation import NAV_ITEMS, nav_index_map, nav_item_by_key

class MainWindowNavigationMixin:
    def _build_ui(self):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        outer = QWidget()
        self.setCentralWidget(outer)
        root = QVBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        tb = QWidget()
        tb.setObjectName("titleBar")
        tb.setFixedHeight(38)
        tb.mousePressEvent = self._tb_press
        tb.mouseMoveEvent = self._tb_move
        tb.mouseReleaseEvent = self._tb_release
        tb.mouseDoubleClickEvent = self._tb_dbl
        tl = QHBoxLayout(tb)
        tl.setContentsMargins(14, 0, 4, 0)
        tl.setSpacing(0)
        tl.addWidget(plain_label("  NTE Drive Calc"))
        tl.addStretch()
        for text, oid, slot in [
            ("—", "", self.showMinimized),
            ("□", "", self._toggle_max),
            ("✕", "btnClose", self.request_window_close),
        ]:
            b = QPushButton(text)
            b.setObjectName(oid)
            b.setFixedSize(36, 28)
            b.clicked.connect(slot)
            tl.addWidget(b)
        root.addWidget(tb)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 12, 0, 0)
        sl.setSpacing(0)
        self._nav_buttons = {}
        for item in NAV_ITEMS:
            # Keep the workbench icon-free but reserve the same leading visual
            # column as emoji-prefixed navigation labels.  The page title
            # still uses the unpadded metadata label.
            caption = _ui_text("navigation.page." + item.key)
            nav_label = ("　  " + caption) if item.key == "home" else caption
            button = self._nav(nav_label, item.key)
            setattr(self, item.button_attr, button)
            self._nav_buttons[item.key] = button
            if item.sidebar:
                sl.addWidget(button)
            else:
                button.hide()
        self.work_mode_button = QPushButton(_ui_text('navigation.main.build_ui.work_mode_button'))
        self.work_mode_button.clicked.connect(lambda: self._go("settings"))
        sl.addWidget(self.work_mode_button)
        sl.addStretch()
        body.addWidget(sidebar)

        right = QWidget()
        rr = QVBoxLayout(right)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(0)
        tbar = QWidget()
        tbar.setObjectName("topbar")
        tbh = QHBoxLayout(tbar)
        tbh.setContentsMargins(20, 10, 20, 10)
        self.topbar_title = plain_label(_ui_text("navigation.page." + NAV_ITEMS[0].key))
        tbh.addWidget(self.topbar_title)
        self.topbar_team_notice = TeamDevelopmentNotice(tbar, catalog=getattr(self, 'text_catalog', None))
        self.topbar_team_notice.hide()
        tbh.addWidget(self.topbar_team_notice)
        self.topbar_warehouse_status = QWidget(tbar)
        status_layout = QHBoxLayout(self.topbar_warehouse_status)
        status_layout.setContentsMargins(10, 0, 0, 0)
        status_layout.setSpacing(14)
        self.topbar_warehouse_score = plain_label('', self.topbar_warehouse_status)
        self.topbar_warehouse_score.setStyleSheet('color:#8b949e;font-size:12px')
        self.topbar_warehouse_score.setToolTip(plain_tooltip(_ui_text('navigation.main.build_ui.topbar_warehouse_score.hint')))
        self.topbar_warehouse_score.setAccessibleName(_ui_text('navigation.main.build_ui.topbar_warehouse_score.accessible'))
        status_layout.addWidget(self.topbar_warehouse_score)
        self.topbar_warehouse_conflict_container = QWidget(self.topbar_warehouse_status)
        conflict_layout = QHBoxLayout(self.topbar_warehouse_conflict_container)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.setSpacing(8)
        self.topbar_warehouse_conflict_summary = plain_label('', self.topbar_warehouse_conflict_container)
        self.topbar_warehouse_conflict_summary.setStyleSheet('color:#d29922;font-size:12px')
        conflict_layout.addWidget(self.topbar_warehouse_conflict_summary)
        self.topbar_warehouse_conflict_details = QPushButton(_ui_text('navigation.main.build_ui.topbar_warehouse_conflict_details'), self.topbar_warehouse_conflict_container)
        self.topbar_warehouse_conflict_details.setObjectName('btnAction')
        conflict_layout.addWidget(self.topbar_warehouse_conflict_details)
        self.topbar_warehouse_conflict_container.setVisible(False)
        status_layout.addWidget(self.topbar_warehouse_conflict_container)
        self.topbar_warehouse_status.setVisible(False)
        tbh.addWidget(self.topbar_warehouse_status)
        self.topbar_catalog_return = QPushButton(_ui_text('navigation.main.build_ui.topbar_catalog_return'), tbar)
        self.topbar_catalog_return.setObjectName("staticCatalogReturnToToolbox")
        self.topbar_catalog_return.setVisible(False)
        self.topbar_catalog_return.clicked.connect(lambda: self._go("toolbox"))
        tbh.addWidget(self.topbar_catalog_return)
        self.topbar_source_label = plain_label(_ui_text('navigation.main.build_ui.topbar_source_label'))
        self.topbar_source_label.setStyleSheet("color:#8b949e;font-size:12px;margin-left:12px")
        self.topbar_source_label.setWordWrap(False)
        self.topbar_source_label.setVisible(False)
        tbh.addWidget(self.topbar_source_label)
        tbh.addSpacing(14)
        self.topbar_equipment_modes = build_equipment_mode_switch(self, tbar)
        self.topbar_equipment_modes.setVisible(False)
        tbh.addWidget(self.topbar_equipment_modes)
        tbh.addStretch()
        self.account_combo = QComboBox()
        self.account_combo.setFixedWidth(150)
        self.account_combo.currentIndexChanged.connect(self._on_account_combo_changed)
        tbh.addWidget(self.account_combo)
        account_btn = QPushButton(_ui_text('navigation.main.build_ui.account_btn'))
        account_btn.setObjectName("btnAction")
        account_btn.clicked.connect(self._manage_accounts)
        tbh.addWidget(account_btn)
        guide_btn = QPushButton(_ui_text('navigation.main.build_ui.guide_btn'))
        guide_btn.setObjectName("btnAction")
        guide_btn.clicked.connect(self.onboarding_guide.show)
        tbh.addWidget(guide_btn)
        self.status_lbl = plain_label(_ui_text('navigation.main.build_ui.status_lbl'))
        self.status_lbl.setStyleSheet(f"color:{theme_color('#6e7681')};font-size:12px")
        tbh.addWidget(self.status_lbl)
        guide_btn.setText(_ui_text('navigation.main.build_ui.guide_btn_2'))
        rr.addWidget(tbar)
        self.stack = QStackedWidget()
        for item in NAV_ITEMS:
            if item.key == "identify":
                page = self.identification_controller.build_page()
            elif item.key == "battle_report":
                page = self.battle_report_controller.build_page()
            elif item.key == "execute":
                page = self.scanning_controller.build_page()
            elif item.key == "blueprint":
                page = self.blueprint_page.build()
            elif item.key == "config":
                page = self.basic_weight_page
            elif item.key == "my_role":
                page = build_official_role_page(self)
            elif item.key == "teams":
                page = self.team_controller.page
            elif item.key == "static_catalog":
                page = self.static_catalog_page.build()
            else:
                page = getattr(self, item.page_builder)()
            self.stack.addWidget(page)
        rr.addWidget(self.stack, 1)

        self.log_frame = QWidget()
        self.log_frame.setObjectName("logPanel")
        self.log_frame.setVisible(False)
        lf = QVBoxLayout(self.log_frame)
        lf.setContentsMargins(0, 0, 0, 0)
        lh = QHBoxLayout()
        lh.setContentsMargins(16, 6, 16, 6)
        lh.addWidget(plain_label(_ui_text('navigation.main.build_ui.label')))
        lh.addStretch()
        cb = QPushButton(_ui_text('navigation.main.build_ui.cb'))
        cb.setObjectName("btnSm")
        cb.clicked.connect(self._clear_log)
        lh.addWidget(cb)
        lf.addLayout(lh)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        lf.addWidget(self.log_view)
        rr.addWidget(self.log_frame)
        body.addWidget(right, 1)
        root.addLayout(body)
        QSizeGrip(self).setStyleSheet("background:transparent")
        self._nav_buttons[NAV_ITEMS[0].key].setChecked(True)
        self._refresh_account_combo()

    def _nav(self, text, page):
        # 非侧栏入口仍保留导航状态，但其隐藏按钮必须随主窗口一起释放。
        b = QPushButton(text, self)
        b.setCheckable(True)
        b.clicked.connect(lambda: self._go(page))
        return b

    def _nav_key_for_index(self, index):
        return NAV_ITEMS[index].key if 0 <= index < len(NAV_ITEMS) else NAV_ITEMS[0].key

    def _go(self, page):
        item = nav_item_by_key(page) or NAV_ITEMS[0]
        if item.required_capability and not self.operation_entry(item.required_capability, item.label.strip()):
            return
        indexes = nav_index_map()
        from src.ui.controllers.configuration_controller import defer_page_transition
        current = self._nav_key_for_index(self.stack.currentIndex())
        if defer_page_transition(self, lambda: self._go(page), pages=(current,) if current != item.key else ()):
            return
        if current != item.key:
            if current == "my_role":
                deactivate_official_role_page(self)
            elif current == "config":
                self.basic_weight_page.deactivate()
            elif current == "teams":
                self.team_controller.deactivate()
            elif current == "execute":
                self.scanning_controller.cancel_team_calculation()
        self.stack.setCurrentIndex(indexes.get(item.key, 0))
        self.topbar_title.setText(resolve_text_catalog(getattr(self, "text_catalog", None)).render("navigation.page." + item.key))
        self.topbar_team_notice.setVisible(item.key == 'teams')
        warehouse_status = getattr(self, 'topbar_warehouse_status', None)
        if warehouse_status is not None:
            warehouse_status.setVisible(item.key == 'warehouse')
        self.topbar_catalog_return.setVisible(item.key == "static_catalog")
        self.topbar_source_label.setVisible(item.key in {"identify", "config"})
        self.topbar_equipment_modes.setVisible(item.key == "equipment")
        for btn in self._nav_buttons.values():
            btn.setChecked(False)
        selected_key = item.key if item.sidebar else item.parent_key
        button = self._nav_buttons.get(selected_key or "")
        if button is not None:
            button.setChecked(True)
        self._refresh_navigation_item(item)

    def _refresh_navigation_item(self, item) -> None:
        """Refresh one built page through its public feature boundary."""

        if item.key in {"equipment", "identify", "warehouse"} and not getattr(self, "_allocation_catalog_ready", True):
            self._refresh_execute()
            return  # Completion refreshes only the still-visible dependent page.
        if not item.refresh_method:
            return
        if item.key == "identify":
            self.identification_controller.refresh_options()
        elif item.key == "blueprint":
            self.blueprint_page.refresh()
        elif item.key == "config":
            self.basic_weight_page.refresh()
        elif item.key == "my_role":
            refresh_official_role_page(self)
        elif item.key == "teams":
            self.team_controller.refresh()
        elif item.key == "static_catalog":
            self.static_catalog_page.refresh()
        else:
            getattr(self, item.refresh_method)()

    def refresh_current_account_page(self) -> str:
        """Reload the visible account-scoped page after an account switch."""

        item = nav_item_by_key(
            self._nav_key_for_index(self.stack.currentIndex())
        ) or NAV_ITEMS[0]
        self._refresh_navigation_item(item)
        return item.key
