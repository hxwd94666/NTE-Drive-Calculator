# PySide6 主窗口入口和功能模块挂载。
"""NTE Drive Calc - PySide6 Desktop Application"""

import sys, os, threading, ctypes, subprocess
from pathlib import Path
from typing import Optional

if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
    APP_DIR = Path(sys.executable).parent
else:
    ROOT = Path(__file__).resolve().parent.parent.parent
    APP_DIR = ROOT
sys.path.insert(0, str(ROOT))

from src.app import runtime
from src.app.constants import (
    ACCOUNT_USER_FILES,
    APP_VERSION,
    BILIBILI_HOME_URL,
    CORE_CONFIG_FILES,
    GITHUB_HOME_URL,
    GITHUB_LATEST_RELEASE_API,
    GITHUB_RELEASES_URL,
    NETDISK_DOWNLOAD_LINKS,
    QUARK_NETDISK_URL,
)
from src.app.theme import (
    apply_app_theme,
    current_style_sheet,
    install_dialog_defaults,
    refresh_inline_theme_styles,
    theme_color,
    theme_preference,
)
from src.services.role_fork_template_service import (
    fork_templates_as_weapon_models,
    load_official_role_fork_templates,
)

BUNDLED_CONFIG_DIR = ROOT / "config"
ASSET_DIR = ROOT / "assets"
APP_ICON_PATH = ASSET_DIR / "app_icon.ico"

def _select_data_root() -> Path:
    candidates = [APP_DIR]
    if getattr(sys, 'frozen', False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "NTE Drive Calc")

    for base in candidates:
        try:
            for subdir in ("config", "scanned_images", "logs"):
                (base / subdir).mkdir(parents=True, exist_ok=True)
            probe = base / "config" / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except Exception:
            continue
    raise RuntimeError("无法创建可写数据目录，请检查安装目录或用户权限。")

DATA_ROOT = _select_data_root()
CONFIG_DIR = DATA_ROOT / "config"
ACCOUNTS_DIR = DATA_ROOT / "accounts"
ACCOUNTS_INDEX_FILE = ACCOUNTS_DIR / "accounts.json"
ACTIVE_ACCOUNT_ID = "default"
ACTIVE_ACCOUNT_NAME = "默认账号"
ACCOUNT_DATA_ROOT = ACCOUNTS_DIR / ACTIVE_ACCOUNT_ID
USER_DATABASE_PATH = ACCOUNT_DATA_ROOT / "user_data.sqlite3"
USER_CONFIG_DIR = ACCOUNT_DATA_ROOT / "config"
TEMPLATE_DIR = CONFIG_DIR / "templates"
SCREENSHOT_DIR = ACCOUNT_DATA_ROOT / "scanned_images"
LOG_DIR = ACCOUNT_DATA_ROOT / "logs"

runtime.configure(
    root=ROOT,
    app_dir=APP_DIR,
    data_root=DATA_ROOT,
    bundled_config_dir=BUNDLED_CONFIG_DIR,
    asset_dir=ASSET_DIR,
    app_icon_path=APP_ICON_PATH,
)

def _apply_account_state(state):
    global ACTIVE_ACCOUNT_ID, ACTIVE_ACCOUNT_NAME, ACCOUNT_DATA_ROOT, USER_DATABASE_PATH, USER_CONFIG_DIR, SCREENSHOT_DIR, LOG_DIR
    ACTIVE_ACCOUNT_ID = state.active_account_id
    ACTIVE_ACCOUNT_NAME = state.active_account_name
    ACCOUNT_DATA_ROOT = state.account_data_root
    USER_DATABASE_PATH = state.user_database_path
    USER_CONFIG_DIR = state.user_config_dir
    SCREENSHOT_DIR = state.screenshot_dir
    LOG_DIR = state.log_dir
    runtime.apply_account_state(state)

def _safe_account_id(name: str) -> str:
    return ACCOUNT_MANAGER.safe_account_id(name)

def _read_accounts_index() -> dict:
    return ACCOUNT_MANAGER.read_index()

def _write_accounts_index(data: dict) -> None:
    ACCOUNT_MANAGER.write_index(data)

def _account_meta(account_id: str | None = None) -> dict:
    return ACCOUNT_MANAGER.account_meta(account_id)

