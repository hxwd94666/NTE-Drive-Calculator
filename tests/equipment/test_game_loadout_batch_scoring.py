# 验证游戏配装批量预热与原单件评分、实际卡带主值及总分完全一致，且不逐件启动组件。
from copy import deepcopy
from unittest.mock import patch

from src.features.inventory.equipment.display.loadout_scoring import (
    score_equipment_display_state,
    score_equipment_display_states,
)
from src.ui.equipment.equipment_presentation import EquipmentPresentation
from tests.allocation.test_equipment_score_backend import engine

NTE_TEST_TIER = "core"


def presenter():
    scoring, client, _, _ = engine()
    scoring.roles_db["A"]["main_weights"] = {"攻击力%": 0.4}
    view = EquipmentPresentation(app_context=None, dialog_parent=None)
    view.update_catalog(roles_db=scoring.roles_db, scoring_engine=scoring, shape_areas={"H_4": 4})
    return view, scoring, client


def states():
    return {
        name: {
            "equipped_tape": {
                "uid": "tape-" + name,
                "main_stats": "攻击力%",
                "main_value": 18.75,
                "quality": "Gold",
                "sub_stats": {"暴击率%": 2},
            },
            "equipped_drives": [
                {
                    "uid": "drive-" + name,
                    "shape_id": "H_4",
                    "quality": "Gold",
                    "sub_stats": {"攻击力%": 6, "暴击率%": 2},
                }
            ],
        }
        for name in ("A", "B")
    }


def test_batched_game_scores_equal_legacy_per_item_and_use_one_rust_request():
    expected = states()
    current = deepcopy(expected)
    view, scoring, _ = presenter()
    for role, state in expected.items():
        score_equipment_display_state(view, role, state, scoring.roles_db)
    view, scoring, client = presenter()
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        score_equipment_display_states(view, current, scoring.roles_db)
        dispatch.assert_called_once()
    assert current == expected
    assert current["A"]["equipped_tape"]["score"] != current["B"]["equipped_tape"]["score"]
