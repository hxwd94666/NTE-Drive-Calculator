# PySide6 主窗口入口和功能模块挂载。
"""NTE Drive Calc - PySide6 Desktop Application"""

import sys, os, threading, ctypes
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
    CORE_CONFIG_FILES,
    GITHUB_HOME_URL,
    GITHUB_LATEST_RELEASE_API,
    GITHUB_RELEASES_URL,
    QUARK_NETDISK_URL,
)
from src.app.theme import STYLE, apply_dark_palette

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
USER_CONFIG_DIR = ACCOUNT_DATA_ROOT / "config"
TEMPLATE_DIR = CONFIG_DIR / "templates"
OUTPUT_FILE = USER_CONFIG_DIR / "real_inventory.json"
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
    global ACTIVE_ACCOUNT_ID, ACTIVE_ACCOUNT_NAME, ACCOUNT_DATA_ROOT, USER_CONFIG_DIR, OUTPUT_FILE, SCREENSHOT_DIR, LOG_DIR
    ACTIVE_ACCOUNT_ID = state.active_account_id
    ACTIVE_ACCOUNT_NAME = state.active_account_name
    ACCOUNT_DATA_ROOT = state.account_data_root
    USER_CONFIG_DIR = state.user_config_dir
    OUTPUT_FILE = state.output_file
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
    QTextEdit, QMessageBox, QComboBox,
    QSizeGrip,
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer
from PySide6.QtGui import QColor, QTextCursor, QIcon

