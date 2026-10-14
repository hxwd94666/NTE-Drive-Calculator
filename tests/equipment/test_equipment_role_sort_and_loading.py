# 验证配装迟到投影、冻结槽位身份与卡带原值/满级值的单位边界。
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.features.inventory.equipment import display_loaders as equipment_display_loaders
from src.features.inventory.equipment import display_view as equipment_display_view
from src.features.inventory.equipment import plan_display_state
from src.features.inventory.equipment.display_controller import invalidate_saved_equipment_cache
from src.optimizer.contracts import ROLE_TOTAL_SCORE
from src.ui.equipment_presentation import _equip_card


def test_mutation_invalidates_inflight_equipment_read() -> None:
    prior = object()
    window = SimpleNamespace(_saved_equipment_cache_valid=True, _equip_load_token=prior)
    invalidate_saved_equipment_cache(window)
    assert not window._saved_equipment_cache_valid
    assert window._equip_load_token is not prior
    equipment_display_view._on_sqlite_equipment_display_loaded(
        window, prior, {"obsolete": {"score": 99}},
    )
    assert not hasattr(window, "_saved_equipment_states")


def test_saved_tape_projection_uses_the_full_level_main_value() -> None:
    tape = plan_display_state._sqlite_inventory_item_display(
        {
            "kind": "core",
            "uid_slot": 1,
            "uid_serial": 2,
            "item_id": "core_1",
            "suit_id": "suit_1",
            "quality": "orange",
            "main_stats": [{"property_id": "AtkUp", "value": 0.125, "percent": True}],
            "sub_stats": [],
        },
        {"suit_1": "测试空幕"},
    )

    assert tape["main_stats"] == "攻击力%"
    assert tape["main_value"] == 37.5
    assert tape["_role_main_stats"] == {"攻击力%": 12.5}


def test_saved_tape_card_displays_its_exact_main_value() -> None:
    app = QApplication.instance() or QApplication([])
    del app

    class Presentation:
        @staticmethod
        def _stat_w(*_args):
            return 1.0

        @staticmethod
        def _stat_c(*_args):
            return "#58a6ff"

    card = _equip_card(
        Presentation(),
        "测试空幕",
        "攻击力%",
        {},
        None,
        "nte-core-1-2",
        {},
        main_value=12.5,
        card_variant="inventory",
    )

    assert any(label.text() == "攻击力% 12.5%" for label in card.findChildren(QLabel))
    card.deleteLater()

def test_saved_plan_without_diff_does_not_project_whole_inventory(
    monkeypatch,
) -> None:
    def unexpected_projection(*_args, **_kwargs):
        raise AssertionError("inventory diff projection should stay lazy")

    monkeypatch.setattr(
        plan_display_state,
        "_sqlite_inventory_item_display",
        unexpected_projection,
    )
    state = plan_display_state._sqlite_plan_display_state(
        {
            "plan_id": 1,
            "source_snapshot_id": 2,
            "score": 0,
            "payload": {},
            "assignments": [],
            "allocation_locked": False,
        },
        object(),
        object(),
        inventory_by_snapshot={
            2: {
                (1, 1): {
                    "kind": "module",
                    "uid_slot": 1,
                    "uid_serial": 1,
                    "virtual": False,
                }
            }
        },
        shape_cells={},
        suit_names={},
        attribute_ids=set(),
    )

    assert state[ROLE_TOTAL_SCORE] == 0.0


def test_game_comparison_uses_first_calculation_slot_for_character() -> None:
    first = {
        "_character_id": 1003,
        "_loadout_slot_id": 11,
        "_loadout_slot_name": "方案一",
        "strategy_mode": "role_priority",
    }
    second = {
        "_character_id": 1003,
        "_loadout_slot_id": 12,
        "_loadout_slot_name": "方案二",
        "strategy_mode": "role_priority",
    }
    game_import = {
        "_character_id": 1003,
        "_loadout_slot_id": 13,
        "strategy_mode": "game_inventory",
    }
    empty_slot = {
        "_character_id": 1003,
        "_loadout_slot_id": 15,
        "_empty_slot": True,
    }
    another_character = {
        "_character_id": 1004,
        "_loadout_slot_id": 14,
        "strategy_mode": "role_priority",
    }

    slots = equipment_display_loaders._calculation_comparison_slots(
        {
            "first": first,
            "game": game_import,
            "empty": empty_slot,
            "second": second,
            "other": another_character,
        },
        1003,
    )

    assert slots == [first, second]
    assert slots[0]["_loadout_slot_id"] == 11


