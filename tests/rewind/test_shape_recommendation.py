# 验证倒带形状推荐的策略、缺分与库存分配。
from __future__ import annotations

from collections import Counter

import pytest

from src.domain.rewind_shape_recommendation import (
    RewindPricingRule,
    RewindShape,
    recommend_rewind_shape_quantities,
    recommend_rewind_shapes,
    recommend_score_shortfall_shapes,
    target_percentage_score,
)
from src.services.rewind.recommendation import (
    RewindShapeRecommendationService,
)
from src.storage.sqlite.user_data_dao import UserDataDao


def test_rewind_execution_preferences_persist_only_in_the_current_account(tmp_path) -> None:
    first_database = tmp_path / "first" / "user_data.sqlite3"
    second_database = tmp_path / "second" / "user_data.sqlite3"
    with UserDataDao(first_database, account_id="first"):
        pass
    with UserDataDao(second_database, account_id="second"):
        pass

    first_service = RewindShapeRecommendationService(
        user_database_path=first_database,
        static_database_path=tmp_path / "static.sqlite3",
    )
    second_service = RewindShapeRecommendationService(
        user_database_path=second_database,
        static_database_path=tmp_path / "static.sqlite3",
    )
    first_service.save_preferences(
        {
            "target_character_ids": [1004],
            "target_threshold_mode": "custom",
            "target_custom_percent": 90.0,
            "rewind_qualities": ["purple", "gold"],
            "rewind_drive_customization": "enabled",
        }
    )

    assert first_service.load_preferences() == {
        "target_character_ids": [1004],
        "target_threshold_mode": "custom",
        "target_custom_percent": 90.0,
        "rewind_qualities": ["purple", "gold"],
        "rewind_drive_customization": "enabled",
    }
    assert second_service.load_preferences() == {}


def test_recommendation_prioritizes_common_missing_shapes() -> None:
    shapes = (
        RewindShape("shape_a", 2),
        RewindShape("shape_b", 3),
        RewindShape("shape_c", 4),
    )

    result = recommend_rewind_shapes(
        shapes=shapes,
        required_shape_ids=("shape_a", "shape_a", "shape_b", "shape_c"),
        owned_shape_counts=Counter({"shape_a": 4, "shape_b": 0, "shape_c": 1}),
        selection_limit=2,
    )

    assert [row.shape.shape_id for row in result] == ["shape_b", "shape_c"]


def test_recommendation_respects_selection_limit() -> None:
    result = recommend_rewind_shapes(
        shapes=(RewindShape("shape_a", 2),),
        required_shape_ids=("shape_a",),
        owned_shape_counts=Counter(),
        selection_limit=8,
    )

    assert len(result) == 1


def test_multiset_recommendation_can_repeat_one_shape() -> None:
    result = recommend_rewind_shape_quantities(
        shapes=(RewindShape("shape_a", 2), RewindShape("shape_b", 3)),
        shape_demand=Counter({"shape_a": 12, "shape_b": 1}),
        owned_shape_counts=Counter(),
        selection_limit=8,
    )

    assert sum(row.quantity for row in result) == 8
    assert result[0].shape.shape_id == "shape_a"
    assert result[0].quantity > 1



def test_balanced_repeats_use_largest_remainder_ratio_allocation() -> None:
    result = recommend_score_shortfall_shapes(
        shapes=(
            RewindShape("shape_a", 2),
            RewindShape("shape_b", 2),
            RewindShape("shape_c", 2),
            RewindShape("shape_d", 2),
        ),
        shortfalls=Counter({"shape_a": 1, "shape_b": 1, "shape_c": 1, "shape_d": 1}),
        # With identical stock, score gaps themselves become the normalized
        # integer priority ratio 5:3:2:1.
        score_gaps=Counter({"shape_a": 5.0, "shape_b": 3.0, "shape_c": 2.0, "shape_d": 1.0}),
        owned_shape_counts=Counter({"shape_a": 1, "shape_b": 1, "shape_c": 1, "shape_d": 1}),
        selection_limit=8,
        proportional=False,
    )

    # Base seats are 1:1:1:1.  Four open seats use the original 5:3:2:1
    # weights, yielding floors 1:1:0:0 and largest remainders for A and C.
    assert {row.shape.shape_id: row.quantity for row in result} == {
        "shape_a": 3,
        "shape_b": 2,
        "shape_c": 2,
        "shape_d": 1,
    }


