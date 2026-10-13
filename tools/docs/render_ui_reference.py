# 用当前真实 Qt 控件生成离线文档示意图与按钮坐标，不创建账号或启动业务服务。
"""Render documentation only: no application composition root, database or game actions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from datetime import datetime


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'output/architecture-map/ui'


def noop(*_args, **_kwargs):
    return None


def main() -> None:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['NTE_TESTING'] = '1'  # Isolate any imported logger; does not invoke tests.
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget
    from src.app.theme import apply_app_theme
    from src.app.version import __version__
    from src.features.home.page import build_home_page
    from src.features.allocation.execute_page import (
        _build_strategy_card,
        _build_run_button,
        _build_result_card,
    )
    from src.features.inventory.equipment.display_view import _page_equipment
    from src.features.settings.work_mode_card import build_work_mode_card

    app = QApplication([])
    font_root = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('msyh.ttc', 'msyhbd.ttc', 'seguiemj.ttf'):
        path = font_root / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    if 'Microsoft YaHei' not in QFontDatabase.families():
        raise RuntimeError('Chinese preview font Microsoft YaHei is unavailable')
    app.setFont(QFont('Microsoft YaHei', 10))
    apply_app_theme(app, 'light')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = []

    def card(title):
        widget = QWidget()
        widget.setObjectName('card')
        layout = QVBoxLayout(widget)
        label = QLabel(title)
        label.setObjectName('cardTitle')
        layout.addWidget(label)
        return widget

    def capture(panel, widget, title, controls, state, height):
        widget.resize(1000, height)
        widget.show()
        app.processEvents()
        hotspots = []
        for control, row in controls:
            if not control.isVisibleTo(widget):
                raise ValueError(f'Hidden reference control: {row}')
            point = control.mapTo(widget, QPoint(0, 0))
            if point.x() < 0 or point.y() < 0 or point.x() + control.width() > widget.width() or point.y() + control.height() > widget.height():
                raise ValueError(f'Clipped reference control: {row}')
            label = control.currentText() if isinstance(control, QComboBox) else control.text().strip()
            hotspots.append({'label': label, 'row': row,
                             'x': round(point.x() / widget.width() * 100, 4),
                             'y': round(point.y() / widget.height() * 100, 4),
                             'w': round(control.width() / widget.width() * 100, 4),
                             'h': round(control.height() / widget.height() * 100, 4)})
        destination = OUTPUT / f'{panel}.png'
        if not widget.grab().save(str(destination)):
            raise RuntimeError('Could not save reference image')
        records.append({'panel': panel, 'title': title, 'state': state, 'file': destination.name,
                        'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'hotspots': hotspots})
        widget.close()

    mode_controller = SimpleNamespace(show_upgrade_guide=noop, check=noop, attach_controls=noop, select_mode=noop)
    home = QWidget()
    home_dependencies = dict(
        app_context=SimpleNamespace(paths=SimpleNamespace(cultivation_asset_root=ROOT / 'assets/game_ui')),
        work_mode_controller=mode_controller,
        work_mode_service=SimpleNamespace(settings=SimpleNamespace(auto_sync_enabled=True)),
        auto_sync_controller=SimpleNamespace(set_enabled=noop, open_restart=noop), _go=noop,
    )
    for name, value in home_dependencies.items():
        setattr(home, name, value)
    page = build_home_page(home)
    home.home_account_label.setText('文档演示账号 · 未读取真实账号')
    home.home_sync_detail.setText('离线界面示意：展示自动同步开启时的操作入口，未启动同步。')
    capture('menu-1', page, '工作台 · 同步操作区', [
        (home.home_auto_sync_toggle, '自动同步：开启'),
        (home.home_restart_sync_button, '重启同步'),
        (next(b for b in page.findChildren(QPushButton) if b.text() == '检测详情'), '检测详情'),
    ], '演示自动同步开启时的按钮布局；无真实连接或业务结果。', 640)

    allocation = SimpleNamespace(_card=card, _open_allocation_filter_settings=noop,
                                 _do_exec=noop, _save_alloc=noop, clear_calculation=noop)
    page = QWidget()
    layout = QVBoxLayout(page)
    _build_strategy_card(allocation, layout)
    _build_run_button(allocation, layout)
    _build_result_card(allocation, layout)
    allocation.result_card.setVisible(True)
    allocation.result_content_layout.addWidget(QLabel('文档示意：仅展开结果区域，不包含实际计算方案。'))
    capture('menu-3', page, '计算 · 策略、执行与结果操作区（局部）', [
        (allocation.allocation_filter_settings_button, '分配策略旁的“设置”'),
        (allocation.btn_run, '开始计算'), (allocation.btn_save, '保存装备锁定'),
    ], '真实控件的局部布局；人为展开结果区以说明保存入口，未执行计算。', 340)

    equipment = SimpleNamespace(_clear_all_equipment=noop, _preview_fast_assemble_all_roles=noop,
                                _preview_automatic_assemble_all_roles=noop)
    page = _page_equipment(equipment)
    equipment.equip_content_layout.addWidget(QLabel('文档演示：未加载账号方案。'))
    buttons = {button.text(): button for button in page.findChildren(QPushButton)}
    capture('menu-4', page, '配装 · 计算配装内容区', [
        (buttons[label], label) for label in ('清空配装', '极速装配', '自动装配')
    ], '未加载真实方案；按钮存在与演示中的可点击外观不代表运行条件满足。', 300)

    settings = SimpleNamespace(_card=card, work_mode_controller=mode_controller,
                               work_mode_service=SimpleNamespace(settings=SimpleNamespace(mode=SimpleNamespace(value='offline'))))
    page = build_work_mode_card(settings)
    capture('menu-10', page, '设置 · 工作模式卡片（局部）', [
        (page.findChild(QComboBox), '工作模式下拉框'),
        (next(b for b in page.findChildren(QPushButton) if b.text() == '检测详情'), '检测详情／重新检测'),
    ], '展示离线模式；未运行模式确认、检测、部署或清理。', 280)

    from PySide6.QtCore import QObject, Signal
    from src.features.settings.performance_card import PerformanceCard
    class PerformancePreview(QObject):
        changed = Signal()
        log_dir = OUTPUT
        set_enabled = staticmethod(noop)
        def snapshot(self):
            return {"enabled": False, "overlay": False, "overlay_state": "off", "state": "off",
                    "automatic": False, "rows": {}, "history": [], "frames": {},
                    "log_error": None, "log_path": ""}
    performance_preview = PerformancePreview()
    page = PerformanceCard(performance_preview)
    capture('settings-performance', page, '设置 · 性能监控（首次关闭）', [
        (page.toggle, '显示游戏内性能'),
        (next(b for b in page.findChildren(QPushButton) if b.text() == '性能详情…'), '性能详情'),
    ], '真实控件的离线示意；未连接游戏，未产生性能样本。', 150)

    dependencies = {Path(__file__).resolve()}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT / 'src') and path.is_file():
                dependencies.add(path)
    manifest = {
        'kind': 'offline-real-widget-reference', 'version': __version__,
        'generated': datetime.now().astimezone().isoformat(timespec='seconds'),
        'head': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True)),
        'dependencies': {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(dependencies)},
        'images': records,
    }
    (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Rendered {len(records)} offline UI references: {OUTPUT}')


if __name__ == '__main__':
    main()