def _account_dir(account_id: str) -> Path:
    return ACCOUNT_MANAGER.account_dir(account_id)

def _seed_user_config():
    ACCOUNT_MANAGER.seed_user_config()

def _seed_account_data(account_id: str, migrate_legacy: bool = False):
    ACCOUNT_MANAGER.seed_account_data(account_id, migrate_legacy=migrate_legacy)

def _set_active_account(account_id: str):
    _apply_account_state(ACCOUNT_MANAGER.activate(account_id))

def _initialize_accounts():
    _apply_account_state(ACCOUNT_MANAGER.initialize())

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame,
    QTextEdit, QMessageBox, QComboBox, QFileDialog, QInputDialog,
    QSizeGrip,
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QSize
from PySide6.QtGui import QColor, QTextCursor, QIcon

from src.features.scanning.file_lifecycle import (
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    iter_image_files as _iter_image_files,
    managed_screenshot_usage,
)
from src.optimizer.scoring import ScoringEngine
from src.domain.stat_catalog import StatCatalog
from src.utils.logger import disable_session_log, enable_session_log, logger, set_log_dir
from src.ui.navigation import NAV_ITEMS, nav_index_map, nav_item_by_key
from src.features.accounts.manager import AccountManager, populate_account_combo, show_account_manager_dialog
from src.features.official_role.page import confirm_pending_my_role_changes
from src.features.configuration.page import (
    add_weight as config_add_weight,
    build_config_page,
    confirm_pending_config_changes as config_confirm_pending_config_changes,
    del_weight as config_del_weight,
    refresh_config_forms as config_refresh_config_forms,
    render_roles_form,
    reset_config_form as config_reset_config_form,
    save_config_data as config_save_config_data,
    save_config_form as config_save_config_form,
    save_role_weight_value as config_save_role_weight_value,
    switch_config_form as config_switch_config_form,
)
from src.features.settings.page import build_settings_page
from src.features.home.page import (
    build_home_page,
    inventory_sync_error_guidance,
    refresh_home_page,
)
from src.features.settings.updates import (
    fetch_update_info,
    is_newer_version,
    should_show_startup_update,
    show_update_dialog,
)
from src.app.workers import WorkerThread
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    deploy_plugin,
    find_game_executables,
    npcap_installation_present,
    packaged_plugin_dll,
    restore_plugin,
)
from src.services.dashboard_service import DashboardService
from src.services.account_settings_service import AccountSettingsService
from src.services.inventory_sync_service import InventorySyncService, InventorySyncState
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.ui.main_window_mixins import FeatureMainWindowMixin
from src.ui.controllers import (
    environment_controller,
    configuration_controller,
    hotkey_controller,
    inventory_sync_controller,
    update_controller,
)

ACCOUNT_MANAGER = AccountManager(
    DATA_ROOT,
    BUNDLED_CONFIG_DIR,
    _iter_image_files,
    CORE_CONFIG_FILES,
    ACCOUNT_USER_FILES,
)

_initialize_accounts()

def _is_admin():
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _restart_as_admin():
    if sys.platform != "win32":
        return False
    try:
        args = sys.argv[1:] if getattr(sys, 'frozen', False) else sys.argv
        params = " ".join(f'"{a}"' for a in args)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return result > 32
    except Exception:
        return False

def _ensure_admin():
    if _is_admin():
        return
    if _restart_as_admin():
        sys.exit(0)
    raise RuntimeError("需要管理员权限启动，请右键程序选择“以管理员身份运行”。")

# ── Log Sink
class QtLogSink:
    def __init__(self,signal): self.signal=signal
    def write(self,m):
        msg=m.strip()
        if msg: self.signal.emit(msg)
    def flush(self): pass