def test_balanced_shortfall_reserves_one_slot_for_each_shape_not_each_drive() -> None:
    result = recommend_score_shortfall_shapes(
        shapes=(
            RewindShape("shape_a", 2),
            RewindShape("shape_b", 2),
            RewindShape("shape_c", 2),
        ),
        shortfalls=Counter({"shape_a": 2, "shape_b": 1, "shape_c": 1}),
        score_gaps=Counter({"shape_a": 4.0, "shape_b": 3.0, "shape_c": 2.0}),
        owned_shape_counts=Counter({"shape_a": 1, "shape_b": 1, "shape_c": 1}),
        selection_limit=8,
        proportional=False,
    )

    assert {row.shape.shape_id: row.quantity for row in result} == {
        "shape_a": 3,
        "shape_b": 3,
        "shape_c": 2,
    }
    assert next(row for row in result if row.shape.shape_id == "shape_a").suit_demand == 2


def test_equal_stock_two_to_one_score_gap_becomes_five_to_three_in_both_modes() -> None:
    kwargs = {
        "shapes": (RewindShape("shape_a", 2), RewindShape("shape_b", 2)),
        "shortfalls": Counter({"shape_a": 1, "shape_b": 1}),
        "score_gaps": Counter({"shape_a": 2.0, "shape_b": 1.0}),
        "owned_shape_counts": Counter({"shape_a": 1, "shape_b": 1}),
        "selection_limit": 8,
    }

    for proportional in (False, True):
        result = recommend_score_shortfall_shapes(
            **kwargs,
            proportional=proportional,
        )
        assert {row.shape.shape_id: row.quantity for row in result} == {
            "shape_a": 5,
            "shape_b": 3,
        }


def test_focused_shortfall_uses_raw_score_gaps_without_inventory_balancing() -> None:
    kwargs = {
        "shapes": (RewindShape("shape_a", 2), RewindShape("shape_b", 2)),
        "shortfalls": Counter({"shape_a": 1, "shape_b": 1}),
        "score_gaps": Counter({"shape_a": 2.0, "shape_b": 1.0}),
        "owned_shape_counts": Counter({"shape_a": 8, "shape_b": 1}),
        "selection_limit": 8,
    }

    focused = recommend_score_shortfall_shapes(**kwargs, proportional=True)
    balanced = recommend_score_shortfall_shapes(**kwargs, proportional=False)

    assert {row.shape.shape_id: row.quantity for row in focused} == {
        "shape_a": 5,
        "shape_b": 3,
    }
    assert {row.shape.shape_id: row.quantity for row in balanced} == {
        "shape_a": 2,
        "shape_b": 6,
    }


def test_focused_shortfall_reserves_only_one_slot_for_each_tiny_gap_shape() -> None:
    result = recommend_score_shortfall_shapes(
        shapes=tuple(RewindShape(f"shape_{index}", 2) for index in range(5)),
        shortfalls=Counter({f"shape_{index}": 1 for index in range(5)}),
        score_gaps=Counter(
            {
                "shape_0": 4.02,
                "shape_1": 3.78,
                "shape_2": 0.71,
                "shape_3": 0.18,
                "shape_4": 0.18,
            }
        ),
        owned_shape_counts=Counter(
            {f"shape_{index}": 1 for index in range(5)}
        ),
        selection_limit=8,
        proportional=True,
    )

    assert {row.shape.shape_id: row.quantity for row in result} == {
        "shape_0": 3,
        "shape_1": 2,
        "shape_2": 1,
        "shape_3": 1,
        "shape_4": 1,
    }


def test_custom_pool_price_and_probability_follow_the_game_rule() -> None:
    pricing = RewindPricingRule()

    assert pricing.cost_for_quantity(8) == 360
    assert pricing.cost_for_quantity(2) == 30
    assert pricing.cost_for_quantity(1) == 10
    assert sum(pricing.cost_for_quantity(2) for _ in range(4)) == 120
    assert pricing.probability_for_quantity(1) == 0.125
    assert pricing.probability_for_quantity(8) == 1.0


def test_custom_percentage_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="1.0% 与 100.0%"):
        target_percentage_score(0.0, 3)
    with pytest.raises(ValueError, match="1.0% 与 100.0%"):
        target_percentage_score(100.1, 3)



def test_target_role_picker_excludes_transformations_and_merges_avatar_variants(tmp_path) -> None:
    (tmp_path / "static.sqlite3").touch()
    class StaticDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_characters(self):
            return [
                {"character_id": 1004, "name_zh": "安魂曲", "classification": "available_character"},
                {"character_id": 1056, "name_zh": "安魂曲", "classification": "combat_transformation"},
                {"character_id": 1046, "name_zh": "「零」", "classification": "available_avatar_variant", "actor_path": "male"},
                {"character_id": 1051, "name_zh": "「零」", "classification": "available_avatar_variant", "actor_path": "female"},
            ]

        def get_character_graduation_template(self, _character_id):
            return None

    service = RewindShapeRecommendationService(
        user_database_path=tmp_path / "user.sqlite3",
        static_database_path=tmp_path / "static.sqlite3",
        static_dao_factory=StaticDao,
    )

    assert {
        role.name: role.character_id
        for role in service.list_target_roles()
    } == {"安魂曲": 1004, "「零」": 1051}
