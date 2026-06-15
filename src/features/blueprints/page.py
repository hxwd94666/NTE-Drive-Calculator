# 构建蓝图计算和筛选页面。
"""MainWindow methods for blueprints."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.app import runtime
from src.app.workers import WorkerThread
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.ui.puzzle_board import PuzzleBoardWidget, get_shape_pixmap as _get_shape_pixmap
from src.ui.widgets import match_pinyin as _match_pinyin

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_page_blueprint', '_refresh_blueprints', '_compute_blueprints', '_render_blueprints', '_draw_blueprints', '_filter_blueprints']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


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
    o=NTEPipelineOrchestrator(config_dir=str(runtime.CONFIG_DIR))
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
                pm=_get_shape_pixmap(shape_id,48)
                sl=QLabel(); sl.setPixmap(pm); sl.setToolTip(shape_id)
                sl.setFixedSize(52,52); sl.setScaledContents(True)
                extra_row.addWidget(sl)
            extra_row.addStretch(); el.addLayout(extra_row)
            row.addWidget(extras_w,1); gl.addLayout(row)
        if not has_search and len(blueprints)>3:
            hint=QLabel(f"默认仅显示 3 张；搜索「{role_name}」可显示全部 {len(blueprints)} 张图纸。")
            hint.setStyleSheet("color:#8b949e;font-size:11px;border:none;background:transparent")
            gl.addWidget(hint)
        self._bp_content_layout.addWidget(grp)

def _filter_blueprints(self,txt): self._draw_blueprints(txt)
