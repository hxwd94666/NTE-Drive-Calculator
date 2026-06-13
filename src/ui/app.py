"""NTE Drive Calc - PySide6 Desktop Application"""

import sys, os, json, time, shutil, traceback, threading, ctypes, re
from pathlib import Path
from typing import Optional

if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
    APP_DIR = Path(sys.executable).parent
else:
    ROOT = Path(__file__).resolve().parent.parent.parent
    APP_DIR = ROOT
sys.path.insert(0, str(ROOT))

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
USER_CONFIG_DIR = DATA_ROOT / "config"
CONFIG_DIR = USER_CONFIG_DIR
TEMPLATE_DIR = CONFIG_DIR / "templates"
OUTPUT_FILE = USER_CONFIG_DIR / "real_inventory.json"
SCREENSHOT_DIR = DATA_ROOT / "scanned_images"

APP_VERSION = "1.0.0"
GITHUB_HOME_URL = "https://github.com/hxwd94666/NTE-Drive-Calc"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/hxwd94666/NTE-Drive-Calc/releases/latest"
GITHUB_RELEASES_URL = GITHUB_HOME_URL + "/releases"

def _seed_user_config():
    if BUNDLED_CONFIG_DIR.exists():
        for fname in ("roles.json", "sets.json", "shapes.json", "stats.json", "equipped_state.json", "real_inventory.json"):
            dst = USER_CONFIG_DIR / fname
            src = BUNDLED_CONFIG_DIR / fname
            if dst.exists():
                continue
            if src.exists() and src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))
            elif fname == "equipped_state.json":
                dst.write_text("{}", encoding="utf-8")
            elif fname == "real_inventory.json":
                dst.write_text("[]", encoding="utf-8")

        src_templates = BUNDLED_CONFIG_DIR / "templates"
        if src_templates.exists() and src_templates.resolve() != TEMPLATE_DIR.resolve():
            shutil.copytree(str(src_templates), str(TEMPLATE_DIR), dirs_exist_ok=True)


_seed_user_config()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QScrollArea, QStackedWidget, QFrame,
    QTextEdit, QRadioButton, QButtonGroup, QLineEdit, QSpinBox,
    QMessageBox, QComboBox, QCheckBox, QDialog, QDialogButtonBox,
    QTabWidget, QGroupBox, QSizePolicy, QSizeGrip, QGridLayout,
    QDoubleSpinBox, QInputDialog, QKeySequenceEdit, QFormLayout,
    QProgressDialog, QFileDialog, QCompleter,
)
from PySide6.QtCore import Qt, Signal, QThread, QSize, QRect, QPoint, QTimer, QEvent
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QTextCursor, QPixmap, QMouseEvent,
    QKeySequence, QShortcut, QIcon, QPalette, QIntValidator,
)

from src.scanner.batch_processor import BatchProcessor
from src.scanner.drone_scanner import DroneScanner
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.optimizer.state_manager import StateManager
from src.optimizer.scoring import ScoringEngine
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.models.equipment import Drive, Tape

GRADE_COLORS = {"ACE": "#ffa726", "SSS": "#ffa726", "SS": "#f0883e", "S": "#f0883e", "A": "#7ec8e3", "B": "#5b9bd5", "C": "#4a7fb5", "D": "#3d5a80"}
GRADE_BGS = {"ACE": "#ffa72630", "SSS": "#ffa72620", "SS": "#f0883e18", "S": "#f0883e18", "A": "#7ec8e318", "B": "#5b9bd515", "C": "#4a7fb512", "D": "#3d5a8010"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def _iter_image_files(path: Path):
    if not path.exists():
        return []
    return [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTS]

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


def _apply_dark_palette(app: QApplication):
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#0d1117"))
    palette.setColor(QPalette.WindowText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Base, QColor("#0d1117"))
    palette.setColor(QPalette.AlternateBase, QColor("#161b22"))
    palette.setColor(QPalette.ToolTipBase, QColor("#161b22"))
    palette.setColor(QPalette.ToolTipText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Text, QColor("#c9d1d9"))
    palette.setColor(QPalette.Button, QColor("#21262d"))
    palette.setColor(QPalette.ButtonText, QColor("#c9d1d9"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

STYLE = """
QMainWindow{background:#0d1117;border:1px solid #21262d;border-radius:10px}
QDialog{background:#0d1117;border:1px solid #21262d;border-radius:8px}
QWidget{color:#c9d1d9;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px}

#sidebar{background:#161b22;border-right:1px solid #21262d;min-width:200px;max-width:200px;border-bottom-left-radius:10px}
#sidebar QPushButton{background:transparent;color:#8b949e;border:none;border-radius:8px;padding:10px 14px;text-align:left;font-size:13px;font-weight:500;margin:2px 8px}
#sidebar QPushButton:hover{background:#1c2128;color:#c9d1d9}
#sidebar QPushButton:checked{background:#1f6feb33;color:#58a6ff}

#titleBar{background:#161b22;border-bottom:1px solid #21262d;border-top-left-radius:10px;border-top-right-radius:10px}
#titleBar QLabel{font-size:13px;font-weight:600;color:#c9d1d9}
#titleBar QPushButton{background:transparent;border:none;border-radius:6px;color:#8b949e;font-size:14px;padding:4px 10px;font-weight:bold}
#titleBar QPushButton:hover{background:#21262d;color:#c9d1d9}
#titleBar #btnClose:hover{background:#da3633;color:#fff}

#topbar{background:#161b22;border-bottom:1px solid #21262d;padding:10px 20px}
#topbar QLabel{font-size:15px;font-weight:600;color:#c9d1d9}

QPushButton{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:7px 16px;font-weight:500}
QPushButton:hover{background:#30363d}
QPushButton:pressed{background:#161b22}
QPushButton#btnPrimary{background:#238636;color:#fff;border:1px solid #2ea043;font-weight:600}
QPushButton#btnPrimary:hover{background:#2ea043}
QPushButton#btnPrimary:disabled{background:#1b3a24;color:#6e7681}
QPushButton#btnDanger{background:#da3633;color:#fff;border:1px solid #f85149}
QPushButton#btnDanger:hover{background:#f85149}
QPushButton#btnAction{background:#1f6feb33;color:#58a6ff;border:1px solid #1f6feb;font-size:12px;padding:5px 12px}
QPushButton#btnAction:hover{background:#1f6feb66}
QPushButton#btnSm{font-size:11px;padding:4px 8px;min-width:28px;min-height:24px}
QPushButton#btnHelp{background:transparent;border:1px solid #30363d;border-radius:10px;color:#8b949e;font-size:11px;font-weight:700;padding:2px 7px;min-width:20px;max-width:20px;min-height:20px;max-height:20px}
QPushButton#btnHelp:hover{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff}

QLineEdit,QTextEdit,QSpinBox,QDoubleSpinBox,QComboBox{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:7px 10px}
QLineEdit:focus,QTextEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus{border:1px solid #58a6ff}
QComboBox::drop-down{border:none;width:20px}
QComboBox QAbstractItemView{background:#161b22;border:1px solid #30363d;selection-background-color:#1f6feb33}

QRadioButton{spacing:10px;padding:6px 0}
QRadioButton::indicator{width:22px;height:22px;border-radius:11px;border:2px solid #30363d;background:#0d1117}
QRadioButton::indicator:checked{border:2px solid #58a6ff;background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,stop:0 #58a6ff,stop:0.45 #1f6feb,stop:0.5 #0d1117,stop:1 #0d1117)}
QRadioButton::indicator:hover{border:2px solid #58a6ff}
QCheckBox{spacing:8px}
QCheckBox::indicator{width:18px;height:18px;border-radius:4px;border:2px solid #30363d;background:#0d1117}
QCheckBox::indicator:checked{background:#238636;border-color:#2ea043}

QScrollArea{border:none;background:transparent}
QScrollBar:vertical{background:#0d1117;width:8px;border-radius:4px}
QScrollBar::handle:vertical{background:#30363d;border-radius:4px;min-height:30px}
QScrollBar::handle:vertical:hover{background:#484f58}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}

QTabWidget::pane{border:1px solid #21262d;background:#0d1117;border-radius:8px}
QTabBar::tab{background:#161b22;color:#8b949e;padding:8px 18px;border:1px solid #21262d;border-bottom:none;border-top-left-radius:8px;border-top-right-radius:8px}
QTabBar::tab:selected{background:#0d1117;color:#58a6ff;border-bottom:2px solid #58a6ff}

QToolTip{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:12px}

QGroupBox{background:#0d1117;border:1px solid #21262d;border-radius:8px;margin-top:14px;padding:20px;padding-top:32px;font-weight:600;color:#58a6ff}
QGroupBox::title{subcontrol-origin:margin;left:14px;padding:0 8px}

#logPanel{background:#0d1117;border-top:1px solid #21262d}
#logPanel QTextEdit{background:#0d1117;border:none;color:#8b949e;font-family:'Consolas','Cascadia Code',monospace;font-size:11px}

QKeySequenceEdit{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 8px;font-family:'Consolas',monospace;font-size:12px}
"""

SCAN_HELP = {
    "4": "直接读取库存\n\n跳过扫描步骤，直接读取已有的\nreal_inventory.json 进行分配计算。\n\n适合：已有库存数据，只想重跑分配。",
    "3": "离线解析\n\n读取 scanned_images/ 文件夹中的截图，\n用 OCR + 模板匹配提取属性，\n生成 real_inventory.json 后分配。\n\n适合：已有截图，需要解析后分配。",
    "2": "增量扫描\n\n自动探测 NEW 标记，\n截取新装备 → 解析 → 分配。\n\n适合：日常更新，只抓取新装备。",
    "1": "全量扫描\n\n虚拟手柄自动遍历背包，\n全量截图所有驱动 → 解析 → 分配。\n\n适合：首次使用。",
}

DRONE_HELP = {
    "2": "半自动模式\n\n· 自己用鼠标点选装备\n· 按 F9 抓取当前装备\n· 按 F10 结算并触发解析\n· 速度快、精准度高\n\n日常推荐",
    "1": "全自动模式\n\n· 程序自动向下翻页\n· 自动检测 NEW 标记\n· 自动截图所有新装备\n· 无需人工干预\n\n需要游戏画面在背包首页",
}


def _match_pinyin(name: str, filt: str) -> bool:
    if not filt: return True
    f = filt.lower(); n = name.lower()
    if f in n: return True
    try:
        from pypinyin import lazy_pinyin, Style
        py_list = lazy_pinyin(name, style=Style.NORMAL)
        if f in ''.join(py_list).lower(): return True
        if f in ''.join(p[0] for p in py_list if p).lower(): return True
    except ImportError: pass
    return False


def _show_help(parent, title, text):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title); dlg.setMinimumSize(380, 220); dlg.setStyleSheet(STYLE)
    l = QVBoxLayout(dlg); l.setSpacing(12)
    lbl = QLabel(text); lbl.setStyleSheet("font-size:13px;line-height:1.6;padding:8px"); lbl.setWordWrap(True)
    l.addWidget(lbl)
    bb = QDialogButtonBox(QDialogButtonBox.Ok); bb.accepted.connect(dlg.accept); l.addWidget(bb)
    dlg.exec()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class SearchableComboBox(NoWheelComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_items = []
        self._search_updating = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        if self.lineEdit():
            self.lineEdit().setClearButtonEnabled(True)
            self.lineEdit().installEventFilter(self)
            self.lineEdit().textEdited.connect(self._filter_items)

    def refresh_search_items(self):
        self._search_items = [(self.itemText(i), self.itemData(i)) for i in range(self.count())]

    def _restore_items(self, text=None, filtered=None):
        if self._search_updating:
            return
        if not self._search_items:
            self.refresh_search_items()
        items = self._search_items if filtered is None else filtered
        self._search_updating = True
        old_block = self.blockSignals(True)
        self.clear()
        for label, data in items:
            self.addItem(label, data)
        self.blockSignals(old_block)
        if text is not None:
            self.setEditText(text)
        self._search_updating = False

    def _filter_items(self, text):
        if self._search_updating:
            return
        if not self._search_items:
            self.refresh_search_items()
        text = text.strip()
        filtered = [item for item in self._search_items if _match_pinyin(item[0], text)]
        self._restore_items(text, filtered or self._search_items)
        if self.lineEdit():
            self.lineEdit().setCursorPosition(len(text))
        self.showPopup()

    def _open_all(self):
        text = self.currentText()
        self._restore_items(text, self._search_items)
        if self.lineEdit():
            self.lineEdit().selectAll()
        self.showPopup()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
            QTimer.singleShot(0, self._open_all)
        return super().eventFilter(obj, event)


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)

    def wheelEvent(self, event):
        event.ignore()


class PuzzleBoardWidget(QWidget):
    SHAPE_HUE = {"H_2":0,"V_2":30,"H_3":60,"V_3":90,
                 "L_3_TL":120,"L_3_TR":150,"L_3_BL":180,"L_3_BR":210,
                 "H_4":240,"V_4":270,"Trap_4_H":300,"Trap_4_V":330,
                 "TAPE_15":50}
    def __init__(self, matrix=None, cell_size=40, parent=None):
        super().__init__(parent); self.matrix=matrix or []; self.cell_size=cell_size
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed); self._recalc()
    def _recalc(self):
        if not self.matrix: self.setFixedSize(100,100); return
        r,c=len(self.matrix), len(self.matrix[0]) if self.matrix else 0
        self.setFixedSize(c*self.cell_size+8, r*self.cell_size+8)
    def set_matrix(self,m): self.matrix=m; self._recalc(); self.update()
    def paintEvent(self,e):
        if not self.matrix: return
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rows,cols=len(self.matrix), len(self.matrix[0])
        for r in range(rows):
            for c in range(cols):
                x,y=c*self.cell_size+4, r*self.cell_size+4
                rect=QRect(x+1,y+1,self.cell_size-2,self.cell_size-2)
                cell=str(self.matrix[r][c]) if r<rows and c<len(self.matrix[r]) else "0"
                if cell in ("XX","-1"):
                    p.setPen(QPen(QColor("#da3633"),1)); p.setBrush(QColor(218,54,51,40)); p.drawRoundedRect(rect,4,4)
                    p.setPen(QColor("#da3633")); p.setFont(QFont("Microsoft YaHei UI",8,QFont.Bold)); p.drawText(rect,Qt.AlignCenter,"✕")
                elif cell in ("0","0.0"):
                    p.setPen(QPen(QColor("#21262d"),1)); p.setBrush(QColor(13,17,23,120)); p.drawRoundedRect(rect,4,4)
                else:
                    hue=self.SHAPE_HUE.get(cell, abs(hash(cell))%360)
                    color=QColor.fromHsl(hue,180,128); border=QColor.fromHsl(hue,220,160)
                    p.setPen(QPen(border,1.5)); p.setBrush(QColor(color.red(),color.green(),color.blue(),100)); p.drawRoundedRect(rect,4,4)
                    p.setPen(border); p.setFont(QFont("Microsoft YaHei UI",7,QFont.Bold))
                    s=cell.replace("L_3_","").replace("Trap_4_","").replace("TAPE_","T")
                    p.drawText(rect,Qt.AlignCenter,s)


class RoleSelector(QWidget):
    orderChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.all_roles:dict={}; self.all_sets:list[str]=[]; self.selected:list[str]=[]; self._cards:dict={}; self._build()
    def _build(self):
        l=QVBoxLayout(self); l.setContentsMargins(0,0,0,0); l.setSpacing(8)
        search_row=QHBoxLayout(); search_row.setSpacing(8)
        self.search=QLineEdit(); self.search.setPlaceholderText("搜索角色（支持拼音）..."); self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter); search_row.addWidget(self.search,1)
        reset_btn=QPushButton("重置优先级"); reset_btn.setObjectName("btnDanger")
        reset_btn.clicked.connect(self.reset_selection); search_row.addWidget(reset_btn)
        l.addLayout(search_row)
        lbl=QLabel("点击选择角色，选中顺序即优先级"); lbl.setStyleSheet("color:#8b949e;font-size:11px;border:none"); l.addWidget(lbl)
        self.grid_scroll=QScrollArea(); self.grid_scroll.setWidgetResizable(True); self.grid_scroll.setMinimumHeight(200)
        self.grid_w=QWidget(); self.grid_layout=QGridLayout(self.grid_w)
        self.grid_layout.setContentsMargins(0,0,0,0); self.grid_layout.setSpacing(6); self.grid_layout.setAlignment(Qt.AlignLeft|Qt.AlignTop)
        self.grid_scroll.setWidget(self.grid_w); l.addWidget(self.grid_scroll,1)
    _CARD_SEL="QFrame{background:#1f6feb22;border:2px solid #58a6ff;border-radius:8px}QFrame:hover{border-color:#79c0ff}"
    _CARD_OFF="QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px}QFrame:hover{border-color:#30363d}"

    def load_roles(self,roles_db,all_sets): self.all_roles=roles_db; self.all_sets=all_sets; self._render_grid()
    def _render_grid(self,filter_text=""):
        while self.grid_layout.count():
            it=self.grid_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self._cards.clear()
        names=sorted(self.all_roles.keys())
        if filter_text.strip(): names=[n for n in names if _match_pinyin(n,filter_text.strip())]
        MAX_COLS=4; col,row=0,0
        for name in names:
            card=self._make_card(name)
            self.grid_layout.addWidget(card,row,col); col+=1
            if col>=MAX_COLS: col=0; row+=1
    def _make_card(self,name):
        sel=name in self.selected
        card=QFrame(); card.setFixedSize(180,56); card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(self._CARD_SEL if sel else self._CARD_OFF)
        card.setProperty("role_name",name)
        cl=QGridLayout(card); cl.setContentsMargins(8,4,8,4); cl.setSpacing(4)
        cl.setColumnMinimumWidth(0,20); cl.setColumnStretch(1,1)
        badge=QLabel(""); badge.setFixedSize(20,20); badge.setAlignment(Qt.AlignCenter)
        if sel:
            idx=self.selected.index(name)+1
            badge.setText(str(idx)); badge.setStyleSheet("background:#1f6feb;color:#fff;border-radius:10px;font-size:10px;font-weight:700;border:none")
        else:
            badge.setStyleSheet("background:transparent;border:none")
        cl.addWidget(badge,0,0,2,1)
        nm=QLabel(name); nm.setStyleSheet("font-size:12px;font-weight:600;border:none;background:transparent;color:#c9d1d9"); cl.addWidget(nm,0,1)
        rd=self.all_roles.get(name,{}); ds=rd.get("default_set",self.all_sets[0] if self.all_sets else "")
        combo=SearchableComboBox(); combo.addItems(self.all_sets); combo.refresh_search_items(); combo.setFixedHeight(20)
        if ds in self.all_sets: combo.setCurrentText(ds)
        combo.setStyleSheet("font-size:10px;padding:1px 4px;background:#0d1117;border:1px solid #30363d;border-radius:3px;color:#8b949e")
        combo.currentTextChanged.connect(lambda: self.orderChanged.emit())
        cl.addWidget(combo,1,1)
        card.mousePressEvent=lambda e,n=name: self._toggle(n)
        self._cards[name]={"card":card,"combo":combo,"badge":badge}
        return card
    def _filter(self,txt): self._render_grid(txt)
    def reset_selection(self):
        self.selected.clear()
        self._render_grid(self.search.text())
        self.orderChanged.emit()
    def _toggle(self,name):
        if name in self.selected: self.selected.remove(name)
        else: self.selected.append(name)
        self._render_grid(self.search.text()); self.orderChanged.emit()
    def get_selected(self): return list(self.selected)
    def get_custom_sets(self):
        result={}
        for name,info in self._cards.items():
            if name in self.selected: result[name]=info["combo"].currentText()
        return result
    def save_priority_config(self):
        cfg={"priority_list":self.selected,"custom_sets":self.get_custom_sets()}
        with open(USER_CONFIG_DIR/"priority_config.json","w",encoding="utf-8") as f: json.dump(cfg,f,ensure_ascii=False,indent=2)
    def load_priority_config(self):
        pf=USER_CONFIG_DIR/"priority_config.json"
        if not pf.exists(): return
        try:
            with open(pf,"r",encoding="utf-8") as f: cfg=json.load(f)
            saved_list=cfg.get("priority_list",[])
            self.selected=[r for r in saved_list if r in self.all_roles]
            self._render_grid(self.search.text())
        except Exception: pass