# ── Main Window
class MainWindow(FeatureMainWindowMixin, QMainWindow):
    log_signal=Signal(str); identify_capture_signal=Signal(str); identify_capture_done_signal=Signal(); inventory_sync_state_signal=Signal(object); W,H=1260,860

    def __init__(self):
        super().__init__(); self.setWindowTitle("NTE Drive Calc")
        screen_geo=QApplication.primaryScreen().availableGeometry()
        initial_w=min(self.W,max(640,screen_geo.width()-80))
        initial_h=min(self.H,max(480,screen_geo.height()-80))
        min_w=min(1000,max(640,screen_geo.width()-120))
        min_h=min(700,max(480,screen_geo.height()-120))
        self.resize(initial_w,initial_h); self.setMinimumSize(min_w,min_h)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setWindowFlags(Qt.FramelessWindowHint); self.setAttribute(Qt.WA_TranslucentBackground,False)
        self._drag_pos:Optional[QPoint]=None; self._resize_margin=8
        self.move(screen_geo.x()+(screen_geo.width()-initial_w)//2,screen_geo.y()+(screen_geo.height()-initial_h)//2)
        self.roles_db:dict={}; self.sets_db:dict={}; self.all_set_names:list[str]=[]; self.tape_main_stats:list[str]=[]; self.stats_config:dict={}
        self.equipped_state:dict={}; self.final_plan=None; self._allocation_dirty=False; self._shape_areas:dict={}
        self.scoring_engine=None
        self._pending_archive_paths=[]
        self._pending_parse_only=False
        self._pending_parse_scope="all"
        self._pending_scan_mode=None
        self._pending_delete_after_parse=[]
        self._identify_blueprint_cache=None
        self._inventory_sync_service=None
        self._log_enabled=False
        set_log_dir(LOG_DIR)

        # Hotkey config
        self._hk_capture="F9"; self._hk_finish="F10"; self._hk_stop="F12"
        self._account_settings=AccountSettingsService(
            USER_DATABASE_PATH, legacy_config_dir=USER_CONFIG_DIR
        )
        self._account_settings.migrate_legacy_settings()
        self._load_hotkey_config()
        self._update_config=self._load_update_config()
        self._ui_preferences=self._load_ui_preferences()
        self._apply_theme_preference()
        self._update_check_manual=True

        self.log_signal.connect(self._on_log); self.identify_capture_signal.connect(self._add_identify_capture_path); self.identify_capture_done_signal.connect(self._finish_identify_capture_mode); self.inventory_sync_state_signal.connect(self._on_inventory_sync_state); self._log_sink=QtLogSink(self.log_signal)
        try:
            from loguru import logger as lu
            lu.add(self._log_sink,format="{time:HH:mm:ss} | {level: <8} | {message}",level="INFO",colorize=False)
        except Exception as exc:
            logger.debug(f"注册界面日志输出失败，仅写入文件日志: {exc}")
        self._build_ui()
        if self._ui_preferences["log_enabled"]:
            self._toggle_log(True)
        self._load_data(); self._refresh_home(); self._maybe_auto_start_inventory_sync(); self._on_log("系统就绪"); self._maybe_show_quick_start(); self._maybe_check_updates_on_startup()

    def _load_update_config(self):
        return self._account_settings.load("update")

    def _save_update_config(self):
        self._update_config=self._account_settings.save("update",self._update_config)

    def _load_ui_preferences(self):
        return self._account_settings.load("ui")

    def _save_ui_preferences(self):
        self._ui_preferences=self._account_settings.save(
            "ui",self._ui_preferences
        )

    def _current_style_sheet(self):
        return current_style_sheet(QApplication.instance())

    def _apply_theme_preference(self):
        theme=(self._ui_preferences or {}).get("theme","dark")
        apply_app_theme(QApplication.instance(), theme)
        refresh_inline_theme_styles(self, QApplication.instance())
        if hasattr(self,"topbar_source_label"):
            self.topbar_source_label.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px;margin-left:12px")
        if hasattr(self,"status_lbl"):
            self.status_lbl.setStyleSheet(f"color:{theme_color('#6e7681')};font-size:12px")

    def _set_theme_preference(self, theme):
        normalized=theme_preference(theme)
        if (self._ui_preferences or {}).get("theme","dark")==normalized:
            return True
        if not self._prompt_restart_for_theme_change():
            return False
        previous=(self._ui_preferences or {}).get("theme","dark")
        self._ui_preferences["theme"]=normalized
        self._save_ui_preferences()
        if not self._restart_application_as_admin():
            self._ui_preferences["theme"]=previous
            self._save_ui_preferences()
            return False
        return True

    def _prompt_restart_for_theme_change(self):
        box=QMessageBox(self)
        box.setWindowTitle("重启生效")
        box.setText("切换主题需要重启应用，是否现在重启并应用？")
        ok_button=box.addButton("好的", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(ok_button)
        box.exec()
        return box.clickedButton() is ok_button

    def _restart_application_as_admin(self):
        QApplication.processEvents()
        program=sys.executable
        args=sys.argv[:]
        parameters=""
        if not getattr(sys, "frozen", False):
            parameters=subprocess.list2cmdline(args)
        elif len(args)>1:
            parameters=subprocess.list2cmdline(args[1:])
        if sys.platform=="win32":
            result=ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                program,
                parameters,
                str(APP_DIR),
                1,
            )
            if result <= 32:
                QMessageBox.warning(self, "重启失败", "未能以管理员方式重启应用，主题设置已取消。")
                return False
            QApplication.quit()
            return True
        QMessageBox.warning(self, "重启失败", "当前系统不支持自动管理员重启，主题设置已取消。")
        return False

    # ── Frameless
    def _on_edge(self,pos): w,h=self.width(),self.height(); m=self._resize_margin; return (pos.x()<m,pos.y()<m,pos.x()>w-m,pos.y()>h-m)
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton: self._drag_pos=e.globalPosition().toPoint(); self._drag_edges=self._on_edge(e.position().toPoint())
        super().mousePressEvent(e)
    def mouseMoveEvent(self,e):
        if self._drag_pos and any(self._drag_edges):
            d=e.globalPosition().toPoint()-self._drag_pos; g=self.geometry(); L,T,R,B=self._drag_edges
            if L: g.setLeft(g.left()+d.x())
            if T: g.setTop(g.top()+d.y())
            if R: g.setRight(g.right()+d.x())
            if B: g.setBottom(g.bottom()+d.y())
            self.setGeometry(g.normalized() if g.width()>=self.minimumWidth() and g.height()>=self.minimumHeight() else self.geometry())
            self._drag_pos=e.globalPosition().toPoint()
        elif not any(self._drag_edges):
            pos=e.position().toPoint(); E=self._on_edge(pos)
            if E[0] and E[1]: self.setCursor(Qt.SizeFDiagCursor)
            elif E[2] and E[3]: self.setCursor(Qt.SizeFDiagCursor)
            elif E[0] and E[3]: self.setCursor(Qt.SizeBDiagCursor)
            elif E[1] and E[2]: self.setCursor(Qt.SizeBDiagCursor)
            elif E[0] or E[2]: self.setCursor(Qt.SizeHorCursor)
            elif E[1] or E[3]: self.setCursor(Qt.SizeVerCursor)
            else: self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self,e): self._drag_pos=None; self._drag_edges=(False,)*4; super().mouseReleaseEvent(e)
    def closeEvent(self,e):
        if getattr(self,"_config_dirty",False) and not self._confirm_leave_config_page():
            e.ignore()
            return
        if getattr(self,"_my_role_dirty",False) and not self._confirm_leave_my_role_page():
            e.ignore()
            return
        if hasattr(self,"role_selector"):
            try:
                self.role_selector.save_temporary_priority_config()
            except Exception as exc:
                logger.warning(f"保存临时优先级失败: {exc}")
        try:
            self._stop_inventory_sync()
        except Exception as exc:
            logger.warning(f"停止背包同步失败: {exc}")
        if self._log_enabled:
            logger.info("运行日志已随程序退出而停止")
            disable_session_log()
            self._log_enabled=False
        super().closeEvent(e)
    def _tb_press(self,e):
        if e.button()==Qt.LeftButton: self._drag_pos=e.globalPosition().toPoint()
    def _tb_move(self,e):
        if self._drag_pos and e.buttons()==Qt.LeftButton:
            self.move(self.pos()+e.globalPosition().toPoint()-self._drag_pos); self._drag_pos=e.globalPosition().toPoint()
    def _tb_dbl(self,e): self._toggle_max()
    def _toggle_max(self): self.showNormal() if self.isMaximized() else self.showMaximized()

    # ── Build
    def _build_ui(self):
        outer=QWidget(); self.setCentralWidget(outer)
        root=QVBoxLayout(outer); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        tb=QWidget(); tb.setObjectName("titleBar"); tb.setFixedHeight(38)
        tb.mousePressEvent=self._tb_press; tb.mouseMoveEvent=self._tb_move; tb.mouseDoubleClickEvent=self._tb_dbl
        tl=QHBoxLayout(tb); tl.setContentsMargins(14,0,4,0); tl.setSpacing(0)
        tl.addWidget(QLabel("  NTE Drive Calc")); tl.addStretch()
        for text,oid,slot in [("—","",self.showMinimized),("□","",self._toggle_max),("✕","btnClose",self.close)]:
            b=QPushButton(text); b.setObjectName(oid); b.setFixedSize(36,28); b.clicked.connect(slot); tl.addWidget(b)
        root.addWidget(tb)

        body=QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        sidebar=QWidget(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(200)
        sl=QVBoxLayout(sidebar); sl.setContentsMargins(0,12,0,0); sl.setSpacing(0)
        self._nav_buttons={}
        for item in NAV_ITEMS:
            # Keep the workbench icon-free but reserve the same leading visual
            # column as emoji-prefixed navigation labels.  The page title
            # still uses the unpadded metadata label.
            nav_label=("　  " + item.label) if item.key == "home" else item.label
            button=self._nav(nav_label,item.key)
            setattr(self,item.button_attr,button)
            self._nav_buttons[item.key]=button
            sl.addWidget(button)
        sl.addStretch(); body.addWidget(sidebar)

        right=QWidget(); rr=QVBoxLayout(right); rr.setContentsMargins(0,0,0,0); rr.setSpacing(0)
        tbar=QWidget(); tbar.setObjectName("topbar"); tbh=QHBoxLayout(tbar); tbh.setContentsMargins(20,10,20,10)
        self.topbar_title=QLabel(NAV_ITEMS[0].label); tbh.addWidget(self.topbar_title)
        self.topbar_source_label=QLabel("评分标准来源于微信小程序“异环工坊”")
        self.topbar_source_label.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px;margin-left:12px")
        self.topbar_source_label.setWordWrap(False)
        self.topbar_source_label.setVisible(False)
        tbh.addWidget(self.topbar_source_label)
        tbh.addStretch()
        self.account_combo=QComboBox(); self.account_combo.setFixedWidth(150)
        self.account_combo.currentIndexChanged.connect(self._on_account_combo_changed)
        tbh.addWidget(self.account_combo)
        account_btn=QPushButton("管理账号"); account_btn.setObjectName("btnAction"); account_btn.clicked.connect(self._manage_accounts); tbh.addWidget(account_btn)
        guide_btn=QPushButton("新手向导"); guide_btn.setObjectName("btnAction"); guide_btn.clicked.connect(self._show_quick_start); tbh.addWidget(guide_btn)
        self.status_lbl=QLabel("就绪"); self.status_lbl.setStyleSheet(f"color:{theme_color('#6e7681')};font-size:12px"); tbh.addWidget(self.status_lbl)
        guide_btn.setText("使用教程")
        rr.addWidget(tbar)
        self.stack=QStackedWidget()
        for item in NAV_ITEMS:
            self.stack.addWidget(getattr(self,item.page_builder)())
        rr.addWidget(self.stack,1)

        self.log_frame=QWidget(); self.log_frame.setObjectName("logPanel"); self.log_frame.setVisible(False)
        lf=QVBoxLayout(self.log_frame); lf.setContentsMargins(0,0,0,0)
        lh=QHBoxLayout(); lh.setContentsMargins(16,6,16,6); lh.addWidget(QLabel("运行日志")); lh.addStretch()
        cb=QPushButton("清空"); cb.setObjectName("btnSm"); cb.clicked.connect(self._clear_log); lh.addWidget(cb); lf.addLayout(lh)
        self.log_view=QTextEdit(); self.log_view.setReadOnly(True); self.log_view.setMaximumHeight(140); lf.addWidget(self.log_view)
        rr.addWidget(self.log_frame)
        body.addWidget(right,1); root.addLayout(body)
        QSizeGrip(self).setStyleSheet("background:transparent"); self._nav_buttons[NAV_ITEMS[0].key].setChecked(True)
        self._refresh_account_combo()

    def _nav(self,text,page): b=QPushButton(text); b.setCheckable(True); b.clicked.connect(lambda: self._go(page)); return b
    def _nav_key_for_index(self,index):
        return NAV_ITEMS[index].key if 0<=index<len(NAV_ITEMS) else NAV_ITEMS[0].key

    def _go(self,page):
        item=nav_item_by_key(page) or NAV_ITEMS[0]
        indexes=nav_index_map()
        if self._nav_key_for_index(self.stack.currentIndex())=="config" and item.key!="config" and not self._confirm_leave_config_page():
            return
        if self._nav_key_for_index(self.stack.currentIndex())=="my_role" and item.key!="my_role" and not self._confirm_leave_my_role_page():
            return
        self.stack.setCurrentIndex(indexes.get(item.key,0))
        self.topbar_title.setText(item.label)
        if hasattr(self,"topbar_source_label"):
            self.topbar_source_label.setVisible(item.key in {"equipment","identify","config"})
        for btn in self._nav_buttons.values(): btn.setChecked(False)
        self._nav_buttons[item.key].setChecked(True)
        if item.refresh_method:
            getattr(self,item.refresh_method)()

    def _refresh_account_combo(self):
        if not hasattr(self,"account_combo"):
            return
        self.account_combo.blockSignals(True)
        populate_account_combo(self.account_combo,ACCOUNT_MANAGER.read_index(),ACTIVE_ACCOUNT_ID)
        self.account_combo.blockSignals(False)

    def _on_account_combo_changed(self,index):
        if index<0:
            return
        account_id=self.account_combo.itemData(index)
        if account_id and account_id!=ACTIVE_ACCOUNT_ID:
            if not self._switch_account(account_id):
                self._refresh_account_combo()

    def _switch_account(self,account_id):
        if getattr(self,"_my_role_dirty",False) and not self._confirm_leave_my_role_page():
            return False
        if getattr(self,"_config_dirty",False) and not self._confirm_leave_config_page():
            return False
        data=ACCOUNT_MANAGER.read_index()
        if not any(a.get("id")==account_id for a in data.get("accounts",[])):
            return False
        sync_was_running=bool(self._inventory_sync_service and self._inventory_sync_service.is_running)
        # A freshly-created account has no UI preference copy yet.  Seed its
        # theme from the current account so the running application never
        # switches to an incompatible default theme mid-session.
        current_theme=(self._ui_preferences or {}).get("theme", "dark")
        if sync_was_running:
            self._stop_inventory_sync()
        ACCOUNT_MANAGER.set_active_account_id(account_id)
        _set_active_account(account_id)
        set_log_dir(LOG_DIR)
        self._account_settings=AccountSettingsService(
            USER_DATABASE_PATH, legacy_config_dir=USER_CONFIG_DIR
        )
        self._account_settings.migrate_legacy_settings()
        self._load_hotkey_config()
        self._update_config=self._load_update_config()
        self._ui_preferences=self._load_ui_preferences()
        with UserDataDao(USER_DATABASE_PATH) as user_dao:
            has_ui_preference_copy = "ui" in user_dao.list_application_setting_copies()
        if not has_ui_preference_copy:
            self._ui_preferences["theme"] = current_theme
            self._save_ui_preferences()
        self._apply_theme_preference()
        self._toggle_log(bool(self._ui_preferences["log_enabled"]))
        if hasattr(self,"_identify_capture_dir"):
            self._identify_capture_dir=ACCOUNT_DATA_ROOT/"identify_captures"
        self.final_plan=None
        self._pending_archive_paths=[]
        self.result_card.setVisible(False)
        self._load_data()
        if hasattr(self,"my_role_form_layout"):
            self._refresh_my_role()
        if hasattr(self, "weighted_role_selector"):
            self._refresh_weighted_allocation()
        self._refresh_account_combo()
        if hasattr(self,"_ss_info"):
            self._refresh_ss()
        self._refresh_home()
        if sync_was_running:
            self._start_inventory_sync()
        else:
            self._maybe_auto_start_inventory_sync()
        logger.info(f"已切换账号: {ACTIVE_ACCOUNT_NAME}")
        return True

    def _manage_accounts(self):
        show_account_manager_dialog(
            self,
            self._current_style_sheet(),
            ACCOUNT_MANAGER,
            ACTIVE_ACCOUNT_ID,
            self._switch_account,
            self._refresh_account_combo,
        )

    # ── Log

    def _on_log(self,msg):
        if not self._log_enabled: return
        c=theme_color("#8b949e")
        if any(k in msg for k in ("ERROR","error","失败","崩溃")): c="#f85149"
        elif any(k in msg for k in ("WARNING","warning","警告")): c="#d2991d"
        elif any(k in msg for k in ("SUCCESS","完成","完毕")): c="#3fb950"
        self.log_view.moveCursor(QTextCursor.End); self.log_view.setTextColor(QColor(c)); self.log_view.insertPlainText(msg+"\n"); self.log_view.moveCursor(QTextCursor.End)
    def _clear_log(self): self.log_view.clear()
    def _toggle_log(self,enabled):
        toggle=getattr(self,"_log_toggle",None)
        if toggle is not None and toggle.isChecked()!=bool(enabled):
            toggle.blockSignals(True)
            toggle.setChecked(bool(enabled))
            toggle.blockSignals(False)
        if enabled:
            try:
                log_path=enable_session_log()
            except Exception as exc:
                logger.error(f"创建运行日志文件失败: {exc}")
                if toggle is not None:
                    toggle.blockSignals(True)
                    toggle.setChecked(False)
                    toggle.blockSignals(False)
                self._ui_preferences["log_enabled"]=False
                self._save_ui_preferences()
                QMessageBox.warning(self,"运行日志","无法创建运行日志文件，请检查日志目录是否可写")
                return
            self._log_enabled=True
            self._ui_preferences["log_enabled"]=True
            self._save_ui_preferences()
            self.log_frame.setVisible(True)
            logger.info(f"运行日志已开启: {log_path.name}")
            return
        if self._log_enabled:
            logger.info("运行日志已关闭")
        disable_session_log()
        self._log_enabled=False
        self._ui_preferences["log_enabled"]=False
        self._save_ui_preferences()
        self.log_frame.setVisible(False)
        self.log_view.clear()
        self.log_view.insertPlainText("(日志已关闭)\n")

    # ── Data
    def _load_data(self, reload_priority=True):
        try:
            catalog=StatCatalog.from_config_dir(CONFIG_DIR)
            self.stats_config={
                "gold_base_values": catalog.gold_base_values,
                "tape_main_stats_pool": catalog.tape_main_stats,
                "tape_main_stat_values": catalog.tape_main_values,
                "tape_stat_values": catalog.tape_stat_values,
                "main_only_keywords": catalog.main_only_keywords,
                "stat_alias_mapping": catalog.stat_alias_mapping,
                "benefit_one": catalog.benefit_one,
                "benefit_alias_mapping": catalog.benefit_alias_mapping,
                "weight_pool": catalog.weight_pool,
            }
            self.tape_main_stats=catalog.tape_main_stats
            self.drive_sub_stats=list(catalog.gold_base_values.keys())
            self.weapons_db=fork_templates_as_weapon_models(
                load_official_role_fork_templates()
            )
            static_catalog = build_legacy_allocation_static_catalog(
                config_dir=CONFIG_DIR, user_database_path=USER_DATABASE_PATH,
            )
            self.roles_db=static_catalog.roles_db
            self.sets_db=static_catalog.sets_db
            self.all_set_names=list(self.sets_db)
            self._shape_areas={
                shape_id: int(shape.area)
                for shape_id, shape in static_catalog.shapes_db.items()
            }
            self.equipped_state={}
            self.scoring_engine=ScoringEngine(
                str(CONFIG_DIR), user_database_path=USER_DATABASE_PATH,
                roles_db=self.roles_db,
            )
            logger.info(f"已从 SQLite 加载 {len(self.roles_db)} 角色，{len(self.sets_db)} 套装")
            self._update_inventory_status()
            self.role_selector.load_roles(self.roles_db,self.all_set_names,self.tape_main_stats,self.drive_sub_stats,weapons_db=self.weapons_db)
            if reload_priority:
                self.role_selector.load_startup_priority_config()
            self._identify_blueprint_cache=None
            if hasattr(self,"ident_shape_combo"):
                self._refresh_identify_options()
        except Exception as e: logger.error(f"加载失败: {e}")

    def _update_inventory_status(self):
        try:
            if USER_DATABASE_PATH.is_file():
                with UserDataDao(USER_DATABASE_PATH) as dao:
                    summary=dao.current_inventory_summary()
                if summary is not None:
                    count=int(summary["stored_item_count"])
                    self.status_lbl.setText(f"稳定背包 {count} 件")
                    self.status_lbl.setStyleSheet("color:#3fb950;font-size:12px")
                    return
        except Exception as exc:
            logger.debug(f"读取 SQLite 背包状态失败: {exc}")
        self.status_lbl.setText("库存为空")
        self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")
    def _card(self,title):
        c=QFrame(); c.setObjectName("card")
        l=QVBoxLayout(c); l.setContentsMargins(20,16,20,16); l.setSpacing(8)
        lb=QLabel(title); lb.setObjectName("cardTitle"); l.addWidget(lb); return c

    # ── Page: Home / 2.0 Dashboard
    def _page_home(self):
        return build_home_page(self)

    def _refresh_home(self):
        if not hasattr(self,"home_account_label"):
            return
        try:
            dashboard=DashboardService(USER_DATABASE_PATH).load()
            refresh_home_page(self,dashboard)
        except Exception as exc:
            self.home_account_label.setText(f"工作台数据暂时不可用：{exc}")
            logger.warning(f"刷新 2.0 工作台失败: {exc}")

    def _settings_paths(self):
        return {
            "config_dir": CONFIG_DIR,
            "accounts_dir": ACCOUNTS_DIR,
            "log_dir": LOG_DIR,
            "screenshot_dir": SCREENSHOT_DIR,
        }

    def _page_settings(self):
        return build_settings_page(self,APP_VERSION,self._settings_paths,_iter_image_files,NETDISK_DOWNLOAD_LINKS)

    def _refresh_ss(self):
        usage=managed_screenshot_usage(SCREENSHOT_DIR, ACCOUNT_DATA_ROOT)
        self._ss_info.setText(f"当前截图: {usage.count} 个 · {usage.size_mb:.1f} MB")
    def _clear_ss(self):
        plan=build_screenshot_cleanup_plan(SCREENSHOT_DIR, ACCOUNT_DATA_ROOT)
        if plan.total_count==0: QMessageBox.information(self,"清理","没有需要清理的文件。"); return
        if QMessageBox.question(self,"确认清理",plan.confirmation_text(),QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            result=execute_screenshot_cleanup(plan)
            self._refresh_ss(); logger.success(f"已清理 {result.deleted} 个截图")
            if result.failed_files:
                QMessageBox.warning(self, "清理完成", f"有 {len(result.failed_files)} 个文件删除失败，可能正在被占用。")
            if plan.baseline_missing:
                QMessageBox.warning(self,"清理完成","注意：丢失用于对比的截图，请重新全量扫描，或不要使用全自动增量扫描。")

# ── Facade

# ── Entry
environment_controller.install_methods(sys.modules[__name__], MainWindow)
configuration_controller.install_methods(sys.modules[__name__], MainWindow)
hotkey_controller.install_methods(sys.modules[__name__], MainWindow)
inventory_sync_controller.install_methods(sys.modules[__name__], MainWindow)
update_controller.install_methods(sys.modules[__name__], MainWindow)


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """全局异常处理，防止未捕获异常导致闪退"""
    import traceback as tb
    error_msg = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"未捕获异常:\n{error_msg}")
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "程序异常", f"发生未捕获的异常:\n\n{error_msg[:1000]}")
    except Exception as exc:
        logger.error(f"显示全局异常弹窗失败: {exc}")

def run_gui():
    import faulthandler
    _ensure_admin()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fault_log = open(str(LOG_DIR / "crash_dump.log"), "w", encoding="utf-8")
    faulthandler.enable(file=_fault_log)

    sys.excepthook = _global_exception_handler
    threading.excepthook = lambda args: logger.error(f"线程异常 [{args.thread}]: {args.exc_type.__name__}: {args.exc_value}")
    if hasattr(Qt, "AA_DontUseNativeDialogs"):
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app=QApplication(sys.argv); app.setStyle("Fusion"); apply_app_theme(app,"dark")
    install_dialog_defaults(app)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": run_gui()
