# 构建主窗口框架、一级导航和页面切换行为。
"""MainWindow shell and primary navigation helpers."""
from __future__ import annotations
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from src.app.theme import theme_color
from src.features.official_role.page import (
    build_official_role_page,
    refresh_official_role_page,
)
from src.ui.navigation import NAV_ITEMS, nav_index_map, nav_item_by_key

class MainWindowNavigationMixin:
    def _build_ui(self):
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
        tl.addWidget(QLabel("  NTE Drive Calc"))
        tl.addStretch()
        for text, oid, slot in [
            ("—", "", self.showMinimized),
            ("□", "", self._toggle_max),
            ("✕", "btnClose", self.close),
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
            nav_label = ("　  " + item.label) if item.key == "home" else item.label
            button = self._nav(nav_label, item.key)
            setattr(self, item.button_attr, button)
            self._nav_buttons[item.key] = button
            if item.sidebar:
                sl.addWidget(button)
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
        self.topbar_title = QLabel(NAV_ITEMS[0].label)
        tbh.addWidget(self.topbar_title)
        self.topbar_source_label = QLabel("评分标准来源于微信小程序“异环工坊”")
        self.topbar_source_label.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px;margin-left:12px")
        self.topbar_source_label.setWordWrap(False)
        self.topbar_source_label.setVisible(False)
        tbh.addWidget(self.topbar_source_label)
        tbh.addStretch()
        self.account_combo = QComboBox()
        self.account_combo.setFixedWidth(150)
        self.account_combo.currentIndexChanged.connect(self._on_account_combo_changed)
        tbh.addWidget(self.account_combo)
        account_btn = QPushButton("管理账号")
        account_btn.setObjectName("btnAction")
        account_btn.clicked.connect(self._manage_accounts)
        tbh.addWidget(account_btn)
        guide_btn = QPushButton("新手向导")
        guide_btn.setObjectName("btnAction")
        guide_btn.clicked.connect(self.onboarding_guide.show)
        tbh.addWidget(guide_btn)
        self.status_lbl = QLabel("就绪")
        self.status_lbl.setStyleSheet(f"color:{theme_color('#6e7681')};font-size:12px")
        tbh.addWidget(self.status_lbl)
        guide_btn.setText("使用教程")
        rr.addWidget(tbar)
        self.stack = QStackedWidget()
        for item in NAV_ITEMS:
            if item.key == "identify":
                page = self.identification_controller.build_page()
            elif item.key == "execute":
                page = self.scanning_controller.build_page()
            elif item.key == "blueprint":
                page = self.blueprint_page.build()
            elif item.key == "my_role":
                page = build_official_role_page(self)
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
        lh.addWidget(QLabel("运行日志"))
        lh.addStretch()
        cb = QPushButton("清空")
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
        b = QPushButton(text)
        b.setCheckable(True)
        b.clicked.connect(lambda: self._go(page))
        return b

    def _nav_key_for_index(self, index):
        return NAV_ITEMS[index].key if 0 <= index < len(NAV_ITEMS) else NAV_ITEMS[0].key

    def _go(self, page):
        item = nav_item_by_key(page) or NAV_ITEMS[0]
        indexes = nav_index_map()
        if (
            self._nav_key_for_index(self.stack.currentIndex()) == "config"
            and item.key != "config"
            and not self._confirm_leave_config_page()
        ):
            return
        if (
            self._nav_key_for_index(self.stack.currentIndex()) == "my_role"
            and item.key != "my_role"
            and not self._confirm_leave_my_role_page()
        ):
            return
        self.stack.setCurrentIndex(indexes.get(item.key, 0))
        self.topbar_title.setText(item.label)
        if hasattr(self, "topbar_source_label"):
            self.topbar_source_label.setVisible(item.key in {"equipment", "identify", "config"})
        for btn in self._nav_buttons.values():
            btn.setChecked(False)
        selected_key = item.key if item.sidebar else item.parent_key
        button = self._nav_buttons.get(selected_key or "")
        if button is not None:
            button.setChecked(True)
        if item.refresh_method:
            if item.key == "identify":
                self.identification_controller.refresh_options()
            elif item.key == "blueprint":
                self.blueprint_page.refresh()
            elif item.key == "my_role":
                refresh_official_role_page(self)
            else:
                getattr(self, item.refresh_method)()
