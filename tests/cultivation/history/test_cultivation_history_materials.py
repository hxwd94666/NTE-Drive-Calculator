# 验证历史材料中未知、手填零、来源及隐藏数量的独立恢复。
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


def _materials():
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.owned_materials import CultivationOwnedMaterials

    application = QApplication.instance() or QApplication([])
    owned = CultivationOwnedMaterials(icon_lookup=lambda _item_id: None, parent=None)
    return application, owned


def test_visible_default_zero_is_not_saved_until_manually_typed():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QSpinBox
    from src.services.progression.models import CultivationMaterial

    _application, owned = _materials()
    owned.set_materials((CultivationMaterial("a", "材料", 1),))
    assert owned.quantities()["a"] == 0
    assert owned.export_history_materials() == []
    owned.select_material("a")
    QTest.keyClicks(owned.findChild(QSpinBox).lineEdit(), "0")
    assert owned.export_history_materials() == [{
        "item_id": "a", "quantity": 0, "manual_override": True,
        "source": "manual", "observed_at_utc": None,
    }]
    assert owned.apply_import({"a": 20}) == 0
    assert owned.quantities()["a"] == 0
    owned.deleteLater()


def test_restore_hidden_quantities_and_manual_zero_atomic():
    from src.services.progression.models import CultivationMaterial

    _application, owned = _materials()
    entries = [
        {"item_id": "a", "quantity": 0, "manual_override": True,
         "source": "manual", "observed_at_utc": None},
        {"item_id": "hidden", "quantity": 12, "manual_override": False,
         "source": "packet", "observed_at_utc": "2026-01-01T00:00:00Z"},
    ]
    owned.restore_history_materials(entries)
    owned.set_materials((CultivationMaterial("a", "材料", 1),))
    assert owned.export_history_materials() == entries
    assert owned.quantities()["hidden"] == 12
    with pytest.raises(ValueError):
        owned.restore_history_materials(entries + [{**entries[0], "quantity": -1}])
    assert owned.export_history_materials() == entries
    owned.clear_quantities()
    assert owned.export_history_materials() == []
    owned.deleteLater()
