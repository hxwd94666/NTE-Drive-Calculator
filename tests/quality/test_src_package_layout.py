# 验证职责归类后的冷导入不读取账号或启动进程，正式入口复用同一类身份。
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_categorized_modules_and_gui_entry_import_without_external_actions():
    code = r'''
import sys, pathlib, importlib
sys.dont_write_bytecode = True

def guard(event, args):
    if event in {'sqlite3.connect', 'subprocess.Popen', 'socket.connect', 'os.system'}:
        raise AssertionError('ordinary import attempted external action: ' + event)
sys.addaudithook(guard)
roots = ['src/domain', 'src/ui', 'src/features/allocation', 'src/features/inventory/equipment',
         'src/features/teams', 'src/features/toolbox/cultivation', 'src/services/allocation',
         'src/services/equipment', 'src/services/damage', 'src/services/runtime',
         'src/storage/sqlite/teams', 'src/optimizer', 'src/services/inventory',
         'src/services/official_role', 'src/features/scanning', 'src/features/inventory/warehouse']
modules = sorted({'.'.join(path.with_suffix('').parts) for root in roots
                  for path in pathlib.Path(root).rglob('*.py') if path.name != '__init__.py'})
for name in modules:
    importlib.import_module(name)
import main
from src.ui import app
from src.ui.equipment.equipment_presentation import EquipmentPresentation
from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.optimizer.scoring import ScoringEngine
from src.app.facade import NTEAppFacade
from PySide6.QtWidgets import QApplication
assert app.EquipmentPresentation is EquipmentPresentation
assert callable(StatCatalog) and callable(ScoringEngine) and callable(NTEAppFacade)
assert QApplication.instance() is None
print('COLD_IMPORT_OK', len(modules), 'adapters/features/domain modules; no DB, socket or process actions')
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'NTE_TESTING': '1',
             'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True, encoding='utf8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'COLD_IMPORT_OK' in result.stdout

