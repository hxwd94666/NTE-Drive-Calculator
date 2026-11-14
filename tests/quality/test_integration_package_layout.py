# 验证职责包导入无外部动作，目录迁移后源码与 frozen 资源定位仍正确。
from pathlib import Path
import os
import subprocess
import sys

from src.integrations.runtime.application_runtime_paths import resolve_application_roots
from src.integrations.runtime.bundled_resources import bundled_root
from src.integrations.native_core import nte_core

ROOT = Path(__file__).resolve().parents[2]


def test_package_import_is_lazy_and_all_adapters_import_without_external_actions():
    code = r'''
import sys, importlib, pathlib
sys.dont_write_bytecode = True

def guard(event, args):
    if event in {'sqlite3.connect', 'subprocess.Popen', 'socket.connect', 'os.system'}:
        raise AssertionError('import attempted external action: ' + event)
sys.addaudithook(guard)
import src.integrations
assert not any(name.startswith('src.integrations.native_core.') for name in sys.modules)
root = pathlib.Path('src/integrations')
modules = sorted('.'.join(path.with_suffix('').parts) for path in root.rglob('*.py')
                 if path.name != '__init__.py')
for name in modules:
    importlib.import_module(name)
import src.ui.app
from PySide6.QtWidgets import QApplication
assert QApplication.instance() is None
print('IMPORTED_ADAPTERS', len(modules), 'GUI imports had no DB, socket or process actions')
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'NTE_TESTING': '1',
             'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True, encoding='utf8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'IMPORTED_ADAPTERS' in result.stdout


def test_source_resource_and_capture_core_candidates_keep_project_root(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.delattr(sys, '_MEIPASS', raising=False)
    monkeypatch.delenv('NTE_CORE_EXE', raising=False)
    monkeypatch.setattr(nte_core.shutil, 'which', lambda _name: None)
    assert resolve_application_roots() == (ROOT, ROOT)
    assert bundled_root() == ROOT
    assert ROOT / 'third_party/nte-core/bin/nte-core.exe' in nte_core.nte_core_candidates()


def test_frozen_resource_root_stays_separate_from_application_directory(tmp_path, monkeypatch):
    assets = tmp_path / 'unpacked resources'
    executable = tmp_path / 'installed program' / 'Calc.exe'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(assets), raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(nte_core.shutil, 'which', lambda _name: None)
    assert resolve_application_roots() == (assets, executable.parent)
    candidates = nte_core.nte_core_candidates()
    assert assets / 'nte-core.exe' in candidates
    assert executable.parent / 'nte-core.exe' in candidates
