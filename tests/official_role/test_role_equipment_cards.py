# 验证角色装备详情不依赖计算页加载，始终展示当前边际权重的分数和评级。
from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QSpinBox, QVBoxLayout, QWidget

from src.domain.allocation_rating import allocation_grade
from src.features.inventory.warehouse.result_card import WarehouseResultCard
from src.features.official_role.role_calculation import _build_margin_group
from src.features.official_role.role_equipment import _build_drive_summary_group, _build_equipment_cards_group
from src.services.official_role.marginal_scoring import (
    calculate_official_role_final_weights, calculate_official_role_hidden_equipment_score,
)
from src.ui.widgets import NoWheelComboBox

NTE_TEST_TIER = "core"


def role_detail():
    attributes = {key: {"display_name_zh": name, "filter_name_zh": name, "show_percent": True}
                  for key, name in (("CritBase", "暴击率%"), ("CritDamageBase", "暴击伤害%"),
                                    ("AtkUp", "攻击力%"), ("DamageUpGeneralBase", "伤害增加%"))}
    items = [
        {"kind": "core", "uid_slot": 1, "uid_serial": 10, "quality": "gold", "level": 20,
         "main_stats": [{"property_id": "AtkUp", "value": .375, "percent": True}],
         "sub_stats": [{"property_id": "CritBase", "value": .1, "percent": True}]},
        {"kind": "module", "uid_slot": 2, "uid_serial": 20, "quality": "gold", "level": 20,
         "geometry": "EquipmentGeometry_Hen2", "grid_count": 2,
         "sub_stats": [{"property_id": "CritDamageBase", "value": .2, "percent": True}]},
    ]
    saved = deepcopy(items)
    saved[0]["sub_stats"][0]["value"] = .7
    return {"character": {"character_id": 1004, "element_type": "Element_CHAOS"},
            "profile": {"character_level": 80, "breakthrough_stage": 6, "awakening_level": 0},
            "growth_rows": [{"level": 80, "breakthrough_stage": 6, "hp_base": 1000,
                             "atk_base": 200, "def_base": 100}],
            "config_dir": str(Path(__file__).resolve().parents[2] / "config"),
            "attributes": attributes, "property_weights": {key: 1 for key in attributes},
            "main_property_weights": {"AtkUp": 1},
            "equipment_contexts": {
                "current": {"title": "当前配装", "items": items},
                "saved": {"title": "已保存配装", "items": saved,
                          "assignment_scores": {"nte-core-1-10": 123.45}},
            }}


def test_cards_refresh_marginal_scores_without_allocation_page_and_preserve_frozen_inputs():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.scoring_engine = None  # 首先打开角色页；计算页的目录与评分引擎尚未加载。
    detail = role_detail()
    original = deepcopy(detail)
    editor = {"detail": detail, "equipment_context_key": "current", "awakening_level": QSpinBox(window)}
    layout = QVBoxLayout(window)
    layout.addWidget(_build_margin_group(window, 1004, detail, editor))
    group = _build_drive_summary_group(window, detail, editor, allow_replacement=False)
    layout.addWidget(group)
    selector = group.findChild(NoWheelComboBox, "officialRoleEquipmentContextSelector")
    scores = []
    try:
        for key in ("current", "saved", "current"):
            selector.setCurrentIndex(selector.findData(key))
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            cards = group.findChildren(WarehouseResultCard)
            weights = calculate_official_role_final_weights(detail, key)
            expected = [calculate_official_role_hidden_equipment_score(
                detail, item, property_weights=weights["property_weights"],
                main_property_weights=weights["main_property_weights"])
                for item in detail["equipment_contexts"][key]["items"]]
            assert all(value > 0 for value in expected)
            assert [card.score for card in cards] == pytest.approx(expected)
            assert [card.grade for card in cards] == [allocation_grade(value, area)
                                                     for value, area in zip(expected, (15, 2))]
            assert group.findChild(QLabel, "officialRoleLoadoutScore") is None
            assert not any("配装评分" in label.text() or "各装备评分之和" in label.toolTip()
                           for label in group.findChildren(QLabel))
            assert any(label.text().startswith("直伤收益:") for label in group.findChildren(QLabel))
            scores.append(expected)
        assert scores[0] != scores[1] and scores[0] == scores[2]
        assert detail == original  # 展示重算不改历史分数、装备快照或基础权重。
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_theory_placeholder_does_not_invent_equipment_scores():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    detail = role_detail()
    detail["equipment_contexts"]["theory"] = {"core_item_id": "template"}
    group = _build_equipment_cards_group(window, detail, "theory")
    try:
        cards = group.findChildren(WarehouseResultCard)
        assert len(cards) == 1 and cards[0].score is None and not cards[0].grade
        app.processEvents()
    finally:
        group.deleteLater()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
