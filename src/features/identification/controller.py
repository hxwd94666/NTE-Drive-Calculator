# 控制单件识别页面的输入、解析和结果保存。
"""MainWindow methods for identification."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QCompleter, QFileDialog, QMessageBox

from src.app import runtime
from src.app.theme import STYLE
from src.app.workers import WorkerThread
from src.features.identification.page import build_identify_page, build_identify_result_row, parse_identify_paths, refresh_identify_previews, render_identify_result_page, show_identify_preview_image
from src.features.identification.temp_files import cleanup_identify_clipboard_files, is_identify_clipboard_file
from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.ui.plain_text_edit import PlainTextOnlyTextEdit
from src.ui.widgets import SearchableComboBox
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_page_identify', '_refresh_identify_options', '_on_identify_type_changed', '_get_tape_main_stats_pool', '_set_combo_data', '_make_combo_searchable', '_combo_data_or_resolved_text', '_identify_quality', '_clear_identify_input', '_clear_identify_results', '_delete_layout', '_set_identify_busy', '_identify_paths_from_text', '_refresh_identify_previews', '_show_identify_preview_image', '_remove_identify_preview_path', '_identify_start', '_start_identify_capture_mode', '_capture_identify_foreground', '_add_identify_capture_path', '_finish_identify_capture_mode', '_identify_choose_file', '_identify_from_clipboard', '_identify_from_image_path', '_parse_identify_images', '_on_identify_items_loaded', '_load_identify_item_to_form', '_identify_from_manual', '_apply_identify_manual_fields', '_manual_tokens', '_manual_value', '_resolve_stat_name', '_parse_manual_stats', '_start_identify_item', '_start_identify_items', '_get_identify_blueprints', '_run_identify_item', '_run_identify_items', '_render_identify_result', '_render_identify_result_page', '_set_identify_result_page', '_identify_result_row', '_on_identify_error']


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _page_identify(self):
    return build_identify_page(self,PlainTextOnlyTextEdit)

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
        with open(runtime.CONFIG_DIR/"stats.json","r",encoding="utf-8") as f:
            return json.load(f).get("tape_main_stats_pool",[])
    except Exception:
        return []

def _set_combo_data(self,combo,value):
    if value is None: return
    if isinstance(combo,SearchableComboBox):
        combo.refresh_search_items()
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
    return parse_identify_paths(self.ident_path_edit.text())

def _refresh_identify_previews(self,*_):
    return refresh_identify_previews(self,self._identify_paths_from_text())

def _show_identify_preview_image(self,path:Path):
    return show_identify_preview_image(self,path,STYLE)

def _remove_identify_preview_path(self,path:Path):
    paths=[p for p in self._identify_paths_from_text() if p!=path]
    self.ident_path_edit.setText(";".join(str(p) for p in paths))

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
    self._identify_capture_dir=runtime.ACCOUNT_DATA_ROOT/"identify_captures"
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
            path=getattr(self,"_identify_capture_dir",runtime.ACCOUNT_DATA_ROOT/"identify_captures")/filename
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
    paths,_=QFileDialog.getOpenFileNames(self,"选择装备截图",str(runtime.SCREENSHOT_DIR),"Images (*.png *.jpg *.jpeg *.bmp)")
    if paths:
        self.ident_path_edit.setText(";".join(paths))

def _identify_from_clipboard(self):
    cb=QApplication.clipboard()
    mime=cb.mimeData()
    if mime and mime.hasImage():
        img=cb.image()
        if not img.isNull():
            clip_path=runtime.ACCOUNT_DATA_ROOT/f"identify_clipboard_{int(time.time()*1000)}.png"
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
    self._pending_identify_clipboard_cleanup=[
        path for path,_options in image_jobs
        if is_identify_clipboard_file(path,runtime.ACCOUNT_DATA_ROOT)
    ]
    self._set_identify_busy(True,"正在解析图片...")
    self._identify_parse_worker=WorkerThread(target=lambda:self._parse_identify_images(image_jobs),parent=self)
    self._identify_parse_worker.result_ready.connect(self._on_identify_items_loaded)
    self._identify_parse_worker.error.connect(self._on_identify_error)
    self._identify_parse_worker.start()

def _parse_identify_images(self,image_jobs:list[tuple[Path,dict]]):
    p=BatchProcessor(input_dir=str(runtime.SCREENSHOT_DIR),output_file=str(runtime.USER_CONFIG_DIR/"identify_preview.json"),config_dir=str(runtime.CONFIG_DIR))
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
    _cleanup_pending_identify_clipboard_files(self)
    if not items:
        QMessageBox.warning(self,"鉴定","未从图片中识别到可鉴定的驱动或卡带。")
        return
    if not self._confirm_identify_tape_main_stats(items):
        self.ident_summary.setText("已取消鉴定")
        return
    self._load_identify_item_to_form(items[0])
    self._start_identify_items(items)

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
    se=self.scoring_engine or ScoringEngine(str(runtime.CONFIG_DIR))
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
    orchestrator=NTEPipelineOrchestrator(config_dir=str(runtime.CONFIG_DIR))
    roles=list(orchestrator.roles_db.keys())
    blueprints=orchestrator.solve_blueprints(roles)
    self._identify_blueprint_cache=(orchestrator,blueprints)
    return self._identify_blueprint_cache

def _run_identify_item(self,item):
    orchestrator,blueprints=self._get_identify_blueprints()
    scoring=ScoringEngine(str(runtime.CONFIG_DIR))
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
    return render_identify_result_page(self,getattr(self,"_identify_result_pages",[]))

def _set_identify_result_page(self,index):
    self._identify_result_page_index=index
    self._render_identify_result_page()

def _identify_result_row(self,rank,row):
    return build_identify_result_row(rank,row)

def _on_identify_error(self,err):
    self._set_identify_busy(False)
    _cleanup_pending_identify_clipboard_files(self)
    QMessageBox.critical(self,"鉴定失败",str(err))

def _cleanup_pending_identify_clipboard_files(self):
    paths=list(getattr(self,"_pending_identify_clipboard_cleanup",[]) or [])
    self._pending_identify_clipboard_cleanup=[]
    if not paths:
        return
    cleanup_identify_clipboard_files(paths,runtime.ACCOUNT_DATA_ROOT)
    remaining=[p for p in self._identify_paths_from_text() if p not in paths and p.exists()]
    self.ident_path_edit.setText(";".join(str(p) for p in remaining))
