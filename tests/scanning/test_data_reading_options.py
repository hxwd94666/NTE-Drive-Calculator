# 验证数据读取折叠的全局持久化、选择保留和原扫描确认边界。
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from auto_sync_ui_fixture import application, dispose
from src.features.allocation.execute_page import _build_scan_mode_card
from src.features.scanning import workflow
from src.features.scanning.controller import ScanningController
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.services.preferences.data_reading_settings import DataReadingSettings


@pytest.fixture
def make_page(tmp_path, monkeypatch):
    app = application()
    roots = []
    store = LocalPreferencesStore(tmp_path / "global_ui_preferences.json")
    store.update({"theme": "light", "locale": "en-US"})
    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", lambda *args, **kwargs: True)
    monkeypatch.setattr(workflow, "_current_scanning_dependencies",
                        lambda _owner: SimpleNamespace(user_database_path=tmp_path / "unused.db"))

    def create():
        root = QWidget()
        roots.append(root)
        layout = QVBoxLayout(root)

        def card(title):
            widget = QWidget()
            QVBoxLayout(widget).addWidget(QLabel(title))
            return widget

        owner = SimpleNamespace(dialog_parent=root, _card=card, _ui_preferences={},
                                _data_reading_settings=DataReadingSettings(store), navigate=lambda _key: None)
        owner.load_reading_options_expanded = lambda: ScanningController.load_reading_options_expanded(owner)
        owner.save_reading_options_expanded = lambda expanded: ScanningController.save_reading_options_expanded(owner, expanded)
        owner._on_scan_change = lambda *args: workflow._on_scan_change(owner, *args)
        _build_scan_mode_card(owner, layout, {}, {}, {}, lambda *_args: None)
        root.show()
        app.processEvents()
        return owner

    yield create, store
    for root in roots:
        dispose(root)


def test_global_expansion_survives_page_recreation_without_overwriting_preferences(make_page):
    create, store = make_page
    first = create()
    assert first.scan_group.checkedId() == 4
    assert first.reading_options_frame.isHidden()
    assert first.reading_options_toggle.text().startswith("其他")
    first.reading_options_toggle.click()
    second = create()
    assert not second.reading_options_frame.isHidden()
    assert second.scan_group.checkedId() == 4
    assert store.read() == {"theme": "light", "locale": "en-US", DataReadingSettings.KEY: True}
    second.reading_options_toggle.click()
    assert create().reading_options_frame.isHidden()


@pytest.mark.parametrize("mode,name,parameter", [
    (1, "全量扫描", "total_count_frame"),
    (2, "增量扫描", "drone_frame"),
    (3, "离线解析", "offline_frame"),
])
def test_folding_keeps_selected_mode_and_required_parameters(make_page, mode, name, parameter):
    create, _ = make_page
    owner = create()
    owner.reading_options_toggle.click()
    owner.scan_group.button(mode).click()
    owner.reading_options_toggle.click()
    assert owner.scan_group.checkedId() == mode
    assert owner._confirmed_scan_mode_id == mode
    assert owner.reading_mode_summary.text() == "当前方式：" + name
    assert not owner.reading_mode_summary.isHidden()
    assert not getattr(owner, parameter).isHidden()
    owner.scan_group.button(4).click()
    assert owner.reading_mode_summary.isHidden()
    assert getattr(owner, parameter).isHidden()


def test_declined_scan_confirmation_keeps_actual_mode_summary(make_page, monkeypatch):
    create, _ = make_page
    owner = create()
    owner.reading_options_toggle.click()
    owner.scan_group.button(3).click()
    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", lambda *args, **kwargs: False)
    owner.scan_group.button(1).click()
    owner.reading_options_toggle.click()
    assert owner.scan_group.checkedId() == 3
    assert owner.reading_mode_summary.text() == "当前方式：离线解析"
    assert not owner.offline_frame.isHidden()


def test_failed_preference_write_keeps_original_visibility_and_other_settings(make_page, monkeypatch):
    create, store = make_page
    owner = create()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    def fail(_changes):
        raise OSError("fixture write failure")

    monkeypatch.setattr(store, "update", fail)
    owner.reading_options_toggle.click()
    assert owner.reading_options_frame.isHidden()
    assert not owner.reading_options_toggle.isChecked()
    assert store.read() == {"theme": "light", "locale": "en-US"}
    assert len(warnings) == 1