# ── Shape Image
_shape_pixmaps:dict[tuple[str, str],QPixmap]={}
def _get_shape_pixmap(shape_id:str,size=60,quality:str|None=None)->QPixmap:
    key=(shape_id,quality or "")
    if key in _shape_pixmaps:
        return _shape_pixmaps[key].scaled(size,size,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    path=TEMPLATE_DIR/f"{shape_id}_{quality}.png" if quality else TEMPLATE_DIR/f"{shape_id}.png"
    if quality and not path.exists():
        path=TEMPLATE_DIR/f"{shape_id}.png"
    if path.exists():
        pm=QPixmap(str(path)); _shape_pixmaps[key]=pm
        return pm.scaled(size,size,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    return QPixmap()

# ── Worker
class WorkerThread(QThread):
    result_ready=Signal(object); error=Signal(str)
    def __init__(self,target,parent=None): super().__init__(parent); self.target=target
    def run(self):
        try: self.result_ready.emit(self.target())
        except SystemExit as e:
            logger.error(f"WorkerThread 捕获 SystemExit: {e}")
            self.error.emit(f"系统异常退出: {e}")
        except Exception as e:
            import traceback as tb
            err_detail=f"{e}\n\n{tb.format_exc()}"
            logger.error(f"WorkerThread 异常: {err_detail}")
            self.error.emit(str(e))

class VisionWorkerThread(QThread):
    processing_done=Signal(list); canceled=Signal(int); error=Signal(str)
    progress=Signal(int,int,str)  # current, total, filename
    def __init__(self,input_dir,output_file,parent=None,replace_output=False):
        super().__init__(parent); self.input_dir=input_dir; self.output_file=output_file; self.replace_output=replace_output; self._cancel_requested=False
    def request_cancel(self):
        self._cancel_requested=True
    def run(self):
        try:
            p=BatchProcessor(input_dir=self.input_dir,output_file=self.output_file,config_dir=str(CONFIG_DIR),replace_output=self.replace_output)
            if not os.path.exists(self.input_dir):
                self.error.emit(f"找不到截图文件夹 {self.input_dir}"); return
            image_files=[f for f in os.listdir(self.input_dir) if
                         f.lower().endswith((".png",".jpg",".jpeg",".bmp")) and os.path.isfile(os.path.join(self.input_dir,f))]
            image_files.sort()
            total=len(image_files)
            if total==0:
                self.error.emit("截图文件夹为空，没有需要处理的图片。"); return
            for idx,filename in enumerate(image_files,1):
                if self._cancel_requested:
                    break
                self.progress.emit(idx,total,filename)
                file_path=os.path.join(self.input_dir,filename)
                try:
                    item_obj,added=p.process_image_file(file_path,filename)
                    if not added:
                        logger.info(f"命名相邻截图解析数据一致，跳过重复入库: {filename}")
                except Exception as e:
                    logger.error(f"解析失败: {filename} | {e}")
            processed_count=len(p.inventory)
            successful_image_paths=list(p.successful_image_paths)
            if p.inventory:
                p._export_to_json()
            del p
            if self._cancel_requested:
                logger.info("VisionWorkerThread: 解析已取消")
                self.canceled.emit(processed_count)
                return
            logger.info("VisionWorkerThread: 即将发射 processing_done 信号")
            self.processing_done.emit(successful_image_paths)
        except SystemExit as e:
            logger.error(f"VisionWorker 捕获 SystemExit: {e}")
            self.error.emit(f"系统异常退出: {e}")
        except Exception as e:
            import traceback as tb
            err_detail=f"{e}\n\n{tb.format_exc()}"
            logger.error(f"VisionWorker 异常: {err_detail}")
            self.error.emit(str(e))

class ScanWorkerThread(QThread):
    scan_done=Signal(int); error=Signal(str); scanner_ready=Signal()
    def __init__(self,mode="semi",parent=None):
        super().__init__(parent); self.mode=mode; self.scanner=None
    def run(self):
        try:
            self.scanner=DroneScanner(output_dir=str(SCREENSHOT_DIR),template_path=str(TEMPLATE_DIR/"new_tag.png"))
            self.scanner_ready.emit()
            if self.mode=="auto":
                count=self.scanner.start_scan()
            else:
                count=self.scanner.start_semi_auto_scan()
            self.scan_done.emit(count)
        except Exception as e:
            logger.error(f"ScanWorker 异常: {e}")
            self.error.emit(str(e))

class GamepadScanWorkerThread(QThread):
    scan_done=Signal(int); error=Signal(str); scanner_ready=Signal()
    def __init__(self,total_drives,parent=None):
        super().__init__(parent); self.total_drives=total_drives; self.scanner=None
    def run(self):
        try:
            from src.scanner.gamepad_controller import GamepadScanner
            self.scanner=GamepadScanner(output_dir=str(SCREENSHOT_DIR))
            self.scanner_ready.emit()
            count=self.scanner.start_scan(self.total_drives)
            self.scan_done.emit(count)
        except (FileNotFoundError, OSError) as e:
            logger.error(f"GamepadScanWorker DLL错误: {e}")
            self.error.emit(f"ViGEmClient.dll 加载失败，请确认:\n1. 已安装 ViGEmBus 驱动 (https://github.com/nefarius/ViGEmBus/releases)\n2. 重启电脑后再试\n\n原始错误: {e}")
        except Exception as e:
            logger.error(f"GamepadScanWorker 异常: {e}")
            self.error.emit(str(e))

# ── Log Sink
class QtLogSink:
    def __init__(self,signal): self.signal=signal
    def write(self,m):
        msg=m.strip()
        if msg: self.signal.emit(msg)
    def flush(self): pass

class PlainTextOnlyTextEdit(QTextEdit):
    def insertFromMimeData(self, source):
        if source.hasText():
            self.insertPlainText(source.text())

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
        self.roles_db:dict={}; self.sets_db:dict={}; self.all_set_names:list[str]=[]
        self.equipped_state:dict={}; self.final_plan=None; self._shape_areas:dict={}
        self.scoring_engine=None
        self._pending_archive_paths=[]
        self._pending_parse_only=False
        self._identify_blueprint_cache=None
        self.state_mgr=StateManager(config_dir=str(USER_CONFIG_DIR)); self._log_enabled=False

        # Hotkey config
        self._hk_capture="F9"; self._hk_finish="F10"; self._hk_stop="F12"
        self._load_hotkey_config()

        self.log_signal.connect(self._on_log); self.identify_capture_signal.connect(self._add_identify_capture_path); self.identify_capture_done_signal.connect(self._finish_identify_capture_mode); self._log_sink=QtLogSink(self.log_signal)
        try:
            from loguru import logger as lu
            lu.add(self._log_sink,format="{time:HH:mm:ss} | {level: <8} | {message}",level="INFO",colorize=False)
        except: pass
        self._build_ui(); self._load_data(); self._on_log("系统就绪"); self._maybe_show_quick_start()

    def _load_hotkey_config(self):
        path=USER_CONFIG_DIR/"hotkeys.json"
        try:
            if path.exists():
                with open(path,"r",encoding="utf-8") as f: d=json.load(f)
                self._hk_capture=d.get("capture","F9"); self._hk_finish=d.get("finish","F10"); self._hk_stop=d.get("stop","F12")
        except: pass
    def _save_hotkey_config(self):
        with open(USER_CONFIG_DIR/"hotkeys.json","w",encoding="utf-8") as f:
            json.dump({"capture":self._hk_capture,"finish":self._hk_finish,"stop":self._hk_stop},f,indent=2)

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
        self.btn_exec=self._nav("⚡  执行","execute"); self.btn_equip=self._nav("💎  配装","equipment")
        self.btn_identify=self._nav("🔍  鉴定","identify")
        self.btn_blueprint=self._nav("📐  图纸","blueprint")
        self.btn_config=self._nav("⚙  配置","config"); self.btn_settings=self._nav("🔧  设置","settings")
        for b in [self.btn_exec,self.btn_equip,self.btn_identify,self.btn_blueprint,self.btn_config,self.btn_settings]: sl.addWidget(b)
        sl.addStretch(); body.addWidget(sidebar)

        right=QWidget(); rr=QVBoxLayout(right); rr.setContentsMargins(0,0,0,0); rr.setSpacing(0)
        tbar=QWidget(); tbar.setObjectName("topbar"); tbh=QHBoxLayout(tbar); tbh.setContentsMargins(20,10,20,10)
        self.topbar_title=QLabel("⚡  执行"); tbh.addWidget(self.topbar_title); tbh.addStretch()
        guide_btn=QPushButton("新手向导"); guide_btn.setObjectName("btnAction"); guide_btn.clicked.connect(self._show_quick_start); tbh.addWidget(guide_btn)
        self.status_lbl=QLabel("就绪"); self.status_lbl.setStyleSheet("color:#6e7681;font-size:12px"); tbh.addWidget(self.status_lbl)
        guide_btn.setText("使用教程")
        rr.addWidget(tbar)
        self.stack=QStackedWidget()
        self.stack.addWidget(self._page_execute()); self.stack.addWidget(self._page_equipment())
        self.stack.addWidget(self._page_identify())
        self.stack.addWidget(self._page_blueprint())
        self.stack.addWidget(self._page_config()); self.stack.addWidget(self._page_settings())
        rr.addWidget(self.stack,1)

        self.log_frame=QWidget(); self.log_frame.setObjectName("logPanel"); self.log_frame.setVisible(False)
        lf=QVBoxLayout(self.log_frame); lf.setContentsMargins(0,0,0,0)
        lh=QHBoxLayout(); lh.setContentsMargins(16,6,16,6); lh.addWidget(QLabel("运行日志")); lh.addStretch()
        cb=QPushButton("清空"); cb.setObjectName("btnSm"); cb.clicked.connect(self._clear_log); lh.addWidget(cb); lf.addLayout(lh)
        self.log_view=QTextEdit(); self.log_view.setReadOnly(True); self.log_view.setMaximumHeight(140); lf.addWidget(self.log_view)
        rr.addWidget(self.log_frame)
        body.addWidget(right,1); root.addLayout(body)
        QSizeGrip(self).setStyleSheet("background:transparent"); self.btn_exec.setChecked(True)

    def _nav(self,text,page): b=QPushButton(text); b.setCheckable(True); b.clicked.connect(lambda: self._go(page)); return b
    def _go(self,page):
        m={"execute":0,"equipment":1,"identify":2,"blueprint":3,"config":4,"settings":5}; self.stack.setCurrentIndex(m.get(page,0))
        t={"execute":"⚡  执行","equipment":"💎  配装","identify":"🔍  鉴定","blueprint":"📐  图纸","config":"⚙  配置","settings":"🔧  设置"}; self.topbar_title.setText(t.get(page,""))
        for btn in [self.btn_exec,self.btn_equip,self.btn_identify,self.btn_blueprint,self.btn_config,self.btn_settings]: btn.setChecked(False)
        {"execute":self.btn_exec,"equipment":self.btn_equip,"identify":self.btn_identify,"blueprint":self.btn_blueprint,"config":self.btn_config,"settings":self.btn_settings}[page].setChecked(True)
        if page=="equipment": self._refresh_equip()
        elif page=="identify": self._refresh_identify_options()
        elif page=="config": self._refresh_config_forms()
        elif page=="blueprint": self._refresh_blueprints()

    def _maybe_show_quick_start(self):
        seen_file=USER_CONFIG_DIR/"quick_start_seen.json"
        if not seen_file.exists():
            QTimer.singleShot(500, lambda: self._show_quick_start(auto=True))

    def _show_quick_start(self, auto=False):
        dlg=QDialog(self)
        dlg.setWindowTitle("新手向导")
        dlg.setMinimumSize(520,360)
        dlg.setStyleSheet(STYLE)
        l=QVBoxLayout(dlg); l.setSpacing(12)
        title=QLabel("异环驱动计算器")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#58a6ff")
        l.addWidget(title)
        body=QLabel(
            "1. 安装完成后可直接打开软件；程序启动时会统一申请管理员权限。\n"
            "2. 首次使用先进入「执行」，选择目标角色；已有库存时可直接读取库存。\n"
            "3. 没有库存时，把装备截图放进 scanned_images 后选择离线解析，或使用半自动/全量扫描。\n"
            "4. 半自动和全量扫描会直接使用当前管理员进程控制鼠标/手柄，不再重复弹出权限提示。\n"
            "5. 配装结果确认后点保存，已成功解析并保存配装的截图才会归档。"
        )
        body.setWordWrap(True)
        body.setStyleSheet("color:#c9d1d9;line-height:1.6;padding:8px")
        l.addWidget(body)
        row=QHBoxLayout()
        open_ss=QPushButton("打开截图文件夹"); open_ss.clicked.connect(lambda: os.startfile(str(SCREENSHOT_DIR)) if SCREENSHOT_DIR.exists() else None)
        open_cfg=QPushButton("打开配置文件夹"); open_cfg.clicked.connect(lambda: os.startfile(str(CONFIG_DIR)) if CONFIG_DIR.exists() else None)
        row.addWidget(open_ss); row.addWidget(open_cfg); row.addStretch(); l.addLayout(row)
        dont_show=QCheckBox("不再自动显示")
        dont_show.setChecked(auto)
        l.addWidget(dont_show)
        bb=QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        l.addWidget(bb)
        dlg.exec()
        if dont_show.isChecked():
            try:
                with open(USER_CONFIG_DIR/"quick_start_seen.json","w",encoding="utf-8") as f:
                    json.dump({"seen":True},f,ensure_ascii=False,indent=2)
            except Exception:
                pass

    # ── Log
    def _guide_image_files(self):
        guide_dir = TEMPLATE_DIR / "guide"
        if not guide_dir.exists():
            return []
        def natural_key(path: Path):
            return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]
        return sorted([p for p in guide_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=natural_key)

    def _maybe_show_quick_start(self):
        seen_file = USER_CONFIG_DIR / "guide_seen.json"
        if not seen_file.exists():
            QTimer.singleShot(500, lambda: self._show_quick_start(auto=True))

    def _show_quick_start(self, auto=False):
        images = self._guide_image_files()
        if not images:
            QMessageBox.warning(self, "使用教程", "未找到教程图片，请检查 config/templates/guide。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("使用教程")
        dlg.setMinimumSize(760, 620)
        dlg.setStyleSheet(STYLE)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setMinimumSize(720, 500)
        layout.addWidget(image_label, 1)

        index = {"value": 0}
        prev_btn = QPushButton("<")
        next_btn = QPushButton(">")
        dont_show = QCheckBox("不再自动显示")
        dont_show.setChecked(auto)

        def render():
            pixmap = QPixmap(str(images[index["value"]]))
            image_label.setPixmap(pixmap.scaled(image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            prev_btn.setEnabled(index["value"] > 0)
            next_btn.setEnabled(index["value"] < len(images) - 1)

        def go(delta):
            index["value"] = max(0, min(len(images) - 1, index["value"] + delta))
            render()

        prev_btn.clicked.connect(lambda: go(-1))
        next_btn.clicked.connect(lambda: go(1))

        row = QHBoxLayout()
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addStretch()
        row.addWidget(dont_show)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dlg.accept)
        row.addWidget(buttons)
        layout.addLayout(row)

        render()
        dlg.exec()
        if dont_show.isChecked():
            try:
                with open(USER_CONFIG_DIR / "guide_seen.json", "w", encoding="utf-8") as f:
                    json.dump({"seen": True}, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

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
    def _load_data(self):
        try:
            with open(CONFIG_DIR/"roles.json","r",encoding="utf-8") as f: self.roles_db=json.load(f)
            with open(CONFIG_DIR/"sets.json","r",encoding="utf-8") as f:
                sd=json.load(f).get("sets",{}); self.sets_db=sd; self.all_set_names=list(sd.keys())
            self._canonicalize_loaded_role_sets()
            with open(CONFIG_DIR/"shapes.json","r",encoding="utf-8") as f:
                self._shape_areas={s["shape_id"]:s["area"] for s in json.load(f).get("shapes",[])}
            sf=USER_CONFIG_DIR/"equipped_state.json"
            if sf.exists():
                with open(sf,"r",encoding="utf-8") as f: self.equipped_state=json.load(f)
            self.scoring_engine=ScoringEngine(str(CONFIG_DIR))
            logger.info(f"加载完成：{len(self.roles_db)} 角色，{len(self.sets_db)} 套装")
            self._update_inventory_status()
            self.role_selector.load_roles(self.roles_db,self.all_set_names)
            self.role_selector.load_priority_config()
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
                with open(CONFIG_DIR/"roles.json","w",encoding="utf-8") as f:
                    json.dump(self.roles_db,f,ensure_ascii=False,indent=4)
            except Exception as e:
                logger.warning(f"默认套装名已在内存中修正，但写回 roles.json 失败: {e}")

    def _update_inventory_status(self):
        if not OUTPUT_FILE.exists():
            self.status_lbl.setText("库存为空")
            self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")
            return
        try:
            with open(OUTPUT_FILE,"r",encoding="utf-8") as f:
                data=json.load(f)
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
    def _page_execute(self):
        page=QWidget(); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(12)

        c1=self._card("第一步 · 扫描模式")
        self.scan_group=QButtonGroup()
        for val,text in [("4","直接读取库存 — 跳过扫描（最快，推荐）"),
                         ("3","离线解析 — 读取 scanned_images/ → 分配"),
                         ("2","增量扫描 — 自动/半自动截图 → 解析"),
                         ("1","全量扫描 — 手柄遍历截图 → 解析")]:
            row=QHBoxLayout(); row.setSpacing(6)
            rb=QRadioButton(text); rb.setChecked(val=="4"); self.scan_group.addButton(rb,int(val)); row.addWidget(rb)
            hb=QPushButton("?"); hb.setObjectName("btnHelp")
            hb.clicked.connect(lambda checked,v=val: _show_help(self,"扫描模式说明",SCAN_HELP.get(v,"")))
            row.addWidget(hb); row.addStretch(); c1.layout().addLayout(row)

        self.total_count_frame=QWidget(); self.total_count_frame.setVisible(False)
        tcl=QHBoxLayout(self.total_count_frame); tcl.setContentsMargins(28,4,0,4); tcl.setSpacing(8)
        tcl.addWidget(QLabel("库存数量:"))
        self.total_count_edit=QLineEdit()
        self.total_count_edit.setPlaceholderText("请输入当前库存数量")
        self.total_count_edit.setValidator(QIntValidator(1,2000,self.total_count_edit))
        self.total_count_edit.setMaximumWidth(180)
        tcl.addWidget(self.total_count_edit)
        tcl.addStretch()
        c1.layout().addWidget(self.total_count_frame)

        self.drone_frame=QWidget(); self.drone_frame.setVisible(False)
        dl=QHBoxLayout(self.drone_frame); dl.setContentsMargins(28,4,0,4)
        dl.addWidget(QLabel("无人机模式:")); self.drone_group=QButtonGroup()
        for val,text in [("2","半自动模式（推荐）"),("1","全自动模式")]:
            sub_row=QHBoxLayout(); sub_row.setSpacing(6)
            rb=QRadioButton(text); rb.setChecked(val=="2"); self.drone_group.addButton(rb,int(val)); sub_row.addWidget(rb)
            hb=QPushButton("?"); hb.setObjectName("btnHelp")
            hb.clicked.connect(lambda checked,v=val: _show_help(self,"无人机模式说明",DRONE_HELP.get(v,"")))
            sub_row.addWidget(hb); dl.addLayout(sub_row)
        dl.addStretch(); c1.layout().addWidget(self.drone_frame)
        self.scan_group.idToggled.connect(self._on_scan_change); l.addWidget(c1)

        c2=self._card("第二步 · 角色优先级配置")
        self.role_selector=RoleSelector(); self.role_selector.orderChanged.connect(self._on_priority_changed); c2.layout().addWidget(self.role_selector); l.addWidget(c2)

        c3=self._card("第三步 · 分配策略")
        self.strategy_group=QButtonGroup()
        for i,text in enumerate(["角色优先 — 保证主C极品，副C次之",
                                  "驱动优先 — 极品贪心反选，让好装备都有归宿",
                                  "全局最优 — 匈牙利算法，追求全队总分最大化",
                                  "增量更新 — 锁定已穿戴，仅用闲置装备配装"]):
            rb=QRadioButton(text); rb.setChecked(i==0); self.strategy_group.addButton(rb,i); c3.layout().addWidget(rb)
        l.addWidget(c3)

        self.btn_run=QPushButton("⚡  开始执行"); self.btn_run.setObjectName("btnPrimary"); self.btn_run.setFixedHeight(46)
        self.btn_run.setStyleSheet("#btnPrimary{font-size:15px;font-weight:700;border-radius:10px}"); self.btn_run.clicked.connect(self._do_exec); l.addWidget(self.btn_run)

        self.result_card=QWidget(); self.result_card.setVisible(False)
        self.result_card.setStyleSheet("QWidget{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:16px}")
        rl=QVBoxLayout(self.result_card); rh=QHBoxLayout(); rh.addWidget(QLabel("计算结果")); rh.addStretch()
        self.btn_save=QPushButton("保存装备锁定"); self.btn_save.setObjectName("btnAction"); self.btn_save.clicked.connect(self._save_alloc); rh.addWidget(self.btn_save)
        rl.addLayout(rh); self.result_content=QWidget(); self.result_content_layout=QVBoxLayout(self.result_content); rl.addWidget(self.result_content)
        l.addWidget(self.result_card); l.addStretch(); return scroll

    def _on_scan_change(self,id):
        self.total_count_frame.setVisible(id==1)
        self.drone_frame.setVisible(id==2)
    def _on_priority_changed(self):
        self.role_selector.save_priority_config()

    def _do_exec(self):
        sel=self.role_selector.get_selected()
        sm=str(self.scan_group.checkedId())
        parse_only=(not sel and sm in ("1","2"))
        if not sel and not parse_only:
            QMessageBox.warning(self,"提示","请先选择目标角色！"); return
        total_drives=None
        if sm=="1":
            raw_count=self.total_count_edit.text().strip()
            if not raw_count:
                QMessageBox.warning(self,"提示","全量扫描前请先填写库存数量。")
                return
            total_drives=int(raw_count)
            if not 0 < total_drives <= 2000:
                QMessageBox.warning(self,"提示","库存数量必须在 1-2000 之间。")
                return
        if parse_only:
            QMessageBox.information(self,"仅生成库存数据","当前未选择任何角色，本次扫描解析只会生成 real_inventory.json，不会进行配装计算。")
        self.btn_run.setEnabled(False); self.btn_run.setText("⏳  执行中..."); self.result_card.setVisible(False)
        strat=["role_priority","drive_priority","global_optimal","update_mode"][max(0,min(3,self.strategy_group.checkedId()))]
        cs=self.role_selector.get_custom_sets()
        self._pending_strat=strat; self._pending_sel=sel; self._pending_cs=cs
        self._pending_archive_paths=[]
        self._pending_parse_only=parse_only

        if sm=="3":
            self._start_vision_processing()
        elif sm=="2":
            drone_mode="auto" if self.drone_group.checkedId()==1 else "semi"
            self._start_scan(drone_mode)
        elif sm=="1":
            self._start_gamepad_scan(total_drives)
        else:
            self._worker=WorkerThread(target=lambda:self._run_allocation(strat,sel,cs),parent=self)
            self._worker.result_ready.connect(self._on_done); self._worker.error.connect(self._on_exec_error); self._worker.start()

    def _start_vision_processing(self, replace_output=False):
        input_dir=str(SCREENSHOT_DIR)
        output_file=str(OUTPUT_FILE)
        self._pending_archive_paths=[]
        self._vision_worker=VisionWorkerThread(input_dir,output_file,self,replace_output=replace_output)
        self._progress_dlg=QProgressDialog("正在解析截图...","取消",0,100,self)
        self._progress_dlg.setWindowTitle("截图解析进度")
        self._progress_dlg.setMinimumWidth(400)
        self._progress_dlg.setAutoClose(False)
        self._progress_dlg.setAutoReset(False)
        self._progress_dlg.canceled.connect(self._on_vision_cancel)
        self._progress_dlg.show()
        self._vision_worker.progress.connect(self._on_vision_progress)
        self._vision_worker.processing_done.connect(self._on_vision_done)
        self._vision_worker.canceled.connect(self._on_vision_canceled)
        self._vision_worker.error.connect(self._on_vision_error)
        self._vision_worker.start()

    def _on_vision_progress(self,current,total,filename):
        self._progress_dlg.setMaximum(total)
        self._progress_dlg.setValue(current)
        self._progress_dlg.setLabelText(f"正在解析 ({current}/{total}): {filename}")

    def _on_vision_done(self,image_paths):
        self._pending_archive_paths=list(image_paths or [])
        logger.info("视觉解析线程完成，准备启动分配计算...")
        if hasattr(self, '_progress_dlg') and self._progress_dlg:
            self._progress_dlg.close()
        if hasattr(self,'_vision_worker') and self._vision_worker.isRunning():
            self._vision_worker.wait(5000)
        if getattr(self,"_pending_parse_only",False):
            self._pending_archive_paths=[]
            self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
            self._update_inventory_status()
            QMessageBox.information(self,"库存数据已生成","本次未配置角色优先级，已仅生成/更新 real_inventory.json，未进行配装计算。")
            self._pending_parse_only=False
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._start_allocation_worker)

    def _start_allocation_worker(self):
        logger.info("启动分配工作线程...")
        self._worker=WorkerThread(target=lambda:self._run_allocation(self._pending_strat,self._pending_sel,self._pending_cs),parent=self)
        self._worker.result_ready.connect(self._on_done); self._worker.error.connect(self._on_exec_error); self._worker.start()
        logger.info("分配线程已启动")

    def _on_vision_error(self,err):
        self._progress_dlg.close()
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
        self._pending_parse_only=False
        QMessageBox.critical(self,"解析失败",f"截图解析出错:\n{err}")

    def _on_vision_cancel(self):
        if hasattr(self,'_vision_worker') and self._vision_worker.isRunning():
            self._vision_worker.request_cancel()
            self._progress_dlg.setCancelButton(None)
            self._progress_dlg.setLabelText("正在取消解析，等待当前截图处理完成...")
            return
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")

    def _on_vision_canceled(self,count):
        if hasattr(self, '_progress_dlg') and self._progress_dlg:
            self._progress_dlg.close()
        self.btn_run.setEnabled(True); self.btn_run.setText("开始执行")
        self._pending_parse_only=False
        QMessageBox.information(self,"解析已取消",f"已停止继续解析，本次已入库 {count} 张截图。")

    def _start_scan(self,drone_mode):
        self.showMinimized()
        self._scan_worker=ScanWorkerThread(mode=drone_mode,parent=self)
        self._scan_worker.scan_done.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._register_scan_hotkeys(drone_mode)
        self.btn_run.setText("⏳  扫描中... (F12 停止)")
        self._scan_worker.start()

    def _start_gamepad_scan(self,total_drives):
        self._replace_inventory_on_next_parse=True
        QMessageBox.information(
            self,
            "全量扫描准备",
            "点击 OK 后程序会最小化并准备开始全量扫描。\n\n"
            "请切换至游戏的驱动仓库页面，并确保当前选中第一排第一个驱动。\n"
            "程序会在短暂倒计时后接管虚拟手柄进行遍历截图。"
        )
        self.showMinimized()
        self._gamepad_worker=GamepadScanWorkerThread(total_drives=total_drives,parent=self)
        self._gamepad_worker.scan_done.connect(self._on_scan_done)
        self._gamepad_worker.error.connect(self._on_gamepad_error)
        self._register_scan_hotkeys("gamepad")
        self.btn_run.setText("⏳  手柄扫描中... (F12 停止)")
        self._gamepad_worker.start()

    def _register_scan_hotkeys(self, mode):
        """启动热键监听线程"""
        self._hk_mode=mode
        self._hk_active=True
        import threading
        self._hk_thread=threading.Thread(target=self._hotkey_poll_loop, daemon=True)
        self._hk_thread.start()

    def _hotkey_poll_loop(self):
        """后台轮询线程，监听全局热键"""
        import keyboard as kb
        import time
        while self._hk_active:
            try:
                if kb.is_pressed(self._hk_stop.lower()):
                    self._on_hk_stop()
                    time.sleep(0.5)
                if self._hk_mode in ("semi","identify"):
                    if kb.is_pressed(self._hk_capture.lower()):
                        self._on_hk_capture()
                        time.sleep(0.3)
                    if kb.is_pressed(self._hk_finish.lower()):
                        self._on_hk_finish()
                        time.sleep(0.5)
            except: pass
            time.sleep(0.05)

    def _unregister_scan_hotkeys(self):
        self._hk_active=False

    def _on_hk_stop(self):
        w=getattr(self,'_scan_worker',None) or getattr(self,'_gamepad_worker',None)
        if w and w.scanner:
            w.scanner._stopped=True
            w.scanner._finish_flag=True

    def _on_hk_capture(self):
        if getattr(self,"_hk_mode",None)=="identify":
            self._capture_identify_foreground()
            return
        w=getattr(self,'_scan_worker',None)
        if w and w.scanner: w.scanner._capture_flag=True

    def _on_hk_finish(self):
        if getattr(self,"_hk_mode",None)=="identify":
            self.identify_capture_done_signal.emit()
            return
        w=getattr(self,'_scan_worker',None)
        if w and w.scanner: w.scanner._finish_flag=True

    def _on_gamepad_error(self,err):
        self._unregister_scan_hotkeys()
        self._replace_inventory_on_next_parse=False
        self.showNormal(); self.activateWindow()
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
        self._pending_parse_only=False
        QMessageBox.critical(self,"手柄扫描失败",f"全量扫描出错:\n{err}")

    def _on_scan_done(self,count):
        self._unregister_scan_hotkeys()
        self.showNormal(); self.activateWindow()
        if count>0:
            replace_output=getattr(self,"_replace_inventory_on_next_parse",False)
            self._replace_inventory_on_next_parse=False
            self._start_vision_processing(replace_output=replace_output)
        else:
            self._replace_inventory_on_next_parse=False
            self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
            self._pending_parse_only=False
            QMessageBox.information(self,"扫描完成","未捕获到新装备，无需解析。")

    def _on_scan_error(self,err):
        self._unregister_scan_hotkeys()
        self.showNormal(); self.activateWindow()
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
        self._pending_parse_only=False
        QMessageBox.critical(self,"扫描失败",f"扫描出错:\n{err}")

    def _run_allocation(self,strat,sel,cs):
        try:
            logger.info(f"开始分配计算: 策略={strat}, 角色={sel}")
            a=NTEAppFacade()
            fp,_=a.execute_allocation(str(OUTPUT_FILE),sel,cs,strat)
            logger.info(f"分配计算完成: result_type={type(fp).__name__}")
            return fp
        except Exception as e:
            import traceback as tb
            logger.error(f"_run_allocation 内部异常: {e}\n{tb.format_exc()}")
            raise

    def _on_done(self,r):
        try:
            logger.info(f"_on_done 收到结果: type={type(r).__name__}, keys={list(r.keys()) if isinstance(r,dict) else 'N/A'}")
            self.final_plan=r; self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
            if r is None: QMessageBox.warning(self,"提示","计算失败，请检查库存文件是否存在。"); return
            self._render_results(r)
            logger.info("_render_results 完成")
        except Exception as e:
            import traceback as tb
            logger.error(f"_on_done 异常: {e}\n{tb.format_exc()}")
            QMessageBox.critical(self,"渲染失败",f"{e}")

    def _on_exec_error(self,err):
        self.btn_run.setEnabled(True); self.btn_run.setText("⚡  开始执行")
        QMessageBox.critical(self,"执行失败",f"发生错误:\n{err}")

    def _render_results(self,plan):
        if not plan: return
        self.result_card.setVisible(True)
        while self.result_content_layout.count():
            it=self.result_content_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        mode_labels={"role_priority":"角色优先","drive_priority":"驱动优先","global_optimal":"全局最优","update_mode":"增量更新"}
        mode_name=mode_labels.get(getattr(self,'_pending_strat',''),'')
        for role,p in plan.items():
            if not p or not p.get("valid"):
                self.result_content_layout.addWidget(QLabel(f"❌ {role}: 无有效配装方案")); continue
            total_score=p.get('score',0); total_grade=self._calc_grade(total_score,20)
            gc=GRADE_COLORS.get(total_grade,"#58a6ff"); gbg=GRADE_BGS.get(total_grade,f"{gc}15")

            grp=QGroupBox(""); gl=QVBoxLayout(grp)
            # Role header: name + score + grade side by side, compact
            role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
            # Role name with different color from stat blocks - use teal/cyan tone
            rnl=QLabel(role)
            rnl.setStyleSheet("font-size:14px;font-weight:700;color:#4dd0e1;border:1px solid #4dd0e1;border-radius:6px;padding:2px 12px;background:#4dd0e122")
            role_hdr.addWidget(rnl)
            if mode_name:
                ml=QLabel(mode_name); ml.setStyleSheet("font-size:10px;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:1px 6px")
                role_hdr.addWidget(ml)
            role_hdr.addStretch()
            # Score badge (separate)
            sf=QFrame()
            sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
            slb=QHBoxLayout(sf); slb.setSpacing(6); slb.setContentsMargins(4,0,4,0)
            sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:14px;font-weight:700;color:{gc};border:none")
            slb.addWidget(QLabel("评分")); slb.addWidget(sv)
            role_hdr.addWidget(sf)
            # Grade badge (separate)
            gf=QFrame()
            gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
            glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
            gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:14px;font-weight:700;color:{gc};border:none")
            glb.addWidget(QLabel("评级")); glb.addWidget(gv)
            role_hdr.addWidget(gf)
            gl.addLayout(role_hdr); gl.addSpacing(6)

            board=p.get("blueprint",{}).get("board",[])
            if board: gl.addWidget(QLabel("拼图图纸:")); gl.addWidget(PuzzleBoardWidget(board)); gl.addSpacing(8)
            wts=self.roles_db.get(role,{}).get("weights",{})

            tape=p.get("assigned_tape")
            if tape:
                t_score=tape.role_scores.get(role,0) if hasattr(tape,'role_scores') else 0
                t_grade=self._calc_grade(t_score,15)
                gl.addWidget(QLabel("卡带:"))
                gl.addWidget(self._equip_card(tape.set_name,tape.main_stats,tape.sub_stats,None,tape.uid,wts,(t_score,t_grade),tape.quality))

            drives=p.get("assigned_set_drives",[])+p.get("assigned_extra_drives",[])
            if drives:
                gl.addWidget(QLabel(f"⚙ 驱动 ({len(drives)}个):"))
                for d in drives:
                    score=d.role_scores.get(role,0) if hasattr(d,'role_scores') else 0
                    grade=self._calc_grade(score,d.area)
                    mvp_tag=f" 👑第{d.pick_order}顺位" if getattr(d,'is_mvp',False) else ""
                    gl.addWidget(self._equip_card(d.shape_id,"",d.sub_stats,d.shape_id,d.uid+mvp_tag,wts,(score,grade),d.quality))
            self.result_content_layout.addWidget(grp)
        self.result_content_layout.addStretch()

    def _calc_grade(self, score, area):
        max_score = area * 10.0
        if max_score == 0: return "D"
        ratio = score / max_score
        if ratio >= 0.8: return "ACE"
        elif ratio >= 0.7: return "SSS"
        elif ratio >= 0.6: return "SS"
        elif ratio >= 0.5: return "S"
        elif ratio >= 0.4: return "A"
        elif ratio >= 0.3: return "B"
        elif ratio >= 0.2: return "C"
        return "D"

    def _stat_w(self,sn,wts):
        if not wts: return 0.3
        if sn in wts: return wts[sn]
        for k,v in wts.items():
            if k.replace("%","")==sn.replace("%","") or sn.replace("%","")==k.replace("%",""): return v
            if k in sn or sn in k: return v
        return 0.2

    def _stat_c(self,w):
        w=max(0.0,min(1.0,w))
        if w<0.3: return "#8b949e"
        if w<0.5: return "#58a6ff"
        if w<0.7: return "#56d364"
        if w<0.85: return "#d2991d"
        return "#f0883e"

    def _weighted_score(self,sub_stats,wts):
        if not sub_stats: return 0
        total=0.0
        for sn,sv in sub_stats.items():
            sw=self._stat_w(sn,wts)
            total+=float(sv)*sw
        return total

    def _score_drive_dict(self, sub_stats, shape_id, weights, quality="Gold"):
        if not self.scoring_engine: return 0.0
        se=self.scoring_engine
        max_w=se._get_max_theoretical_weight(weights)
        area=self._shape_areas.get(shape_id, 3)
        actual_w=sum(se._get_flexible_weight(sn, weights) for sn in sub_stats.keys())
        if actual_w<=0 or max_w<=0: return 0.0
        quality_coef=se.quality_map.get(quality, 1.0)
        return round((10.0/max_w)*actual_w*area*quality_coef, 2)

    def _score_tape_dict(self, main_stats, sub_stats, weights, quality="Gold"):
        if not self.scoring_engine: return 0.0
        se=self.scoring_engine
        max_w=se._get_max_theoretical_weight(weights)
        quality_coef=se.quality_map.get(quality, 1.0)
        main_w=se._get_flexible_weight(main_stats, weights) if main_stats else 0
        main_score=main_w*50.0*quality_coef
        sub_w=sum(se._get_flexible_weight(sn, weights) for sn in sub_stats.keys())
        sub_score=(10.0/max_w)*sub_w*10.0*quality_coef if max_w>0 else 0
        return round(main_score+sub_score, 2)

    def _equip_card(self,label,main_stat,sub_stats,shape_id,uid,weights,score_info=None,quality=None):
        QUALITY_COLORS={"Gold":"#ffd700","Purple":"#ffe082","Blue":"#58a6ff"}
        QUALITY_LABELS={"Gold":"金","Purple":"紫","Blue":"蓝"}
        QUALITY_BGS={"Gold":"#332600","Purple":"#6f2dbd","Blue":"#0d2748"}
        w=QWidget(); w.setStyleSheet("QWidget{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:6px 10px;margin:2px 0}")
        outer=QHBoxLayout(w); outer.setSpacing(10); outer.setContentsMargins(0,0,0,0)

        # Shape image (compact)
        if shape_id:
            pm=_get_shape_pixmap(shape_id,52,quality)
            if not pm.isNull():
                img_lbl=QLabel(); img_lbl.setPixmap(pm); img_lbl.setFixedSize(56,56); img_lbl.setScaledContents(True)
                img_lbl.setStyleSheet("border:1px solid #30363d;border-radius:6px;background:#161b22"); outer.addWidget(img_lbl)

        inner=QVBoxLayout(); inner.setSpacing(3); inner.setContentsMargins(0,2,0,2)

        # Header: shape name + quality + main stat block + score|grade
        hdr=QHBoxLayout(); hdr.setSpacing(8)
        # Tape label = bright pink, Drive label = cyan
        label_color = "#f48fb1" if not shape_id else "#4dd0e1"
        name_lbl = QLabel(f"<b>{label}</b>")
        name_lbl.setStyleSheet(f"font-size:12px;font-weight:700;color:{label_color};border:1px solid {label_color};border-radius:5px;padding:1px 8px;background:{label_color}15")
        hdr.addWidget(name_lbl)
        # Quality badge: only tapes show text; drive quality is represented by the icon.
        if quality and not shape_id:
            qcolor=QUALITY_COLORS.get(quality,"#8b949e"); qlabel=QUALITY_LABELS.get(quality,quality)
            qbg=QUALITY_BGS.get(quality,f"{qcolor}15")
            q_lbl=QLabel(qlabel)
            q_lbl.setStyleSheet(f"font-size:10px;font-weight:700;color:{qcolor};border:1px solid {qcolor};border-radius:4px;padding:1px 6px;background:{qbg}")
            hdr.addWidget(q_lbl)
        # Main stat as colored block (same style as sub stats)
        if main_stat:
            mw=self._stat_w(main_stat,weights); mc=self._stat_c(mw); qc=QColor(mc)
            ms_block=QLabel(main_stat); ms_block.setStyleSheet(
                f"border:1px solid {mc};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);"
                f"border-radius:4px;padding:2px 8px;font-size:11px;color:{mc};font-weight:600"
            )
            hdr.addWidget(ms_block)
        hdr.addStretch()

        # Score | Grade side by side
        if score_info is not None:
            score,grade=score_info; gc=GRADE_COLORS.get(grade,"#58a6ff")
            sf=QFrame()
            sf.setStyleSheet(f"QFrame{{background:{gc}15;border:1px solid {gc};border-radius:5px;padding:1px 8px}}")
            sf_layout=QHBoxLayout(sf); sf_layout.setSpacing(4); sf_layout.setContentsMargins(3,0,3,0)
            sl=QLabel(f"{score:.1f}"); sl.setStyleSheet(f"font-size:12px;font-weight:700;color:{gc};border:none"); sf_layout.addWidget(sl)
            gl=QLabel(grade); gl.setStyleSheet(f"font-size:10px;font-weight:700;color:{gc};border:none"); sf_layout.addWidget(gl)
            hdr.addWidget(sf)
        uid_lbl=QLabel(f"<span style='color:#6e7681;font-size:9px;'>{uid}</span>"); hdr.addWidget(uid_lbl)
        inner.addLayout(hdr)

        # Stat blocks row
        if sub_stats:
            br=QHBoxLayout(); br.setSpacing(5)
            for sn,sv in sub_stats.items():
                sw=self._stat_w(sn,weights); color=self._stat_c(sw); qc=QColor(color)
                block=QLabel(f"{sn} <b>{sv}</b>"); block.setAlignment(Qt.AlignCenter)
                block.setStyleSheet(f"border:1px solid {color};background:rgba({qc.red()},{qc.green()},{qc.blue()},0.12);border-radius:4px;padding:3px 8px;font-size:10px;color:{color}")
                block.setToolTip(f"权重: {sw:.2f}"); br.addWidget(block)
            br.addStretch(); inner.addLayout(br)
        outer.addLayout(inner,1); return w

    def _save_alloc(self):
        if not self.final_plan: return
        try:
            self.state_mgr.save_allocation(self.final_plan, mode=getattr(self,'_pending_strat',''))
            archived_count=self._archive_pending_screenshots()
            self._load_data()
            msg="装备锁定已保存！"
            if archived_count:
                msg+=f"\n已归档 {archived_count} 张截图。"
            QMessageBox.information(self,"保存",msg)
        except Exception as e: QMessageBox.critical(self,"失败",str(e))

    def _archive_pending_screenshots(self):
        paths=list(getattr(self,'_pending_archive_paths',[]) or [])
        if not paths:
            return 0
        archive_dir=SCREENSHOT_DIR/"archive"
        archive_dir.mkdir(parents=True,exist_ok=True)
        archived_count=0
        for src in paths:
            src_path=Path(src)
            if not src_path.exists():
                continue
            dst=archive_dir/src_path.name
            base=dst.with_suffix("")
            ext=dst.suffix
            suffix=1
            while dst.exists():
                dst=Path(f"{base}_{suffix}{ext}")
                suffix+=1
            shutil.move(str(src_path),str(dst))
            archived_count+=1
        self._pending_archive_paths=[]
        if archived_count:
            logger.success(f"已归档 {archived_count} 张已保存配装的截图。")
        return archived_count

    # ── Page: Equipment
    def _page_equipment(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(8)
        sh=QHBoxLayout(); sh.addWidget(QLabel("搜索"))
        self.equip_search=QLineEdit(); self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）..."); self.equip_search.setClearButtonEnabled(True)
        self.equip_search.textChanged.connect(self._refresh_equip); sh.addWidget(self.equip_search); l.addLayout(sh)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        self.equip_content=QWidget(); self.equip_content_layout=QVBoxLayout(self.equip_content); scroll.setWidget(self.equip_content)
        l.addWidget(scroll,1); return page

    def _refresh_equip(self):
        while self.equip_content_layout.count():
            it=self.equip_content_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        eq=self.equipped_state; all_roles=sorted(eq.keys())
        filt=self.equip_search.text().strip() if hasattr(self,'equip_search') else ""
        shown=0
        for role_name in all_roles:
            if filt and not _match_pinyin(role_name,filt): continue
            rd=eq.get(role_name,{})
            if not isinstance(rd,dict): continue
            shown+=1; wts=self.roles_db.get(role_name,{}).get("weights",{})

            total_score=0.0; total_area=0
            tape_data=rd.get("equipped_tape")
            if tape_data:
                t_q=tape_data.get("quality","Gold")
                t_s=self._score_tape_dict(tape_data.get("main_stats",""),tape_data.get("sub_stats",{}),wts,t_q)
                total_score+=t_s; total_area+=15
            for d in rd.get("equipped_drives",[]):
                d_area=self._shape_areas.get(d.get("shape_id",""),3)
                d_q=d.get("quality","Gold")
                d_s=self._score_drive_dict(d.get("sub_stats",{}),d.get("shape_id",""),wts,d_q)
                total_score+=d_s; total_area+=d_area
            total_grade=self._calc_grade(total_score,total_area)
            gc=GRADE_COLORS.get(total_grade,"#58a6ff"); gbg=GRADE_BGS.get(total_grade,f"{gc}15")

            grp=QGroupBox(""); gl=QVBoxLayout(grp)
            role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
            rnl=QLabel(role_name)
            rnl.setStyleSheet(f"font-size:14px;font-weight:700;color:#4dd0e1;border:1px solid #4dd0e1;border-radius:6px;padding:2px 12px;background:#4dd0e122")
            role_hdr.addWidget(rnl)
            _sm=rd.get("strategy_mode","")
            if _sm:
                _ml={"role_priority":"角色优先","drive_priority":"驱动优先","global_optimal":"全局最优","update_mode":"增量更新"}.get(_sm,_sm)
                sml=QLabel(_ml); sml.setStyleSheet("font-size:10px;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:1px 6px")
                role_hdr.addWidget(sml)
            role_hdr.addStretch()
            # Score
            sf=QFrame()
            sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
            slb=QHBoxLayout(sf); slb.setSpacing(6); slb.setContentsMargins(4,0,4,0)
            sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:14px;font-weight:700;color:{gc};border:none")
            slb.addWidget(QLabel("评分")); slb.addWidget(sv); role_hdr.addWidget(sf)
            # Grade
            gf=QFrame()
            gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:6px;padding:2px 10px}}")
            glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
            gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:14px;font-weight:700;color:{gc};border:none")
            glb.addWidget(QLabel("评级")); glb.addWidget(gv); role_hdr.addWidget(gf)
            gl.addLayout(role_hdr); gl.addSpacing(6)

            bp=rd.get("blueprint_layout",[])
            if bp: gl.addWidget(QLabel("拼图图纸:")); gl.addWidget(PuzzleBoardWidget(bp))
            if tape_data:
                t_q=tape_data.get("quality","Gold")
                t_s=self._score_tape_dict(tape_data.get("main_stats",""),tape_data.get("sub_stats",{}),wts,t_q)
                t_g=self._calc_grade(t_s,15)
                gl.addWidget(QLabel("卡带:"))
                gl.addWidget(self._equip_card(tape_data.get("set_name",""),tape_data.get("main_stats",""),tape_data.get("sub_stats",{}),None,tape_data.get("uid",""),wts,(t_s,t_g),t_q))
            drives=rd.get("equipped_drives",[])
            if drives:
                gl.addWidget(QLabel(f"⚙ 驱动 ({len(drives)}个):"))
                for d in drives:
                    d_q=d.get("quality","Gold")
                    d_s=self._score_drive_dict(d.get("sub_stats",{}),d.get("shape_id",""),wts,d_q)
                    d_g=self._calc_grade(d_s,self._shape_areas.get(d.get("shape_id",""),3))
                    gl.addWidget(self._equip_card(d.get("shape_id",""),"",d.get("sub_stats",{}),d.get("shape_id",""),d.get("uid",""),wts,(d_s,d_g),d_q))
            self.equip_content_layout.addWidget(grp)
        if shown==0:
            ph=QLabel("暂无已保存的配装。请先执行分配并保存。"); ph.setStyleSheet("color:#6e7681;padding:24px"); ph.setAlignment(Qt.AlignCenter); self.equip_content_layout.addWidget(ph)
        self.equip_content_layout.addStretch()

    def _save_eq(self):
        with open(USER_CONFIG_DIR/"equipped_state.json","w",encoding="utf-8") as f: json.dump(self.equipped_state,f,ensure_ascii=False,indent=4); logger.success("装备状态已保存")

    # ── Page: Identify
    def _page_identify(self):
        page=QWidget(); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(12)

        c1=self._card("快速鉴定")
        type_row=QHBoxLayout(); type_row.setSpacing(12)
        type_row.addWidget(QLabel("装备类型"))
        self.ident_type_group=QButtonGroup(self)
        self.ident_drive_rb=QRadioButton("驱动块"); self.ident_tape_rb=QRadioButton("卡带")
        self.ident_drive_rb.setChecked(True)
        self.ident_type_group.addButton(self.ident_drive_rb,0); self.ident_type_group.addButton(self.ident_tape_rb,1)
        self.ident_type_group.buttonToggled.connect(lambda *_: self._on_identify_type_changed())
        type_row.addWidget(self.ident_drive_rb); type_row.addWidget(self.ident_tape_rb)
        type_row.addSpacing(18); type_row.addWidget(QLabel("品质"))
        self.ident_quality_combo=QComboBox()
        for label,value in [("金色","Gold"),("紫色","Purple"),("蓝色","Blue")]:
            self.ident_quality_combo.addItem(label,value)
        type_row.addWidget(self.ident_quality_combo); type_row.addStretch(); c1.layout().addLayout(type_row)

        self.ident_shape_row=QWidget(); sr=QHBoxLayout(self.ident_shape_row); sr.setContentsMargins(0,0,0,0)
        sr.addWidget(QLabel("驱动形状")); self.ident_shape_combo=SearchableComboBox(); sr.addWidget(self.ident_shape_combo,1); c1.layout().addWidget(self.ident_shape_row)

        self.ident_tape_row=QWidget(); tr=QHBoxLayout(self.ident_tape_row); tr.setContentsMargins(0,0,0,0); tr.setSpacing(8)
        tr.addWidget(QLabel("卡带套装")); self.ident_set_combo=SearchableComboBox(); tr.addWidget(self.ident_set_combo,1)
        tr.addWidget(QLabel("主词条")); self.ident_main_combo=SearchableComboBox(); tr.addWidget(self.ident_main_combo,1); c1.layout().addWidget(self.ident_tape_row)

        path_row=QHBoxLayout(); path_row.setSpacing(8)
        self.ident_path_edit=QLineEdit(); self.ident_path_edit.setPlaceholderText("图片路径；多个图片可用分号分隔")
        self.ident_path_edit.textChanged.connect(self._refresh_identify_previews)
        path_row.addWidget(self.ident_path_edit,1)
        choose_btn=QPushButton("选择图片"); choose_btn.clicked.connect(self._identify_choose_file); path_row.addWidget(choose_btn)
        paste_btn=QPushButton("粘贴"); paste_btn.clicked.connect(self._identify_from_clipboard); path_row.addWidget(paste_btn)
        self.ident_parse_btn=QPushButton("解析图片"); self.ident_parse_btn.setObjectName("btnPrimary"); self.ident_parse_btn.clicked.connect(self._identify_from_image_path); path_row.addWidget(self.ident_parse_btn)
        capture_btn=QPushButton("截图鉴定"); capture_btn.clicked.connect(self._start_identify_capture_mode); path_row.insertWidget(path_row.count()-1,capture_btn)
        self.ident_parse_btn.setVisible(False)
        c1.layout().addLayout(path_row)

        self.ident_preview_scroll=QScrollArea()
        self.ident_preview_scroll.setWidgetResizable(True)
        self.ident_preview_scroll.setFixedHeight(106)
        self.ident_preview_widget=QWidget()
        self.ident_preview_layout=QHBoxLayout(self.ident_preview_widget)
        self.ident_preview_layout.setContentsMargins(4,4,4,4)
        self.ident_preview_layout.setSpacing(8)
        self.ident_preview_scroll.setWidget(self.ident_preview_widget)
        self.ident_preview_scroll.setVisible(False)
        c1.layout().addWidget(self.ident_preview_scroll)

        self.ident_manual_text=PlainTextOnlyTextEdit()
        self.ident_manual_text.setAcceptDrops(False)
        self.ident_manual_text.setPlaceholderText("手动输入副词条，每行一条，例如：暴击率 1.0%")
        self.ident_manual_text.setFixedHeight(108)
        c1.layout().addWidget(self.ident_manual_text)
        btn_row=QHBoxLayout(); btn_row.addStretch()
        clear_btn=QPushButton("清空"); clear_btn.clicked.connect(self._clear_identify_input); btn_row.addWidget(clear_btn)
        self.ident_manual_btn=QPushButton("开始鉴定"); self.ident_manual_btn.setObjectName("btnPrimary"); self.ident_manual_btn.clicked.connect(self._identify_start); btn_row.addWidget(self.ident_manual_btn)
        c1.layout().addLayout(btn_row); l.addWidget(c1)

        c2=self._card("鉴定结果")
        self.ident_summary=QLabel("等待输入装备数据")
        self.ident_summary.setStyleSheet("color:#8b949e")
        c2.layout().addWidget(self.ident_summary)
        self.ident_result_widget=QWidget(); self.ident_result_layout=QVBoxLayout(self.ident_result_widget); self.ident_result_layout.setContentsMargins(0,0,0,0); self.ident_result_layout.setSpacing(8)
        c2.layout().addWidget(self.ident_result_widget); l.addWidget(c2); l.addStretch()

        self._on_identify_type_changed()
        return scroll

    def _refresh_identify_options(self):
        if not hasattr(self,"ident_shape_combo"):
            return

        current_shape=self.ident_shape_combo.currentData()
        self.ident_shape_combo.blockSignals(True); self.ident_shape_combo.clear()
        for sid in sorted([s for s in self._shape_areas.keys() if s!="TAPE_15"], key=lambda x:(self._shape_areas.get(x,0),x)):
            self.ident_shape_combo.addItem(f"{sid} ({self._shape_areas.get(sid,0)}格)",sid)
        idx=self.ident_shape_combo.findData(current_shape)
        if idx>=0: self.ident_shape_combo.setCurrentIndex(idx)
        self.ident_shape_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_shape_combo)

        current_set=self.ident_set_combo.currentData()
        self.ident_set_combo.blockSignals(True); self.ident_set_combo.clear()
        for set_name in self.all_set_names:
            self.ident_set_combo.addItem(set_name,set_name)
        idx=self.ident_set_combo.findData(current_set)
        if idx>=0: self.ident_set_combo.setCurrentIndex(idx)
        self.ident_set_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_set_combo)

        current_main=self.ident_main_combo.currentData()
        self.ident_main_combo.blockSignals(True); self.ident_main_combo.clear()
        for stat_name in self._get_tape_main_stats_pool():
            self.ident_main_combo.addItem(stat_name,stat_name)
        idx=self.ident_main_combo.findData(current_main)
        if idx>=0: self.ident_main_combo.setCurrentIndex(idx)
        self.ident_main_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_main_combo)

    def _on_identify_type_changed(self):
        is_tape=hasattr(self,"ident_tape_rb") and self.ident_tape_rb.isChecked()
        if hasattr(self,"ident_shape_row"): self.ident_shape_row.setVisible(not is_tape)
        if hasattr(self,"ident_tape_row"): self.ident_tape_row.setVisible(is_tape)

    def _get_tape_main_stats_pool(self):
        try:
            with open(CONFIG_DIR/"stats.json","r",encoding="utf-8") as f:
                return json.load(f).get("tape_main_stats_pool",[])
        except Exception:
            return []

    def _set_combo_data(self,combo,value):
        if value is None: return
        if isinstance(combo,SearchableComboBox):
            combo._restore_items()
        idx=combo.findData(value)
        if idx<0: idx=combo.findText(str(value))
        if idx>=0: combo.setCurrentIndex(idx)

    def _make_combo_searchable(self,combo):
        if isinstance(combo,SearchableComboBox):
            combo.refresh_search_items()
            return
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        items=[combo.itemText(i) for i in range(combo.count())]
        completer=QCompleter(items,combo)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        combo.setCompleter(completer)

    def _combo_data_or_resolved_text(self,combo,choices=None):
        data=combo.currentData()
        if data:
            return data
        text=combo.currentText().strip()
        for i in range(combo.count()):
            if text == combo.itemText(i):
                return combo.itemData(i) or combo.itemText(i)
        if choices:
            return resolve_name(text,choices,cutoff=0.55) or text
        return text

    def _identify_quality(self):
        return self.ident_quality_combo.currentData() or "Gold"

    def _clear_identify_input(self):
        self.ident_path_edit.clear(); self.ident_manual_text.clear()
        self._clear_identify_results()
        self.ident_summary.setText("等待输入装备数据")
        self._refresh_identify_previews()

    def _clear_identify_results(self):
        while self.ident_result_layout.count():
            it=self.ident_result_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
            elif it.layout(): self._delete_layout(it.layout())

    def _delete_layout(self,layout):
        while layout.count():
            it=layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
            elif it.layout(): self._delete_layout(it.layout())

    def _set_identify_busy(self,busy,msg=None):
        if msg: self.ident_summary.setText(msg)
        for btn in (getattr(self,"ident_parse_btn",None),getattr(self,"ident_manual_btn",None)):
            if btn: btn.setEnabled(not busy)

    def _identify_paths_from_text(self):
        raw=self.ident_path_edit.text().strip().strip('"')
        if not raw:
            return []
        return [Path(os.path.expandvars(part.strip().strip('"'))) for part in re.split(r"[;\n]+",raw) if part.strip()]

    def _refresh_identify_previews(self):
        if not hasattr(self,"ident_preview_layout"):
            return
        while self.ident_preview_layout.count():
            it=self.ident_preview_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
            elif it.layout(): self._delete_layout(it.layout())

        paths=[path for path in self._identify_paths_from_text() if path.exists()]
        self.ident_preview_scroll.setVisible(bool(paths))
        for path in paths[:12]:
            frame=QFrame()
            frame.setFixedSize(98,98)
            frame.setStyleSheet("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:6px}")
            grid=QGridLayout(frame)
            grid.setContentsMargins(2,2,2,2)
            grid.setSpacing(0)
            label=QLabel()
            label.setFixedSize(92,92)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("border:none;background:transparent")
            pix=QPixmap(str(path))
            if not pix.isNull():
                label.setPixmap(pix.scaled(label.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
            label.setToolTip(str(path))
            label.mousePressEvent=lambda event,p=path: self._show_identify_preview_image(p)
            grid.addWidget(label,0,0)
            close_btn=QPushButton("×")
            close_btn.setObjectName("btnDanger")
            close_btn.setFixedSize(20,20)
            close_btn.clicked.connect(lambda checked,p=path: self._remove_identify_preview_path(p))
            grid.addWidget(close_btn,0,0,Qt.AlignTop|Qt.AlignRight)
            self.ident_preview_layout.addWidget(frame)
        self.ident_preview_layout.addStretch()

    def _show_identify_preview_image(self,path:Path):
        dlg=QDialog(self)
        dlg.setWindowTitle(path.name)
        dlg.setMinimumSize(900,650)
        dlg.setStyleSheet(STYLE)
        layout=QVBoxLayout(dlg)
        label=QLabel()
        label.setAlignment(Qt.AlignCenter)
        pix=QPixmap(str(path))
        if not pix.isNull():
            screen=QApplication.primaryScreen().availableGeometry()
            max_size=QSize(min(1200,screen.width()-160),min(800,screen.height()-180))
            label.setPixmap(pix.scaled(max_size,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        layout.addWidget(label,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def _remove_identify_preview_path(self,path:Path):
        remaining=[str(p) for p in self._identify_paths_from_text() if p != path]
        self.ident_path_edit.setText(";".join(remaining))

    def _identify_start(self):
        if self._identify_paths_from_text():
            self._identify_from_image_path()
        else:
            self._identify_from_manual()

    def _start_identify_capture_mode(self):
        QMessageBox.information(
            self,
            "截图鉴定",
            f"点击 OK 后请切回游戏。\n\n按 {self._hk_capture} 连续截图，按 {self._hk_finish} 完成并返回鉴定页。"
        )
        self._identify_capture_dir=DATA_ROOT/"identify_captures"
        self._identify_capture_dir.mkdir(parents=True,exist_ok=True)
        self._identify_capture_count=0
        self.showMinimized()
        self._register_scan_hotkeys("identify")
        self.ident_summary.setText(f"截图鉴定已启动：{self._hk_capture} 截图，{self._hk_finish} 完成")

    def _capture_identify_foreground(self):
        try:
            import mss
            import mss.tools
            from src.scanner.window_capture import capture_foreground_window
            with mss.MSS() as sct:
                screenshot,_=capture_foreground_window(sct)
                self._identify_capture_count=getattr(self,"_identify_capture_count",0)+1
                filename=f"identify_capture_{int(time.time()*1000)}_{self._identify_capture_count:04d}.png"
                path=getattr(self,"_identify_capture_dir",DATA_ROOT/"identify_captures")/filename
                path.parent.mkdir(parents=True,exist_ok=True)
                mss.tools.to_png(screenshot.rgb,screenshot.size,output=str(path))
            logger.success(f"鉴定截图成功: {path.name}")
            self.identify_capture_signal.emit(str(path))
        except Exception as e:
            logger.error(f"鉴定截图失败: {e}")

    def _add_identify_capture_path(self,path_text):
        paths=[str(p) for p in self._identify_paths_from_text()]
        paths.append(path_text)
        self.ident_path_edit.setText(";".join(paths))

    def _finish_identify_capture_mode(self):
        self._unregister_scan_hotkeys()
        self.showNormal(); self.activateWindow()
        count=getattr(self,"_identify_capture_count",0)
        self.ident_summary.setText(f"已完成鉴定截图 {count} 张，点击开始鉴定继续。")

    def _identify_choose_file(self):
        paths,_=QFileDialog.getOpenFileNames(self,"选择装备截图",str(SCREENSHOT_DIR),"Images (*.png *.jpg *.jpeg *.bmp)")
        if paths:
            self.ident_path_edit.setText(";".join(paths))

    def _identify_from_clipboard(self):
        cb=QApplication.clipboard()
        mime=cb.mimeData()
        if mime and mime.hasImage():
            img=cb.image()
            if not img.isNull():
                clip_path=DATA_ROOT/f"identify_clipboard_{int(time.time()*1000)}.png"
                img.save(str(clip_path))
                self.ident_path_edit.setText(str(clip_path))
                return

        text=(cb.text() or "").strip()
        if not text:
            QMessageBox.information(self,"粘贴","剪贴板中没有图片、路径或文本数据。")
            return
        maybe_paths=[Path(os.path.expandvars(part.strip().strip('"'))) for part in re.split(r"[;\n]+",text) if part.strip()]
        if maybe_paths and all(path.exists() for path in maybe_paths):
            self.ident_path_edit.setText(";".join(str(path) for path in maybe_paths))
        else:
            self.ident_manual_text.setPlainText(text)
            self._apply_identify_manual_fields(text)

    def _identify_from_image_path(self):
        paths=self._identify_paths_from_text()
        if not paths:
            QMessageBox.warning(self,"鉴定","请先选择或粘贴图片路径。")
            return
        missing=[str(path) for path in paths if not path.exists()]
        if missing:
            QMessageBox.warning(self,"鉴定",f"图片不存在：{missing[0]}")
            return
        image_jobs=[]
        for path in paths:
            options=self._choose_identify_image_options(path)
            if options is None:
                return
            image_jobs.append((path,options))
        self._set_identify_busy(True,"正在解析图片...")
        self._identify_parse_worker=WorkerThread(target=lambda:self._parse_identify_images(image_jobs),parent=self)
        self._identify_parse_worker.result_ready.connect(self._on_identify_items_loaded)
        self._identify_parse_worker.error.connect(self._on_identify_error)
        self._identify_parse_worker.start()

    def _choose_identify_image_options(self,path:Path):
        dlg=QDialog(self)
        dlg.setWindowTitle("选择鉴定类型")
        dlg.setMinimumSize(820,680)
        dlg.setStyleSheet(STYLE)
        layout=QVBoxLayout(dlg); layout.setSpacing(10)

        image_label=QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setMinimumHeight(430)
        pix=QPixmap(str(path))
        if not pix.isNull():
            image_label.setPixmap(pix.scaled(QSize(780,430),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        layout.addWidget(image_label,1)

        type_row=QHBoxLayout(); type_row.addWidget(QLabel("装备类型"))
        type_group=QButtonGroup(dlg)
        drive_rb=QRadioButton("驱动"); tape_rb=QRadioButton("卡带")
        drive_rb.setChecked(True)
        type_group.addButton(drive_rb,0); type_group.addButton(tape_rb,1)
        type_row.addWidget(drive_rb); type_row.addWidget(tape_rb); type_row.addStretch()
        layout.addLayout(type_row)

        drive_row=QHBoxLayout(); drive_row.addWidget(QLabel("驱动形状"))
        shape_combo=SearchableComboBox()
        for sid in sorted([s for s in self._shape_areas.keys() if s!="TAPE_15"], key=lambda x:(self._shape_areas.get(x,0),x)):
            shape_combo.addItem(f"{sid} ({self._shape_areas.get(sid,0)}格)",sid)
        current_shape=self.ident_shape_combo.currentData() if hasattr(self,"ident_shape_combo") else None
        idx=shape_combo.findData(current_shape)
        if idx>=0: shape_combo.setCurrentIndex(idx)
        self._make_combo_searchable(shape_combo)
        drive_row.addWidget(shape_combo,1); layout.addLayout(drive_row)

        tape_row=QHBoxLayout(); tape_row.addWidget(QLabel("卡带套装"))
        set_combo=SearchableComboBox()
        for set_name in self.all_set_names:
            set_combo.addItem(set_name,set_name)
        current_set=self.ident_set_combo.currentData() if hasattr(self,"ident_set_combo") else None
        idx=set_combo.findData(current_set)
        if idx>=0: set_combo.setCurrentIndex(idx)
        self._make_combo_searchable(set_combo)
        tape_row.addWidget(set_combo,1)
        tape_row.addWidget(QLabel("默认主词条"))
        main_combo=SearchableComboBox()
        main_pool=self._get_tape_main_stats_pool()
        for stat_name in main_pool:
            main_combo.addItem(stat_name,stat_name)
        current_main=self.ident_main_combo.currentData() if hasattr(self,"ident_main_combo") else None
        idx=main_combo.findData(current_main)
        if idx>=0: main_combo.setCurrentIndex(idx)
        self._make_combo_searchable(main_combo)
        tape_row.addWidget(main_combo,1); layout.addLayout(tape_row)

        def sync_rows():
            is_tape=tape_rb.isChecked()
            for i in range(drive_row.count()):
                w=drive_row.itemAt(i).widget()
                if w: w.setVisible(not is_tape)
            for i in range(tape_row.count()):
                w=tape_row.itemAt(i).widget()
                if w: w.setVisible(is_tape)
        drive_rb.toggled.connect(sync_rows); tape_rb.toggled.connect(sync_rows); sync_rows()

        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:
            return None
        if tape_rb.isChecked():
            return {
                "type":"tape",
                "set_name":self._combo_data_or_resolved_text(set_combo,self.all_set_names),
                "main_stat":self._combo_data_or_resolved_text(main_combo,main_pool),
            }
        return {"type":"drive","shape_id":self._combo_data_or_resolved_text(shape_combo,self._shape_areas.keys()).split()[0]}

    def _parse_identify_images(self,image_jobs:list[tuple[Path,dict]]):
        p=BatchProcessor(input_dir=str(SCREENSHOT_DIR),output_file=str(USER_CONFIG_DIR/"identify_preview.json"),config_dir=str(CONFIG_DIR))
        items=[]
        for path,options in image_jobs:
            items.extend(p.parse_identify_items(
                str(path),
                forced_type=options.get("type"),
                forced_shape_id=options.get("shape_id"),
                forced_set_name=options.get("set_name"),
                forced_main_stat=options.get("main_stat"),
            ))
        return items

    def _on_identify_items_loaded(self,items):
        self._set_identify_busy(False)
        if not items:
            QMessageBox.warning(self,"鉴定","未从图片中识别到可鉴定的驱动或卡带。")
            return
        if not self._confirm_identify_tape_main_stats(items):
            self.ident_summary.setText("已取消鉴定")
            return
        self._load_identify_item_to_form(items[0])
        self._start_identify_items(items)

    def _confirm_identify_tape_main_stats(self,items):
        tapes=[item for item in items if isinstance(item,Tape)]
        if not tapes:
            return True
        should_confirm=len(tapes)>1 or any((not item.main_stats) or ("未知" in str(item.main_stats)) for item in tapes)
        if not should_confirm:
            return True

        main_pool=self._get_tape_main_stats_pool()
        dlg=QDialog(self)
        dlg.setWindowTitle("确认卡带主词条")
        dlg.setMinimumSize(720,420)
        dlg.setStyleSheet(STYLE)
        layout=QVBoxLayout(dlg); layout.setSpacing(10)
        hint=QLabel("请为每个识别到的卡带分别确认主词条；可直接输入中文或拼音搜索。")
        hint.setStyleSheet("color:#8b949e;border:none")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        content=QWidget(); rows=QVBoxLayout(content); rows.setContentsMargins(0,0,0,0); rows.setSpacing(8)
        combos=[]
        fallback_main=self.ident_main_combo.currentData() or self.ident_main_combo.currentText()
        for idx,item in enumerate(tapes,1):
            frame=QFrame()
            frame.setStyleSheet("QFrame{background:#161b22;border:1px solid #21262d;border-radius:6px}")
            row=QHBoxLayout(frame); row.setContentsMargins(10,8,10,8); row.setSpacing(10)
            summary=", ".join(f"{k}{v:g}" for k,v in list(item.sub_stats.items())[:4])
            label=QLabel(f"卡带 {idx}  {item.set_name or ''}  {summary}")
            label.setWordWrap(True)
            row.addWidget(label,1)
            combo=SearchableComboBox()
            current=item.main_stats if item.main_stats and "未知" not in str(item.main_stats) else fallback_main
            added=set()
            if current:
                combo.addItem(str(current),str(current)); added.add(str(current))
            for stat_name in main_pool:
                if stat_name not in added:
                    combo.addItem(stat_name,stat_name)
            combo.refresh_search_items()
            self._set_combo_data(combo,current)
            combo.setMinimumWidth(220)
            row.addWidget(combo)
            combos.append((item,combo))
            rows.addWidget(frame)
        rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll,1)

        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec()!=QDialog.Accepted:
            return False
        for item,combo in combos:
            item.main_stats=self._combo_data_or_resolved_text(combo,main_pool)
        return True

    def _load_identify_item_to_form(self,item):
        if isinstance(item,Tape):
            self.ident_tape_rb.setChecked(True)
            set_name=resolve_name(item.set_name,self.all_set_names,cutoff=0.78) or item.set_name
            self._set_combo_data(self.ident_set_combo,set_name)
            self._set_combo_data(self.ident_main_combo,item.main_stats)
        else:
            self.ident_drive_rb.setChecked(True)
            self._set_combo_data(self.ident_shape_combo,item.shape_id)
        self._set_combo_data(self.ident_quality_combo,item.quality)
        self.ident_manual_text.setPlainText("\n".join(f"{k}: {v}" for k,v in item.sub_stats.items()))
        self._on_identify_type_changed()

    def _identify_from_manual(self):
        text=self.ident_manual_text.toPlainText()
        self._apply_identify_manual_fields(text)
        sub_stats=self._parse_manual_stats(text)
        quality=self._identify_quality()
        uid=f"identify_{int(time.time()*1000)}"
        try:
            if self.ident_tape_rb.isChecked():
                set_name=self._combo_data_or_resolved_text(self.ident_set_combo,self.all_set_names)
                set_name=resolve_name(set_name,self.all_set_names,cutoff=0.78) or set_name
                main_stat=self._combo_data_or_resolved_text(self.ident_main_combo,self._get_tape_main_stats_pool())
                item=Tape(uid=uid,item_type="tape",shape_id="TAPE_15",area=15,quality=quality,set_name=set_name,main_stats=main_stat,sub_stats=sub_stats)
            else:
                shape_id=self._combo_data_or_resolved_text(self.ident_shape_combo,self._shape_areas.keys()).split()[0]
                area=self._shape_areas.get(shape_id,3)
                item=Drive(uid=uid,item_type="drive",shape_id=shape_id,area=area,quality=quality,main_stats={"攻击力":0.0,"生命值":0.0},sub_stats=sub_stats)
        except Exception as e:
            QMessageBox.critical(self,"鉴定",f"装备数据无效：\n{e}")
            return
        self._start_identify_item(item)

    def _apply_identify_manual_fields(self,text):
        if not text: return
        lower=text.lower()
        if "卡带" in text or "tape" in lower:
            self.ident_tape_rb.setChecked(True)
        elif "驱动" in text or "drive" in lower:
            self.ident_drive_rb.setChecked(True)

        if "purple" in lower or "紫" in text:
            self._set_combo_data(self.ident_quality_combo,"Purple")
        elif "blue" in lower or "蓝" in text:
            self._set_combo_data(self.ident_quality_combo,"Blue")
        elif "gold" in lower or "金" in text:
            self._set_combo_data(self.ident_quality_combo,"Gold")

        for sid in self._shape_areas.keys():
            if sid!="TAPE_15" and sid in text:
                self._set_combo_data(self.ident_shape_combo,sid)
                break

        tokens=self._manual_tokens(text)
        for token in tokens:
            if "套装" in token or "set" in token.lower():
                value=self._manual_value(token)
                resolved=resolve_name(value,self.all_set_names,cutoff=0.55)
                if resolved:
                    self._set_combo_data(self.ident_set_combo,resolved)
            if "主词条" in token or "主属性" in token or "main" in token.lower():
                value=self._manual_value(token)
                resolved=resolve_name(value,self._get_tape_main_stats_pool(),cutoff=0.55)
                if resolved:
                    self._set_combo_data(self.ident_main_combo,resolved)
        self._on_identify_type_changed()

    def _manual_tokens(self,text):
        import re
        return [p.strip() for p in re.split(r"[\n,，;；]+",text) if p.strip()]

    def _manual_value(self,token):
        for sep in ("：",":","="):
            if sep in token:
                return token.split(sep,1)[1].strip()
        return token.strip()

    def _resolve_stat_name(self,name,percent=False):
        clean=name.strip().strip("：:= ")
        for prefix in ("副词条","词条","主词条","主属性"):
            clean=clean.replace(prefix,"")
        clean=clean.strip()
        if percent and not clean.endswith("%") and not clean.endswith("百分比"):
            clean=f"{clean}%"
        se=self.scoring_engine or ScoringEngine(str(CONFIG_DIR))
        aliases=se.stat_alias_mapping
        if clean in aliases:
            return aliases[clean]
        choices=list(se.gold_base_values.keys())+list(aliases.keys())+list(aliases.values())
        resolved=resolve_name(clean,choices,cutoff=0.62)
        if resolved in aliases:
            return aliases[resolved]
        return resolved or clean

    def _parse_manual_stats(self,text):
        import re
        stats={}
        for token in self._manual_tokens(text):
            if any(k in token for k in ("类型","品质","形状","套装","主词条","主属性")):
                continue
            m=re.search(r"(.+?)[：:=\s]+([-+]?\d+(?:\.\d+)?)\s*(%)?",token)
            if not m:
                m=re.search(r"([\u4e00-\u9fffA-Za-z%]+?)([-+]?\d+(?:\.\d+)?)\s*(%)?",token)
            if not m:
                continue
            stat_name=self._resolve_stat_name(m.group(1),percent=(m.group(3)=="%" or "%" in token))
            try:
                stats[stat_name]=float(m.group(2))
            except ValueError:
                continue
        return stats

    def _start_identify_item(self,item):
        self._start_identify_items([item])

    def _start_identify_items(self,items):
        self._set_identify_busy(True,"正在匹配角色图纸并评分...")
        self._identify_worker=WorkerThread(target=lambda:self._run_identify_items(items),parent=self)
        self._identify_worker.result_ready.connect(self._render_identify_result)
        self._identify_worker.error.connect(self._on_identify_error)
        self._identify_worker.start()

    def _get_identify_blueprints(self):
        if self._identify_blueprint_cache:
            return self._identify_blueprint_cache
        orchestrator=NTEPipelineOrchestrator(config_dir=str(CONFIG_DIR))
        roles=list(orchestrator.roles_db.keys())
        blueprints=orchestrator.solve_blueprints(roles)
        self._identify_blueprint_cache=(orchestrator,blueprints)
        return self._identify_blueprint_cache

    def _run_identify_item(self,item):
        orchestrator,blueprints=self._get_identify_blueprints()
        scoring=ScoringEngine(str(CONFIG_DIR))
        rows=[]
        if isinstance(item,Tape):
            item_set=orchestrator._resolve_set_name(item.set_name)
            item.set_name=item_set
        for role_name,role_data in orchestrator.roles_db.items():
            role_bps=blueprints.get(role_name,[])
            if not role_bps:
                continue
            target_set=orchestrator._resolve_set_name(role_data.get("default_set",""))
            weights=role_data.get("weights",{})
            max_weight=scoring._get_max_theoretical_weight(weights)
            if isinstance(item,Tape):
                if item.set_name!=target_set:
                    continue
                score=scoring.calculate_cartridge_score(item,weights,max_weight)
                match_desc="套装匹配"
                area=15
            else:
                set_shapes=orchestrator.sets_db[target_set]["shapes"]
                in_set=item.shape_id in set_shapes
                in_extra=any(item.shape_id in bp.get("extra_pieces",[]) for bp in role_bps)
                if not in_set and not in_extra:
                    continue
                score=scoring.calculate_drive_score(item,weights,max_weight)
                match_desc="套装位" if in_set else "散件位"
                area=item.area
            grade=scoring.get_grade_tag(score,area)
            max_score=area*10.0
            rows.append({
                "role":role_name,
                "set":target_set,
                "score":score,
                "grade":grade,
                "percent":round(score/max_score*100,1) if max_score else 0,
                "match":match_desc,
                "weights":weights,
            })
        rows.sort(key=lambda r:r["score"],reverse=True)
        return {"item":item,"rows":rows}

    def _run_identify_items(self,items):
        return [self._run_identify_item(item) for item in items]

    def _render_identify_result(self,data):
        if isinstance(data,list):
            self._identify_result_pages=data
            self._identify_result_page_index=0
            self._render_identify_result_page()
            return
        self._identify_result_pages=[data]
        self._identify_result_page_index=0
        self._render_identify_result_page()

    def _render_identify_result_page(self):
        pages=getattr(self,"_identify_result_pages",[])
        if not pages:
            return
        idx=max(0,min(getattr(self,"_identify_result_page_index",0),len(pages)-1))
        self._identify_result_page_index=idx
        data=pages[idx]
        self._set_identify_busy(False)
        self._clear_identify_results()
        item=data.get("item")
        rows=data.get("rows",[])
        item_name="卡带" if isinstance(item,Tape) else "驱动块"
        page_text=f"（{idx+1}/{len(pages)}）" if len(pages)>1 else ""
        self.ident_summary.setText(f"{item_name}鉴定完成{page_text}：{len(rows)} 名角色可使用")

        preview_weights=rows[0]["weights"] if rows else {}
        if isinstance(item,Tape):
            self.ident_result_layout.addWidget(self._equip_card(item.set_name,item.main_stats,item.sub_stats,None,item.uid,preview_weights,None,item.quality))
        else:
            self.ident_result_layout.addWidget(self._equip_card(item.shape_id,"",item.sub_stats,item.shape_id,item.uid,preview_weights,None,item.quality))

        if len(pages)>1:
            nav=QHBoxLayout()
            prev_btn=QPushButton("上一页"); next_btn=QPushButton("下一页")
            prev_btn.setEnabled(idx>0); next_btn.setEnabled(idx<len(pages)-1)
            prev_btn.clicked.connect(lambda:self._set_identify_result_page(idx-1))
            next_btn.clicked.connect(lambda:self._set_identify_result_page(idx+1))
            nav.addWidget(prev_btn); nav.addWidget(next_btn); nav.addStretch()
            self.ident_result_layout.addLayout(nav)

        if not rows:
            empty=QLabel("没有找到图纸可使用该装备的角色。")
            empty.setAlignment(Qt.AlignCenter); empty.setStyleSheet("color:#6e7681;padding:20px")
            self.ident_result_layout.addWidget(empty)
            return

        for i,row in enumerate(rows,1):
            self.ident_result_layout.addWidget(self._identify_result_row(i,row))
        self.ident_result_layout.addStretch()

    def _set_identify_result_page(self,index):
        self._identify_result_page_index=index
        self._render_identify_result_page()

    def _identify_result_row(self,rank,row):
        grade=row["grade"]; gc=GRADE_COLORS.get(grade,"#58a6ff"); gbg=GRADE_BGS.get(grade,f"{gc}15")
        w=QFrame(); w.setStyleSheet("QFrame{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:8px}")
        h=QHBoxLayout(w); h.setSpacing(10); h.setContentsMargins(8,4,8,4)
        rk=QLabel(str(rank)); rk.setFixedSize(28,28); rk.setAlignment(Qt.AlignCenter)
        rk.setStyleSheet("background:#21262d;color:#c9d1d9;border-radius:14px;font-weight:700")
        h.addWidget(rk)
        info=QVBoxLayout(); info.setSpacing(2)
        rn=QLabel(row["role"]); rn.setStyleSheet("font-size:14px;font-weight:700;color:#c9d1d9;border:none")
        meta=QLabel(f"{row['set']} · {row['match']} · 占比 {row['percent']:.1f}%")
        meta.setStyleSheet("color:#8b949e;font-size:11px;border:none")
        info.addWidget(rn); info.addWidget(meta); h.addLayout(info,1)
        badge=QFrame(); badge.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:6px;padding:4px 10px}}")
        bl=QVBoxLayout(badge); bl.setContentsMargins(8,2,8,2); bl.setSpacing(0)
        score=QLabel(f"{row['score']:.1f}"); score.setAlignment(Qt.AlignCenter); score.setStyleSheet(f"font-size:18px;font-weight:800;color:{gc};border:none")
        gd=QLabel(grade); gd.setAlignment(Qt.AlignCenter); gd.setStyleSheet(f"font-size:11px;font-weight:700;color:{gc};border:none")
        bl.addWidget(score); bl.addWidget(gd); h.addWidget(badge)
        return w

    def _on_identify_error(self,err):
        self._set_identify_busy(False)
        QMessageBox.critical(self,"鉴定失败",str(err))

    # ── Page: Blueprint
    def _page_blueprint(self):
        page=QWidget(); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(12)
        hdr=QHBoxLayout()
        self._bp_search=QLineEdit(); self._bp_search.setPlaceholderText("搜索角色（支持拼音）..."); self._bp_search.setClearButtonEnabled(True)
        self._bp_search.textChanged.connect(self._filter_blueprints); hdr.addWidget(self._bp_search)
        refresh_btn=QPushButton("刷新图纸"); refresh_btn.setObjectName("btnAction"); refresh_btn.clicked.connect(self._refresh_blueprints); hdr.addWidget(refresh_btn)
        l.addLayout(hdr)
        self._bp_content=QWidget(); self._bp_content_layout=QVBoxLayout(self._bp_content)
        self._bp_content_layout.setSpacing(12); self._bp_content_layout.setAlignment(Qt.AlignTop)
        l.addWidget(self._bp_content); l.addStretch()
        self._bp_data={}
        return scroll

    def _refresh_blueprints(self):
        while self._bp_content_layout.count():
            it=self._bp_content_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self._bp_content_layout.addWidget(QLabel("正在求解图纸..."))
        self._bp_worker=WorkerThread(target=self._compute_blueprints,parent=self)
        self._bp_worker.result_ready.connect(self._render_blueprints)
        self._bp_worker.error.connect(lambda e: self._bp_content_layout.itemAt(0).widget().setText(f"求解失败: {e}"))
        self._bp_worker.start()

    def _compute_blueprints(self):
        o=NTEPipelineOrchestrator(config_dir=str(CONFIG_DIR))
        all_roles=list(self.roles_db.keys())
        raw=o.solve_blueprints(all_roles)
        deduped={}
        for role_name,blueprints in raw.items():
            extra_label=self.roles_db[role_name].get("extra_shape_label","")
            seen=set()
            unique=[]
            for bp in blueprints:
                extra_set=frozenset(sid for sid in bp["extra_pieces"] if o.shapes_db[sid].label==extra_label)
                if extra_set not in seen:
                    seen.add(extra_set)
                    unique.append(bp)
            deduped[role_name]=unique
        return deduped

    def _render_blueprints(self,data):
        self._bp_data=data or {}
        self._draw_blueprints()

    def _draw_blueprints(self,filter_text=""):
        while self._bp_content_layout.count():
            it=self._bp_content_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        if not self._bp_data:
            self._bp_content_layout.addWidget(QLabel("暂无图纸数据，请点击刷新")); return
        search_text=filter_text.strip()
        has_search=bool(search_text)
        for role_name in sorted(self._bp_data.keys()):
            if has_search and not _match_pinyin(role_name,search_text): continue
            blueprints=self._bp_data[role_name]
            rd=self.roles_db.get(role_name,{})
            default_set=rd.get("default_set","")
            grp=QGroupBox(f"{role_name}  —  {default_set}  ({len(blueprints)} 套图纸)")
            grp.setStyleSheet("QGroupBox{font-size:13px;font-weight:600;color:#58a6ff;border:1px solid #21262d;border-radius:8px;padding-top:16px}")
            gl=QVBoxLayout(grp); gl.setSpacing(8)
            visible_blueprints=blueprints if has_search else blueprints[:3]
            for i,bp in enumerate(visible_blueprints):
                row=QHBoxLayout(); row.setSpacing(10)
                board_w=PuzzleBoardWidget(bp["board"],cell_size=28)
                row.addWidget(board_w)
                extras_w=QWidget(); el=QVBoxLayout(extras_w); el.setContentsMargins(0,0,0,0); el.setSpacing(2)
                el.addWidget(QLabel(f"方案 {i+1}"))
                extra_row=QHBoxLayout(); extra_row.setSpacing(4)
                for shape_id in bp.get("extra_pieces",[]):
                    pm=_get_shape_pixmap(shape_id,40)
                    sl=QLabel(); sl.setPixmap(pm); sl.setToolTip(shape_id)
                    extra_row.addWidget(sl)
                extra_row.addStretch(); el.addLayout(extra_row)
                row.addWidget(extras_w,1); gl.addLayout(row)
            if not has_search and len(blueprints)>3:
                hint=QLabel(f"默认仅显示 3 张；搜索「{role_name}」可显示全部 {len(blueprints)} 张图纸。")
                hint.setStyleSheet("color:#8b949e;font-size:11px;border:none;background:transparent")
                gl.addWidget(hint)
            self._bp_content_layout.addWidget(grp)

    def _filter_blueprints(self,txt): self._draw_blueprints(txt)

    # ── Page: Config
    def _page_config(self):
        page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(10)
        ts=QHBoxLayout(); ts.addWidget(QLabel("编辑配置文件:"))
        self.config_tabs=QComboBox()
        self.config_tabs.addItems(["roles.json","sets.json"])
        self.config_tabs.currentTextChanged.connect(self._switch_config_form); ts.addWidget(self.config_tabs)
        self.config_add_btn=QPushButton("+ 添加角色"); self.config_add_btn.setObjectName("btnPrimary")
        self.config_add_btn.clicked.connect(self._config_add_item); ts.addWidget(self.config_add_btn)
        ts.addStretch()
        save_btn=QPushButton("保存"); save_btn.setObjectName("btnPrimary"); save_btn.clicked.connect(self._save_config_form); ts.addWidget(save_btn)
        l.addLayout(ts)
        self.config_form_area=QScrollArea(); self.config_form_area.setWidgetResizable(True)
        self.config_form_widget=QWidget(); self.config_form_layout=QVBoxLayout(self.config_form_widget); self.config_form_area.setWidget(self.config_form_widget)
        l.addWidget(self.config_form_area,1); return page

    def _refresh_config_forms(self):
        if hasattr(self,'config_tabs'): self._switch_config_form(self.config_tabs.currentText())

    def _switch_config_form(self,name):
        if not name: return
        # Update add button text based on selected file
        if name=="roles.json": self.config_add_btn.setText("+ 添加角色")
        elif name=="sets.json": self.config_add_btn.setText("+ 添加套装")
        while self.config_form_layout.count():
            it=self.config_form_layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        path=CONFIG_DIR/name
        if not path.exists(): self.config_form_layout.addWidget(QLabel(f"文件不存在: {name}")); return
        with open(path,"r",encoding="utf-8") as f: data=json.load(f)
        self._current_config_name=name
        if name=="roles.json": self._build_roles_form(data)
        elif name=="sets.json": self._build_sets_form(data)

    def _add_section(self,title): grp=QGroupBox(title); l=QVBoxLayout(grp); return grp,l
    def _field(self,label,widget,layout): row=QHBoxLayout(); row.addWidget(QLabel(label)); row.addWidget(widget,1); layout.addLayout(row)

    def _build_roles_form(self,data):
        hdr=QHBoxLayout()
        role_search=QLineEdit(); role_search.setPlaceholderText("搜索角色（支持拼音）..."); role_search.setClearButtonEnabled(True)
        hdr.addWidget(role_search); hdr.addStretch()
        self.config_form_layout.addLayout(hdr)

        all_names=sorted(data.keys())
        roles_tabs=QTabWidget()
        tab_indices={}

        def _build_all_tabs():
            for rname in all_names:
                rd=data[rname]; tw=QWidget()
                scroll2=QScrollArea(); scroll2.setWidgetResizable(True); scroll2.setWidget(tw)
                fl=QVBoxLayout(tw); fl.setSpacing(8)
                role_hdr=QHBoxLayout(); role_hdr.addWidget(QLabel(f"角色: {rname}")); role_hdr.addStretch()
                del_rb=QPushButton("删除此角色"); del_rb.setObjectName("btnDanger")
                del_rb.clicked.connect(lambda checked,rn=rname:self._del_role(rn,data,_full_rebuild))
                role_hdr.addWidget(del_rb); fl.addLayout(role_hdr)
                self._field("默认套装", (lambda:(c:=QComboBox(),c.addItems(self.all_set_names),c.setCurrentText(rd.get("default_set","")),c)[-1])(),fl)
                self._field("额外形状标签", (lambda:(c:=QComboBox(),c.addItems(["Type-2","Type-3","Type-4"]),c.setCurrentText(rd.get("extra_shape_label","")),c)[-1])(),fl)
                fl.addWidget(QLabel("底盘矩阵 (0=空格, -1=锁定):"))
                bm=rd.get("board_matrix",[[0]*5]*5); bw=QWidget(); bg=QGridLayout(bw); bg.setSpacing(2)
                for ri in range(5):
                    for ci in range(5):
                        v=str(bm[ri][ci]) if ri<len(bm) and ci<len(bm[ri]) else "0"
                        cb=QComboBox(); cb.addItems(["-1","0"]); cb.setCurrentText(v); cb.setFixedWidth(65); bg.addWidget(cb,ri,ci)
                fl.addWidget(bw)
                wts_hdr=QHBoxLayout(); wts_hdr.addWidget(QLabel("词条权重:")); wts_hdr.addStretch()
                add_wt=QPushButton("+ 添加词条"); add_wt.setObjectName("btnAction")
                add_wt.clicked.connect(lambda checked,rn=rname: self._add_weight(rn,data,lambda active=rn: _full_rebuild(active)))
                wts_hdr.addWidget(add_wt); fl.addLayout(wts_hdr)
                wts=rd.get("weights",{})
                for wk in sorted(wts.keys()):
                    wt_row=QHBoxLayout(); wt_row.setSpacing(6)
                    wt_row.addWidget(QLabel(wk))
                    sb=NoWheelDoubleSpinBox(); sb.setRange(0,10); sb.setSingleStep(0.05); sb.setValue(float(wts[wk])); sb.setDecimals(3)
                    sb.setKeyboardTracking(False)
                    sb.editingFinished.connect(lambda rn=rname,k=wk,s=sb: self._save_role_weight_value(rn,k,s.value(),data))
                    wt_row.addWidget(sb)
                    dw=QPushButton("✕"); dw.setObjectName("btnSm"); dw.setFixedSize(22,22)
                    dw.clicked.connect(lambda checked,rn=rname,k=wk: self._del_weight(rn,k,data,lambda active=rn: _full_rebuild(active)))
                    wt_row.addWidget(dw); fl.addLayout(wt_row)
                fl.addStretch()
                idx=roles_tabs.addTab(scroll2,rname); tab_indices[rname]=idx

        def _filter_tabs(filter_txt=""):
            ft=filter_txt.strip()
            for rname,idx in tab_indices.items():
                visible=_match_pinyin(rname,ft) if ft else True
                roles_tabs.setTabVisible(idx,visible)

        def _full_rebuild(active_role=None):
            nonlocal all_names, tab_indices
            if active_role is None and roles_tabs.currentIndex() >= 0:
                active_role=roles_tabs.tabText(roles_tabs.currentIndex())
            all_names=sorted(data.keys())
            while roles_tabs.count(): roles_tabs.removeTab(0)
            tab_indices.clear()
            _build_all_tabs()
            _filter_tabs(role_search.text())
            if active_role in tab_indices:
                roles_tabs.setCurrentIndex(tab_indices[active_role])

        _build_all_tabs()
        role_search.textChanged.connect(_filter_tabs)
        self.config_form_layout.addWidget(roles_tabs)

    def _config_add_item(self):
        name=getattr(self,'_current_config_name','')
        if name=="roles.json":
            data={}
            path=CONFIG_DIR/name
            if path.exists():
                with open(path,"r",encoding="utf-8") as f: data=json.load(f)
            self._add_role(data)
        elif name=="sets.json":
            data={}
            path=CONFIG_DIR/name
            if path.exists():
                with open(path,"r",encoding="utf-8") as f:
                    raw=json.load(f)
                    data=raw.get("sets",{})
            self._add_set(data)

    def _add_weight(self,rn,data,cb):
        stats_path=CONFIG_DIR/"stats.json"
        pool=[]
        if stats_path.exists():
            with open(stats_path,"r",encoding="utf-8") as f: pool=sorted(json.load(f).get("gold_base_values",{}).keys())
        existing=set(data[rn].get("weights",{}).keys())
        available=[s for s in pool if s not in existing]
        if not available: QMessageBox.information(self,"提示","所有词条已添加。"); return
        name,ok=QInputDialog.getItem(self,"添加词条","选择词条:",available,0,False)
        if ok and name.strip():
            data[rn].setdefault("weights",{})[name.strip()]=0.5
            self._save_config_data(data); cb()
    def _save_role_weight_value(self,rn,key,value,data):
        if rn in data and key in data[rn].get("weights",{}):
            data[rn]["weights"][key]=round(float(value),3)
            self._save_config_data(data)
    def _del_weight(self,rn,key,data,cb):
        if rn in data and key in data[rn].get("weights",{}):
            del data[rn]["weights"][key]
            self._save_config_data(data); cb()

    def _add_role(self,data):
        name,ok=QInputDialog.getText(self,"添加角色","角色名称:")
        if ok and name.strip() and name.strip() not in data:
            data[name.strip()]={"role_name":name.strip(),"default_set":self.all_set_names[0] if self.all_set_names else "","extra_shape_label":"","board_matrix":[[0]*5]*5,"weights":{}}
            self._save_config_data(data); self._load_data(); self._switch_config_form("roles.json")
    def _del_role(self,rn,data,cb=None):
        if QMessageBox.question(self,"确认",f"确定删除角色「{rn}」？")==QMessageBox.Yes:
            if rn in data: del data[rn]
            self._save_config_data(data); self._load_data()
            if cb: cb()
            else: self._switch_config_form("roles.json")

    def _build_sets_form(self,data):
        sd=data.get("sets",{})
        hdr=QHBoxLayout()
        set_search=QLineEdit(); set_search.setPlaceholderText("搜索套装（支持拼音）..."); set_search.setClearButtonEnabled(True)
        hdr.addWidget(set_search); hdr.addStretch()
        self.config_form_layout.addLayout(hdr)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        sw=QWidget(); slayout=QVBoxLayout(sw)
        set_groups={}
        for sname in sorted(sd.keys()):
            sinfo=sd[sname]; grp,gl=self._add_section(sname)
            set_groups[sname]=grp
            set_hdr=QHBoxLayout(); set_hdr.addWidget(QLabel(f"套装名称: {sname}")); set_hdr.addStretch()
            del_btn=QPushButton("删除"); del_btn.setObjectName("btnDanger")
            del_btn.clicked.connect(lambda checked,sn=sname: self._del_set(sn,sd)); set_hdr.addWidget(del_btn)
            gl.addLayout(set_hdr)
            shapes_edit=QLineEdit(); shapes_edit.setText(", ".join(sinfo.get("shapes",[])))
            gl.addWidget(QLabel("形状列表（逗号分隔）:")); gl.addWidget(shapes_edit)
            save_shapes_btn=QPushButton("保存形状列表"); save_shapes_btn.setObjectName("btnAction")
            save_shapes_btn.clicked.connect(lambda checked,sn=sname,se=shapes_edit,sdata=sd: self._save_set_shapes(sn,se,sdata))
            gl.addWidget(save_shapes_btn)
            slayout.addWidget(grp)
        def _filter_sets(filter_txt=""):
            ft=filter_txt.strip()
            for sname,grp in set_groups.items():
                grp.setVisible(_match_pinyin(sname,ft) if ft else True)
        set_search.textChanged.connect(_filter_sets)
        slayout.addStretch(); scroll.setWidget(sw); self.config_form_layout.addWidget(scroll)

    def _save_set_shapes(self,set_name,line_edit,sd):
        shapes_text=line_edit.text().strip()
        shapes=[s.strip() for s in shapes_text.split(",") if s.strip()]
        sd[set_name]["shapes"]=shapes
        data={"sets":sd}; self._save_config_data(data)
        QMessageBox.information(self,"保存",f"套装「{set_name}」形状列表已保存")

    def _add_set(self,sd):
        name,ok=QInputDialog.getText(self,"添加套装","套装名称:")
        if ok and name.strip() and name.strip() not in sd:
            sd[name.strip()]={"set_name":name.strip(),"shapes":[]}
            data={"sets":sd}; self._save_config_data(data); self._switch_config_form("sets.json")
    def _del_set(self,sn,sd):
        if QMessageBox.question(self,"确认",f"确定删除套装「{sn}」？")==QMessageBox.Yes:
            if sn in sd: del sd[sn]
            data={"sets":sd}; self._save_config_data(data); self._switch_config_form("sets.json")

    def _save_config_form(self):
        name=getattr(self,'_current_config_name',None)
        if not name: return
        path=CONFIG_DIR/name
        dlg=JsonEditDialog(name,path,self)
        if dlg.exec()==QDialog.Accepted: self._load_data(); QMessageBox.information(self,"保存",f"{name} 已保存")
    def _save_config_data(self,data):
        name=getattr(self,'_current_config_name',None)
        if not name: return
        with open(CONFIG_DIR/name,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=4)

    # ── Page: Settings
    def _page_settings(self):
        page=QWidget(); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(16)

        c1=self._card("运行日志设置")
        lr=QHBoxLayout(); lr.addWidget(QLabel("实时日志输出:")); lt=QCheckBox("启用运行日志"); lt.setChecked(self._log_enabled); lt.toggled.connect(self._toggle_log); lr.addWidget(lt); lr.addStretch(); c1.layout().addLayout(lr); l.addWidget(c1)

        # Hotkeys
        c_hk=self._card("快捷键绑定")
        f=QFormLayout(); f.setSpacing(10)
        cap_row=QHBoxLayout(); cap_row.setSpacing(8)
        self._hk_capture_edit=QKeySequenceEdit(QKeySequence(self._hk_capture))
        self._hk_capture_edit.setMaximumWidth(160)
        cap_row.addWidget(QLabel("全局截图按键:")); cap_row.addWidget(self._hk_capture_edit); cap_row.addStretch()
        f.addRow(cap_row)
        fin_row=QHBoxLayout(); fin_row.setSpacing(8)
        self._hk_finish_edit=QKeySequenceEdit(QKeySequence(self._hk_finish))
        self._hk_finish_edit.setMaximumWidth(160)
        fin_row.addWidget(QLabel("截图完成按键:")); fin_row.addWidget(self._hk_finish_edit); fin_row.addStretch()
        f.addRow(fin_row)
        stop_row=QHBoxLayout(); stop_row.setSpacing(8)
        self._hk_stop_edit=QKeySequenceEdit(QKeySequence(self._hk_stop))
        self._hk_stop_edit.setMaximumWidth(160)
        stop_row.addWidget(QLabel("紧急停止按键:")); stop_row.addWidget(self._hk_stop_edit); stop_row.addStretch()
        f.addRow(stop_row)
        save_hk=QPushButton("保存快捷键"); save_hk.setObjectName("btnPrimary"); save_hk.clicked.connect(self._save_hotkeys)
        f.addRow(save_hk)
        c_hk.layout().addLayout(f); l.addWidget(c_hk)

        c_update=self._card("软件更新")
        self._update_status=QLabel(f"当前版本: {APP_VERSION}")
        c_update.layout().addWidget(self._update_status)
        ur=QHBoxLayout(); ur.setSpacing(10)
        self._check_update_btn=QPushButton("检查更新"); self._check_update_btn.setObjectName("btnPrimary")
        self._check_update_btn.clicked.connect(self._check_updates)
        home_btn=QPushButton("GitHub 主页"); home_btn.clicked.connect(self._open_update_homepage)
        ur.addWidget(self._check_update_btn); ur.addWidget(home_btn); ur.addStretch()
        c_update.layout().addLayout(ur); l.addWidget(c_update)

        c2=self._card("截图文件管理")
        screenshot_files=_iter_image_files(SCREENSHOT_DIR)
        count=len(screenshot_files)
        smb=sum(f.stat().st_size for f in screenshot_files)/(1024*1024) if screenshot_files else 0
        self._ss_info=QLabel(f"当前截图: {count} 个 · {smb:.1f} MB"); c2.layout().addWidget(self._ss_info)
        br=QHBoxLayout(); br.setSpacing(10)
        for text,slot in [("刷新统计",self._refresh_ss),("清理所有截图",self._clear_ss),("打开文件夹",lambda: os.startfile(str(SCREENSHOT_DIR)) if SCREENSHOT_DIR.exists() else None)]:
            b=QPushButton(text)
            if "清理" in text: b.setObjectName("btnDanger")
            b.clicked.connect(slot); br.addWidget(b)
        br.addStretch(); c2.layout().addLayout(br); l.addWidget(c2)
        c3=self._card("库存信息")
        if OUTPUT_FILE.exists(): c3.layout().addWidget(QLabel(f"real_inventory.json: {OUTPUT_FILE.stat().st_size/1024:.1f} KB"))
        else: c3.layout().addWidget(QLabel("real_inventory.json 不存在")); l.addWidget(c3)
        c4=self._card("📁  快捷访问")
        qr=QHBoxLayout(); qr.setSpacing(10)
        for lbl,path in [("config",CONFIG_DIR),("logs",DATA_ROOT/"logs")]:
            b=QPushButton(lbl); b.clicked.connect(lambda checked,p=path: os.startfile(str(p)) if p.exists() else None); qr.addWidget(b)
        qr.addStretch(); c4.layout().addLayout(qr); l.addWidget(c4); l.addStretch(); return scroll

    def _check_updates(self):
        self._check_update_btn.setEnabled(False)
        self._update_status.setText("正在检查更新...")
        self._update_worker=WorkerThread(target=self._fetch_update_info,parent=self)
        self._update_worker.result_ready.connect(self._on_update_checked)
        self._update_worker.error.connect(self._on_update_error)
        self._update_worker.start()

    def _fetch_update_info(self):
        import urllib.error
        import urllib.request

        req=urllib.request.Request(
            GITHUB_LATEST_RELEASE_API,
            headers={"User-Agent": f"NTE-Drive-Calc/{APP_VERSION}", "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req,timeout=10) as resp:
                data=json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code==404:
                return {"has_release":False,"newer":False,"url":GITHUB_RELEASES_URL,"message":"未找到 GitHub Release。"}
            raise

        latest=str(data.get("tag_name") or data.get("name") or "").strip()
        url=data.get("html_url") or GITHUB_RELEASES_URL
        assets=data.get("assets") or []
        setup_asset=next((a for a in assets if str(a.get("name","")).lower().endswith(".exe")),None)
        if setup_asset and setup_asset.get("browser_download_url"):
            url=setup_asset["browser_download_url"]
        return {
            "has_release":True,
            "latest":latest,
            "newer":self._is_newer_version(latest,APP_VERSION),
            "url":url,
            "message":data.get("body") or "",
        }

    def _on_update_checked(self,info):
        self._check_update_btn.setEnabled(True)
        if not info.get("has_release"):
            self._update_status.setText(f"当前版本: {APP_VERSION}。{info.get('message','')}")
            QMessageBox.information(self,"检查更新","当前仓库还没有发布 Release，已为你打开 GitHub 主页。")
            self._open_url(GITHUB_HOME_URL)
            return

        latest=info.get("latest") or "未知"
        if info.get("newer"):
            self._update_status.setText(f"发现新版本: {latest}（当前 {APP_VERSION}）")
            if QMessageBox.question(self,"发现更新",f"发现新版本 {latest}。\n是否打开下载页面？")==QMessageBox.Yes:
                self._open_url(info.get("url") or GITHUB_RELEASES_URL)
        else:
            self._update_status.setText(f"当前已是最新版本: {APP_VERSION}")
            QMessageBox.information(self,"检查更新",f"当前已是最新版本。\n当前版本: {APP_VERSION}\n最新版本: {latest}")

    def _on_update_error(self,err):
        self._check_update_btn.setEnabled(True)
        self._update_status.setText(f"检查失败: {err}")
        if QMessageBox.question(self,"检查更新失败",f"无法自动检查更新:\n{err}\n\n是否打开 GitHub 主页？")==QMessageBox.Yes:
            self._open_update_homepage()

    def _open_update_homepage(self):
        self._open_url(GITHUB_HOME_URL)

    def _open_url(self,url):
        try:
            os.startfile(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    def _is_newer_version(self,remote,current):
        import re
        def nums(v):
            parts=[int(x) for x in re.findall(r"\d+",str(v))]
            return (parts+[0,0,0])[:3]
        return nums(remote)>nums(current)

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
        if QMessageBox.question(self,"确认",f"确定删除 {count} 个截图？\n不可恢复！")==QMessageBox.Yes:
            for f in files:
                try: f.unlink()
                except: pass
            self._refresh_ss(); logger.success(f"已清理 {count} 个截图")

# ── Dialogs
class JsonEditDialog(QDialog):
    def __init__(self,name,path,parent=None):
        super().__init__(parent); self._path=path; self.setWindowTitle(f"编辑 {name}"); self.setMinimumSize(650,500); self.setStyleSheet(STYLE)
        l=QVBoxLayout(self)
        self._editor=QTextEdit(); self._editor.setStyleSheet("font-family:'Consolas',monospace;font-size:12px"); self._editor.setTabStopDistance(20)
        with open(path,"r",encoding="utf-8") as f: self._editor.setPlainText(json.dumps(json.load(f),ensure_ascii=False,indent=2))
        l.addWidget(self._editor)
        bb=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); l.addWidget(bb)
    def _ok(self):
        try:
            data=json.loads(self._editor.toPlainText())
            with open(self._path,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=4); self.accept()
        except json.JSONDecodeError as e: QMessageBox.critical(self,"JSON 错误",str(e))

# ── Facade
class NTEAppFacade:
    def __init__(self,config_dir=str(CONFIG_DIR),user_config_dir=str(USER_CONFIG_DIR)): self.config_dir=config_dir; self.user_config_dir=user_config_dir
    def execute_vision_processing(self,input_dir=str(SCREENSHOT_DIR),output_file=str(OUTPUT_FILE)):
        logger.info("开始视觉解析..."); p=BatchProcessor(input_dir=input_dir,output_file=output_file,config_dir=self.config_dir); p.process_all(); logger.success("视觉解析完成")
    def execute_allocation(self,inventory_file,priority_list,custom_sets=None,mode="role_priority"):
        if not os.path.exists(inventory_file): logger.error(f"找不到 {inventory_file}！"); return None,None
        with open(inventory_file,"r",encoding="utf-8") as f: inventory=json.load(f)
        o=NTEPipelineOrchestrator(config_dir=self.config_dir); m=StateManager(config_dir=self.user_config_dir); lu=set(); bm=mode
        if mode=="update_mode": lu=m.get_locked_uids(); bm="role_priority"
        fp=o.run_full_allocation(inventory=inventory,priority_list=priority_list,custom_sets=custom_sets or {},mode=bm,locked_uids=lu)
        return fp,m

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
    from src.utils.logger import LOG_DIR
    _ensure_admin()
    _fault_log = open(str(LOG_DIR / "crash_dump.log"), "w", encoding="utf-8")
    faulthandler.enable(file=_fault_log)

    sys.excepthook = _global_exception_handler
    threading.excepthook = lambda args: logger.error(f"线程异常 [{args.thread}]: {args.exc_type.__name__}: {args.exc_value}")
    if hasattr(Qt, "AA_DontUseNativeDialogs"):
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app=QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    _apply_dark_palette(app)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": run_gui()