from src.features.scanning.file_lifecycle import (
    iter_image_files as _iter_image_files,
)
from src.optimizer.state_manager import StateManager
from src.optimizer.scoring import ScoringEngine
from src.domain.stat_catalog import StatCatalog
from src.storage.json_store import read_json, write_json
from src.utils.logger import logger, set_log_dir
from src.utils.name_resolver import resolve_name
from src.ui.navigation import NAV_ITEMS, nav_index_map, nav_item_by_key
from src.features.accounts.manager import AccountManager, populate_account_combo, show_account_manager_dialog
from src.features.settings.hotkeys import load_hotkey_config, save_hotkey_config
from src.features.configuration.page import (
    add_role as config_add_role,
    add_set as config_add_set,
    add_weight as config_add_weight,
    build_config_page,
    confirm_pending_config_changes as config_confirm_pending_config_changes,
    config_add_item as config_add_config_item,
    del_role as config_del_role,
    del_set as config_del_set,
    del_weight as config_del_weight,
    refresh_config_forms as config_refresh_config_forms,
    render_roles_form,
    render_sets_form,
    save_config_data as config_save_config_data,
    save_config_form as config_save_config_form,
    save_role_board_cell as config_save_role_board_cell,
    save_role_field as config_save_role_field,
    save_role_weight_value as config_save_role_weight_value,
    save_set_shapes as config_save_set_shapes,
    save_single_extra_shape_buff as config_save_single_extra_shape_buff,
    stat_choice_pool as config_stat_choice_pool,
    switch_config_form as config_switch_config_form,
)
from src.features.settings.page import build_settings_page
from src.features.settings.updates import (
    fetch_update_info,
    is_newer_version,
    load_update_config,
    save_update_config,
    should_show_startup_update,
    show_update_dialog,
)
from src.app.workers import WorkerThread

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
class MainWindow(QMainWindow):
    log_signal=Signal(str); identify_capture_signal=Signal(str); identify_capture_done_signal=Signal(); W,H=1260,860

    def __init__(self):
        super().__init__(); self.setWindowTitle("NTE Drive Calc"); self.resize(self.W,self.H); self.setMinimumSize(1000,700)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setWindowFlags(Qt.FramelessWindowHint); self.setAttribute(Qt.WA_TranslucentBackground,False)
        self._drag_pos:Optional[QPoint]=None; self._resize_margin=8
        screen=QApplication.primaryScreen().geometry(); self.move((screen.width()-self.W)//2,(screen.height()-self.H)//2)
        self.roles_db:dict={}; self.sets_db:dict={}; self.all_set_names:list[str]=[]; self.tape_main_stats:list[str]=[]; self.stats_config:dict={}
        self.equipped_state:dict={}; self.final_plan=None; self._allocation_dirty=False; self._shape_areas:dict={}
        self.scoring_engine=None
        self._pending_archive_paths=[]
        self._pending_parse_only=False
        self._pending_parse_scope="all"
        self._pending_scan_mode=None
        self._pending_delete_after_parse=[]
        self._identify_blueprint_cache=None
        self.state_mgr=StateManager(config_dir=str(USER_CONFIG_DIR)); self._log_enabled=False
        set_log_dir(LOG_DIR)

        # Hotkey config
        self._hk_capture="F9"; self._hk_finish="F10"; self._hk_stop="F12"
        self._load_hotkey_config()
        self._update_config=self._load_update_config()
        self._ui_preferences=self._load_ui_preferences()
        self._update_check_manual=True

        self.log_signal.connect(self._on_log); self.identify_capture_signal.connect(self._add_identify_capture_path); self.identify_capture_done_signal.connect(self._finish_identify_capture_mode); self._log_sink=QtLogSink(self.log_signal)
        try:
            from loguru import logger as lu
            lu.add(self._log_sink,format="{time:HH:mm:ss} | {level: <8} | {message}",level="INFO",colorize=False)
        except: pass
        self._build_ui(); self._load_data(); self._on_log("系统就绪"); self._maybe_show_quick_start(); self._maybe_check_updates_on_startup()

    def _load_hotkey_config(self):
        hotkeys=load_hotkey_config(USER_CONFIG_DIR)
        self._hk_capture=hotkeys["capture"]; self._hk_finish=hotkeys["finish"]; self._hk_stop=hotkeys["stop"]
    def _save_hotkey_config(self):
        save_hotkey_config(USER_CONFIG_DIR,self._hk_capture,self._hk_finish,self._hk_stop)

    def _load_update_config(self):
        return load_update_config(USER_CONFIG_DIR)

    def _save_update_config(self):
        save_update_config(USER_CONFIG_DIR,self._update_config)

    def _load_ui_preferences(self):
        path=USER_CONFIG_DIR/"ui_preferences.json"
        default={"skip_unsaved_allocation_prompt":False}
        try:
            data=read_json(path, default={}) or {}
            if isinstance(data,dict):
                default["skip_unsaved_allocation_prompt"]=bool(data.get("skip_unsaved_allocation_prompt",False))
        except Exception:
            pass
        return default

    def _save_ui_preferences(self):
        write_json(USER_CONFIG_DIR/"ui_preferences.json", self._ui_preferences)

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
        if hasattr(self,"role_selector"):
            try:
                self.role_selector.save_temporary_priority_config()
            except Exception as exc:
                logger.warning(f"保存临时优先级失败: {exc}")
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
            button=self._nav(item.label,item.key)
            setattr(self,item.button_attr,button)
            self._nav_buttons[item.key]=button
            sl.addWidget(button)
        sl.addStretch(); body.addWidget(sidebar)

        right=QWidget(); rr=QVBoxLayout(right); rr.setContentsMargins(0,0,0,0); rr.setSpacing(0)
        tbar=QWidget(); tbar.setObjectName("topbar"); tbh=QHBoxLayout(tbar); tbh.setContentsMargins(20,10,20,10)
        self.topbar_title=QLabel(NAV_ITEMS[0].label); tbh.addWidget(self.topbar_title); tbh.addStretch()
        self.account_combo=QComboBox(); self.account_combo.setFixedWidth(150)
        self.account_combo.currentIndexChanged.connect(self._on_account_combo_changed)
        tbh.addWidget(self.account_combo)
        account_btn=QPushButton("管理账号"); account_btn.setObjectName("btnAction"); account_btn.clicked.connect(self._manage_accounts); tbh.addWidget(account_btn)
        guide_btn=QPushButton("新手向导"); guide_btn.setObjectName("btnAction"); guide_btn.clicked.connect(self._show_quick_start); tbh.addWidget(guide_btn)
        self.status_lbl=QLabel("就绪"); self.status_lbl.setStyleSheet("color:#6e7681;font-size:12px"); tbh.addWidget(self.status_lbl)
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
        self.stack.setCurrentIndex(indexes.get(item.key,0))
        self.topbar_title.setText(item.label)
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
            self._switch_account(account_id)

    def _switch_account(self,account_id):
        data=ACCOUNT_MANAGER.read_index()
        if not any(a.get("id")==account_id for a in data.get("accounts",[])):
            return
        ACCOUNT_MANAGER.set_active_account_id(account_id)
        _set_active_account(account_id)
        set_log_dir(LOG_DIR)
        self.state_mgr=StateManager(config_dir=str(USER_CONFIG_DIR))
        self._load_hotkey_config()
        self._update_config=self._load_update_config()
        if hasattr(self,"_identify_capture_dir"):
            self._identify_capture_dir=ACCOUNT_DATA_ROOT/"identify_captures"
        self.final_plan=None
        self._pending_archive_paths=[]
        self.result_card.setVisible(False)
        self._load_data()
        self._refresh_account_combo()
        if hasattr(self,"_ss_info"):
            self._refresh_ss()
        logger.info(f"已切换账号: {ACTIVE_ACCOUNT_NAME}")

    def _manage_accounts(self):
        show_account_manager_dialog(
            self,
            STYLE,
            ACCOUNT_MANAGER,
            ACTIVE_ACCOUNT_ID,
            self._switch_account,
            self._refresh_account_combo,
        )

    # ── Log

    def _on_log(self,msg):
        if not self._log_enabled: return
        c="#8b949e"
        if any(k in msg for k in ("ERROR","error","失败","崩溃")): c="#f85149"
        elif any(k in msg for k in ("WARNING","warning","警告")): c="#d2991d"
        elif any(k in msg for k in ("SUCCESS","完成","完毕")): c="#3fb950"
        self.log_view.moveCursor(QTextCursor.End); self.log_view.setTextColor(QColor(c)); self.log_view.insertPlainText(msg+"\n"); self.log_view.moveCursor(QTextCursor.End)
    def _clear_log(self): self.log_view.clear()
    def _toggle_log(self,enabled):
        self._log_enabled=enabled; self.log_frame.setVisible(enabled)
        if enabled: self._on_log("运行日志已开启")
        else: self.log_view.clear(); self.log_view.insertPlainText("(日志已关闭)\n")

    # ── Data
    def _load_data(self, reload_priority=True):
        try:
            self.roles_db=read_json(CONFIG_DIR/"roles.json", default={}) or {}
            sd=(read_json(CONFIG_DIR/"sets.json", default={}) or {}).get("sets",{})
            self.sets_db=sd; self.all_set_names=list(sd.keys())
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
            self._canonicalize_loaded_role_sets()
            self._shape_areas={s["shape_id"]:s["area"] for s in (read_json(CONFIG_DIR/"shapes.json", default={}) or {}).get("shapes",[])}
            sf=USER_CONFIG_DIR/"equipped_state.json"
            self.equipped_state=read_json(sf, default={}) or {}
            self.scoring_engine=ScoringEngine(str(CONFIG_DIR))
            logger.info(f"加载完成：{len(self.roles_db)} 角色，{len(self.sets_db)} 套装")
            self._update_inventory_status()
            self.role_selector.load_roles(self.roles_db,self.all_set_names,self.tape_main_stats,self.drive_sub_stats)
            if reload_priority:
                self.role_selector.load_startup_priority_config()
            self._identify_blueprint_cache=None
            if hasattr(self,"ident_shape_combo"):
                self._refresh_identify_options()
        except Exception as e: logger.error(f"加载失败: {e}")

    def _canonicalize_loaded_role_sets(self):
        changed=False
        for role_name,role_data in self.roles_db.items():
            raw_set=role_data.get("default_set","")
            resolved=resolve_name(raw_set,self.sets_db.keys(),cutoff=0.78)
            if resolved and resolved!=raw_set:
                role_data["default_set"]=resolved
                changed=True
                logger.warning(f"角色 [{role_name}] 默认套装名已自动修正: {raw_set} -> {resolved}")
        if changed:
            try:
                write_json(CONFIG_DIR/"roles.json", self.roles_db, indent=4)
            except Exception as e:
                logger.warning(f"默认套装名已在内存中修正，但写回 roles.json 失败: {e}")

    def _update_inventory_status(self):
        if not OUTPUT_FILE.exists():
            self.status_lbl.setText("库存为空")
            self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")
            return
        try:
            data=read_json(OUTPUT_FILE, default=[])
            count=len(data) if isinstance(data,list) else 0
            self.status_lbl.setText(f"库存 {count} 件" if count else "库存为空")
            self.status_lbl.setStyleSheet("color:#3fb950;font-size:12px" if count else "color:#d2991d;font-size:12px")
        except Exception:
            self.status_lbl.setText("库存文件异常")
            self.status_lbl.setStyleSheet("color:#f85149;font-size:12px")
    def _card(self,title):
        c=QFrame(); c.setStyleSheet("QFrame{background:#161b22;border:1px solid #21262d;border-radius:10px}")
        l=QVBoxLayout(c); l.setContentsMargins(20,16,20,16); l.setSpacing(8)
        lb=QLabel(title); lb.setStyleSheet("font-size:14px;font-weight:600;color:#58a6ff"); l.addWidget(lb); return c

    # ── Page: Execute

    # ── Page: Equipment

    # ── Page: Identify

    # ── Page: Blueprint

    # ── Page: Config
    def _page_config(self):
        return build_config_page(self)

    def _refresh_config_forms(self):
        return config_refresh_config_forms(self,CONFIG_DIR)

    def _confirm_leave_config_page(self):
        return config_confirm_pending_config_changes(self,CONFIG_DIR)

    def _switch_config_form(self,name):
        return config_switch_config_form(self,name,CONFIG_DIR)

    def _build_roles_form(self,data):
        return render_roles_form(self,data)

    def _build_sets_form(self,data):
        return render_sets_form(self,data)

    def _config_add_item(self):
        return config_add_config_item(self,CONFIG_DIR)

    def _add_weight(self,rn,data,cb):
        return config_add_weight(self,rn,data,cb,CONFIG_DIR)

    def _stat_choice_pool(self):
        return config_stat_choice_pool(self)

    def _save_single_extra_shape_buff(self,rn,raw_stat,value,data):
        return config_save_single_extra_shape_buff(self,rn,raw_stat,value,data,CONFIG_DIR)

    def _add_extra_shape_buff(self,rn,combo,spin,data,cb):
        self._save_single_extra_shape_buff(rn,combo.currentText() or combo.currentData(),spin.value(),data)
        cb()

    def _save_extra_shape_buff_value(self,rn,key,value,data):
        return self._save_single_extra_shape_buff(rn,key,value,data)

    def _del_extra_shape_buff(self,rn,key,data,cb):
        if rn in data:
            data[rn].pop("extra_shape_buffs",None)
            self._save_config_data(data)
            cb()

    def _save_role_weight_value(self,rn,key,value,data):
        return config_save_role_weight_value(self,rn,key,value,data,CONFIG_DIR)

    def _save_role_board_cell(self,rn,row,col,value,data):
        return config_save_role_board_cell(self,rn,row,col,value,data,CONFIG_DIR)

    def _save_role_field(self,rn,key,value,data):
        return config_save_role_field(self,rn,key,value,data,CONFIG_DIR)

    def _del_weight(self,rn,key,data,cb):
        return config_del_weight(self,rn,key,data,cb,CONFIG_DIR)

    def _add_role(self,data):
        return config_add_role(self,data,CONFIG_DIR)

    def _del_role(self,rn,data,cb=None):
        return config_del_role(self,rn,data,CONFIG_DIR,cb=cb)

    def _save_set_shapes(self,set_name,line_edit,sd):
        return config_save_set_shapes(self,set_name,line_edit,sd,CONFIG_DIR)

    def _add_set(self,sd):
        return config_add_set(self,sd,CONFIG_DIR)

    def _del_set(self,sn,sd):
        return config_del_set(self,sn,sd,CONFIG_DIR)

    def _save_config_form(self):
        return config_save_config_form(self,CONFIG_DIR,None)

    def _save_config_data(self,data):
        return config_save_config_data(self,data,CONFIG_DIR)

    def _settings_paths(self):
        return {
            "config_dir": CONFIG_DIR,
            "accounts_dir": ACCOUNTS_DIR,
            "log_dir": LOG_DIR,
            "output_file": OUTPUT_FILE,
            "screenshot_dir": SCREENSHOT_DIR,
        }

    def _page_settings(self):
        return build_settings_page(self,APP_VERSION,self._settings_paths,_iter_image_files,QUARK_NETDISK_URL)

    def _maybe_check_updates_on_startup(self):
        if self._update_config.get("never_remind"):
            return
        QTimer.singleShot(1200, lambda: self._check_updates(manual=False))

    def _check_updates(self, manual=True):
        if hasattr(self,"_update_worker") and self._update_worker.isRunning():
            if manual:
                self._update_status.setText("正在检查更新...")
            return
        self._update_check_manual=manual
        if manual:
            self._check_update_btn.setEnabled(False)
            self._update_status.setText("正在检查更新...")
        self._update_worker=WorkerThread(target=self._fetch_update_info,parent=self)
        self._update_worker.result_ready.connect(self._on_update_checked)
        self._update_worker.error.connect(self._on_update_error)
        self._update_worker.start()

    def _fetch_update_info(self):
        return fetch_update_info(
            GITHUB_LATEST_RELEASE_API,
            GITHUB_RELEASES_URL,
            APP_VERSION,
        )

    def _on_update_checked(self,info):
        manual=getattr(self,"_update_check_manual",True)
        if manual:
            self._check_update_btn.setEnabled(True)
        if not info.get("has_release"):
            if info.get("error"):
                self._update_status.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
                if manual:
                    self._show_update_failure_netdisk_prompt(info.get("error", ""))
                return
            self._update_status.setText(f"当前版本: {APP_VERSION}。{info.get('message','')}")
            if manual:
                QMessageBox.information(self,"检查更新","当前仓库还没有发布 Release。")
            return

        latest=info.get("latest") or "未知"
        if info.get("newer"):
            self._update_status.setText(f"发现新版本: {latest}（当前 {APP_VERSION}）")
            if manual or self._should_show_startup_update(info):
                self._show_update_dialog(info, manual=manual)
        else:
            self._update_status.setText(f"当前已是最新版本: {APP_VERSION}")
            if manual:
                QMessageBox.information(self,"检查更新",f"当前已是最新版本。\n当前版本: {APP_VERSION}\n最新版本: {latest}")

    def _on_update_error(self,err):
        manual=getattr(self,"_update_check_manual",True)
        if manual:
            self._check_update_btn.setEnabled(True)
            self._update_status.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
            self._show_update_failure_netdisk_prompt(err)
            return
        else:
            logger.warning(f"启动自动检查更新失败: {err}")

    def _should_show_startup_update(self, info):
        return should_show_startup_update(self._update_config,info)

    def _show_update_dialog(self, info, manual=False):
        result=show_update_dialog(self,STYLE,info,APP_VERSION)
        if result.get("never_remind"):
            self._update_config["never_remind"]=True
        if result.get("ignored_version"):
            self._update_config["ignored_version"]=result["ignored_version"]
        if result.get("changed"):
            self._save_update_config()

    def _show_update_failure_netdisk_prompt(self, detail=""):
        box=QMessageBox(self)
        box.setWindowTitle("检查更新失败")
        box.setText("GitHub请求失败，可前往网盘链接查看版本更新情况")
        if detail:
            box.setInformativeText(str(detail))
        go_btn=box.addButton("前往", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is go_btn:
            self._open_url(QUARK_NETDISK_URL)

    def _open_update_homepage(self):
        self._open_url(GITHUB_HOME_URL)

    def _open_url(self,url):
        try:
            os.startfile(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    def _is_newer_version(self,remote,current):
        return is_newer_version(remote,current)

    def _save_hotkeys(self):
        self._hk_capture=self._hk_capture_edit.keySequence().toString()
        self._hk_finish=self._hk_finish_edit.keySequence().toString()
        self._hk_stop=self._hk_stop_edit.keySequence().toString()
        self._save_hotkey_config()
        QMessageBox.information(self,"保存","快捷键已保存！\n全局截图: "+self._hk_capture+"\n截图完成: "+self._hk_finish+"\n停止: "+self._hk_stop)

    def _refresh_ss(self):
        files=_iter_image_files(SCREENSHOT_DIR)
        c=len(files)
        s=sum(f.stat().st_size for f in files)/(1024*1024) if files else 0
        self._ss_info.setText(f"当前截图: {c} 个 · {s:.1f} MB")
    def _clear_ss(self):
        if not SCREENSHOT_DIR.exists(): return
        files=_iter_image_files(SCREENSHOT_DIR)
        count=len(files)
        if count==0: QMessageBox.information(self,"清理","没有需要清理的文件。"); return
        baseline=SCREENSHOT_DIR/"raw_drive_0001.png"
        has_baseline=baseline.exists()
        delete_files=[f for f in files if f.resolve()!=baseline.resolve()]
        prompt=f"由于增量逻辑设定，需要保留一张 raw_drive_0001.png，请勿删除。\n\n将删除 {len(delete_files)} 个其它截图，不可恢复。"
        if not has_baseline:
            prompt+="\n\n注意：丢失用于对比的截图，请重新全量扫描，或不要使用全自动增量扫描。"
        if QMessageBox.question(self,"确认清理",prompt,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
            for f in delete_files:
                try: f.unlink()
                except: pass
            self._refresh_ss(); logger.success(f"已清理 {len(delete_files)} 个截图")
            if not has_baseline:
                QMessageBox.warning(self,"清理完成","注意：丢失用于对比的截图，请重新全量扫描，或不要使用全自动增量扫描。")

def _install_feature_methods():
    import sys as _sys
    from src.features.onboarding import guide as onboarding_guide
    from src.features.inventory import page as inventory_page
    from src.features.blueprints import page as blueprints_page
    from src.features.allocation import runner as allocation_runner
    from src.features.allocation import results_view as allocation_results_view
    from src.features.identification import controller as identification_controller
    from src.features.identification import dialogs as identification_dialogs
    from src.features.scanning import controller as scanning_controller

    app_module = _sys.modules[__name__]
    for module in (
        onboarding_guide,
        inventory_page,
        blueprints_page,
        allocation_runner,
        allocation_results_view,
        identification_controller,
        identification_dialogs,
        scanning_controller,
    ):
        module.install_methods(app_module, MainWindow)

_install_feature_methods()

# ── Facade

# ── Entry
def _global_exception_handler(exc_type, exc_value, exc_tb):
    """全局异常处理，防止未捕获异常导致闪退"""
    import traceback as tb
    error_msg = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"未捕获异常:\n{error_msg}")
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "程序异常", f"发生未捕获的异常:\n\n{error_msg[:1000]}")
    except:
        pass

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
    app=QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    apply_dark_palette(app)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": run_gui()
