# 验证普通导入不准备账号，显式启动保持路径、默认账号和依赖注入。
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest


def test_import_main_window_does_not_initialize_account_or_runtime_paths():
    # 新进程覆盖真实冷导入，避免被 sys.modules 缓存或替身绑定的残留影响。
    code = """
import os, pathlib, sqlite3
def forbidden(*args, **kwargs):
    raise AssertionError('ordinary import created runtime data')
os.makedirs = forbidden
pathlib.Path.mkdir = forbidden
sqlite3.connect = forbidden
before = {key: value for key, value in os.environ.items() if key.startswith('NTE_')}
import src.ui.app
assert {key: value for key, value in os.environ.items() if key.startswith('NTE_')} == before
print('IMPORT_OK: no runtime directories, SQLite connection or NTE path mutation')
"""
    environment = {**os.environ, "NTE_TESTING": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_logger_explicit_paths_treat_braces_as_literal_directory(tmp_path):
    from src.utils import logger as module

    original = module.LOG_DIR
    directory = tmp_path / "logs{literal}"
    try:
        module.set_log_dir(directory)
        session = module.enable_session_log()
        module.logger.info("literal-directory-probe")
        module.logger.complete()
        assert "literal-directory-probe" in (directory / "nte_runtime.log").read_text(encoding="utf-8")
        assert session.parent == directory
        assert "literal-directory-probe" in session.read_text(encoding="utf-8")
    finally:
        module.disable_session_log()
        module.set_log_dir(original)


def test_runtime_paths_are_read_only_until_selected(tmp_path, monkeypatch):
    from src.integrations.application_runtime_paths import resolve_application_roots

    executable = tmp_path / "install" / "app.exe"
    bundle = tmp_path / "bundle"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert resolve_application_roots() == (bundle, executable.parent)
    assert not bundle.exists() and not executable.parent.exists()


def test_writable_root_falls_back_only_for_packaged_application(tmp_path, monkeypatch):
    from src.integrations.application_runtime_paths import select_runtime_data_root

    install = tmp_path / "read-only-install"
    fallback = tmp_path / "local"
    original = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if install in path.parents or path == install:
            raise PermissionError("fixture read-only directory")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    selected = select_runtime_data_root(install, packaged=True, local_appdata=str(fallback))
    assert selected == fallback / "NTE Drive Calc"
    assert (selected / "config").is_dir()
    assert not list(selected.rglob(".nte-write-*.tmp"))


def test_explicit_runtime_prepares_only_injected_data_root(tmp_path, monkeypatch):
    from src.ui.application_runtime import prepare_application_runtime
    from src.services.characters.character_shape_bonus_service import SHARED_DATABASE_ENV
    from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV

    package = tmp_path / "public-package"
    data = tmp_path / "isolated-runtime"
    (package / "config").mkdir(parents=True)
    # 运行期新增发行文本依赖：隔离宿主显式提供同一基础目录，不走全局回退。
    import shutil
    (package / "assets/locales").mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "assets/locales/zh-CN.json",
                    package / "assets/locales/zh-CN.json")
    monkeypatch.setenv(SHARED_DATABASE_ENV, "previous-shared")
    monkeypatch.setenv(WORKSHOP_WEIGHT_TEMPLATE_ENV, "previous-template")
    runtime = prepare_application_runtime(package_root=package, app_dir=package, data_root=data)
    assert runtime.context.paths.data_root == data
    assert runtime.accounts.data_root == data
    assert runtime.context.account.active_account_id == "default"
    assert runtime.context.account.user_database_path.is_file()
    assert not (package / "accounts").exists()
    assert runtime.theme_settings is not None
    assert runtime.language_settings.startup().catalog.locale == "zh-CN"


def test_gui_entry_checks_privilege_before_preparing_runtime(monkeypatch):
    from src.ui import app, gui_startup

    events = []
    runtime = Mock()
    monkeypatch.setattr(app, "_ensure_admin", lambda: events.append("admin"))
    monkeypatch.setattr(app, "prepare_application_runtime", lambda: events.append("prepare") or runtime)
    monkeypatch.setattr(gui_startup, "run_gui", lambda *_args: events.append("gui"))
    app.run_gui()
    assert events == ["admin", "prepare", "gui"]


def test_failed_runtime_preparation_restores_previous_path_environment(tmp_path, monkeypatch):
    from src.features.accounts.manager import AccountManager
    from src.ui.application_runtime import prepare_application_runtime
    from src.services.characters.character_shape_bonus_service import SHARED_DATABASE_ENV
    from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV

    monkeypatch.setenv(SHARED_DATABASE_ENV, "existing-shared")
    monkeypatch.delenv(WORKSHOP_WEIGHT_TEMPLATE_ENV, raising=False)
    monkeypatch.setattr(AccountManager, "initialize", Mock(side_effect=RuntimeError("fixture initialization failed")))
    with pytest.raises(RuntimeError, match="fixture initialization failed"):
        prepare_application_runtime(package_root=tmp_path, app_dir=tmp_path, data_root=tmp_path / "runtime")
    assert os.environ[SHARED_DATABASE_ENV] == "existing-shared"
    assert WORKSHOP_WEIGHT_TEMPLATE_ENV not in os.environ


def test_explicit_gui_bootstrap_prepares_dpi_before_qapplication(tmp_path, monkeypatch):
    import faulthandler
    from types import SimpleNamespace
    from unittest.mock import mock_open
    from src.ui import gui_startup

    events = []
    class StopAtApplication(Exception):
        pass

    class Application:
        @staticmethod
        def setAttribute(*args):
            pass

        def __init__(self, args):
            events.append("application")
            raise StopAtApplication

    monkeypatch.setattr(gui_startup, "QApplication", Application)
    monkeypatch.setattr(gui_startup, "prepare_process_dpi_awareness", lambda: events.append("dpi"))
    monkeypatch.setattr(gui_startup, "open", mock_open(), raising=False)
    monkeypatch.setattr(faulthandler, "enable", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    import threading
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    context = SimpleNamespace(account=SimpleNamespace(log_dir=tmp_path))
    with pytest.raises(StopAtApplication):
        gui_startup.run_gui(context, None, lambda: pytest.fail("window created too early"))
    assert events == ["dpi", "application"]


@pytest.mark.parametrize("outcome", [0, 1, AttributeError("missing API"), OSError("API error")])
def test_gui_dpi_bootstrap_preserves_legacy_api_failure_semantics(monkeypatch, outcome):
    import ctypes
    from src.ui.gui_startup import prepare_process_dpi_awareness

    if sys.platform != "win32":
        pytest.skip("此行为依赖 Windows DPI API")
    calls = []
    def set_awareness():
        calls.append("system-aware")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ctypes.windll.user32, "SetProcessDPIAware", set_awareness)
    if isinstance(outcome, OSError):
        with pytest.raises(OSError) as caught:
            prepare_process_dpi_awareness()
        assert caught.value is outcome
    else:
        prepare_process_dpi_awareness()
    assert calls == ["system-aware"]
