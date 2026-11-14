# 验证角色展示刷新去重、原地草稿失效和表格复用，不改变毕业率或边际口径。
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QSpinBox, QTableWidget, QVBoxLayout, QWidget

from src.features.official_role import role_calculation as ui
from tests.characters.official.test_role_equipment_cards import role_detail

NTE_TEST_TIER = "core"


def test_refresh_skips_identical_inputs_but_keeps_mutable_equipment_and_owner_isolated(monkeypatch):
    app = QApplication.instance() or QApplication([])
    owners = []
    calls = []
    original = ui.calculate_official_role_margins

    def calculate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ui, "calculate_official_role_margins", calculate)
    try:
        for _ in range(2):
            owner = QWidget()
            owners.append(owner)
            detail = role_detail()
            editor = {"detail": detail, "equipment_context_key": "current", "awakening_level": QSpinBox(owner)}
            layout = QVBoxLayout(owner)
            layout.addWidget(ui._build_margin_group(owner, 1004, detail, editor))
            layout.addWidget(ui._build_weight_group(owner, 1004, detail, editor))
            tables = owner.findChildren(QTableWidget)
            initial = deepcopy(detail)
            count = len(calls)
            for _ in range(100):
                ui._refresh_role_calculations(editor)
            assert len(calls) == count
            assert detail == initial
            before = tuple(tables[0].item(i, 3).text() for i in range(tables[0].rowCount()))
            detail["equipment_contexts"]["current"]["items"][0]["sub_stats"][0]["value"] = .6
            ui._refresh_role_calculations(editor)
            assert len(calls) == count + 1
            assert tuple(tables[0].item(i, 3).text() for i in range(tables[0].rowCount())) != before
            expected = original(ui._calculation_detail(detail, editor), "current")
            assert tables[0].rowCount() == len(expected["rows"])
            assert [tables[0].item(i, 3).text() for i in range(tables[0].rowCount())] == [
                f"{row['gain_percent']:.4f}%" for row in expected["rows"]]
            editor["equipment_context_key"] = "saved"
            ui._refresh_role_calculations(editor)
            assert len(calls) == count + 2
            detail["property_weights"]["AtkUp"] = .2
            ui._refresh_role_calculations(editor)
            assert len(calls) == count + 3
        assert len(calls) == 8
    finally:
        for owner in owners:
            owner.close()
            owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_refresh_failed_input_is_retried_and_not_mistaken_for_complete():
    editor = {"detail": {"profile": {}}, "awakening_level": SimpleNamespace(value=lambda: 0)}
    calls = []

    def calculate():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("fixture")

    refresh = ui._register_calculation_refresh(editor, calculate)
    with pytest.raises(ValueError):
        refresh()
    refresh()
    refresh()
    assert len(calls) == 2
    editor["detail"]["profile"]["character_level"] = 40
    refresh()
    assert len(calls) == 3
