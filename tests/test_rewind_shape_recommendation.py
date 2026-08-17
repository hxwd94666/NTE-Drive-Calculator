# 验证倒带形状推荐的策略、缺分与库存分配。
from __future__ import annotations

from collections import Counter

import pytest

from src.domain.rewind_shape_recommendation import (
    RewindPricingRule,
    RewindShape,
    recommend_rewind_plans,
    recommend_rewind_shape_quantities,
    recommend_rewind_shapes,
    recommend_score_shortfall_shapes,
    target_percentage_score,
)
from src.services.rewind_shape_recommendation_service import (
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


def test_plans_use_the_documented_linear_repeat_price() -> None:
    plans = recommend_rewind_plans(
        shapes=(RewindShape("shape_a", 2), RewindShape("shape_b", 2)),
        shape_demand=Counter({"shape_a": 8, "shape_b": 1}),
        owned_shape_counts=Counter(),
        quality_gaps=Counter({"shape_a": 5}),
        pricing_rule=RewindPricingRule(base_cost=10, repeat_increment=5),
        selection_limit=3,
    )

    score_plan = next(plan for plan in plans if plan.key == "score")
    selected_a = next(
        row.quantity for row in score_plan.recommendations
        if row.shape.shape_id == "shape_a"
    )
    assert selected_a > 1
    assert score_plan.total_cost >= 10 + 15
    economy_plan = next(plan for plan in plans if plan.key == "economy")
    assert economy_plan.total_cost <= score_plan.total_cost


def test_custom_pool_price_and_probability_follow_the_game_rule() -> None:
    pricing = RewindPricingRule()

    assert pricing.cost_for_quantity(8) == 360
    assert pricing.cost_for_quantity(2) == 30
    assert pricing.cost_for_quantity(1) == 10
    assert sum(pricing.cost_for_quantity(2) for _ in range(4)) == 120
    assert pricing.probability_for_quantity(1) == 0.125
    assert pricing.probability_for_quantity(8) == 1.0


def test_focused_shortfall_uses_persisted_drive_scores_from_plan_snapshot(tmp_path) -> None:
    class StaticDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_characters(self):
            return [{"character_id": 1, "name_zh": "角色一"}]

        def list_shapes(self):
            return [
                {"shape_id": "EquipmentGeometry_Hen2", "cell_count": 2},
                {"shape_id": "EquipmentGeometry_Hen3", "cell_count": 3},
            ]

    database_path = tmp_path / "user.sqlite3"
    database_path.touch()

    class UserDao:
        requested_snapshots: list[int] = []

        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def current_inventory_snapshot_id(self):
            return 9

        def inventory_snapshot_summary(self, snapshot_id):
            assert snapshot_id == 9
            return {"source": "nte_core"}

        def list_inventory_items(self, snapshot_id, *, kind):
            assert kind == "module"
            self.requested_snapshots.append(snapshot_id)
            if snapshot_id == 9:
                return [{"uid_slot": 1, "uid_serial": 1, "geometry": "Hen3", "grid_count": 3}]
            assert snapshot_id == 3
            return [
                {"uid_slot": 4, "uid_serial": 5, "geometry": "Hen2", "grid_count": 4},
                {"uid_slot": 6, "uid_serial": 7, "geometry": "Hen3", "grid_count": 3},
            ]

        def list_current_loadout_slot_plans(self):
            return [
                {
                    "slot": {"character_id": 9001, "slot_key": "primary"},
                    "plan": {
                        "character_id": 9001,
                        "source_snapshot_id": 3,
                        "payload": {"assignment_scores": {"nte-module-4-5": 5.0}},
                        "assignments": [{"kind": "module", "uid_slot": 4, "uid_serial": 5}],
                    },
                },
                {
                    "slot": {"character_id": 9001, "slot_key": "second"},
                    "plan": {
                        "character_id": 9001,
                        "source_snapshot_id": 3,
                        "payload": {"assignment_scores": {"nte-module-6-7": 5.0}},
                        "assignments": [{"kind": "module", "uid_slot": 6, "uid_serial": 7}],
                    },
                },
            ]

        def list_active_loadout_plans_by_role(self):
            raise AssertionError("current saved slots must win over any legacy projection")

        def list_custom_characters(self):
            return [{"character_id": 9001, "name_zh": "自建角色", "target_suit_id": "Suit_Custom"}]

    service = RewindShapeRecommendationService(
        user_database_path=database_path,
        static_database_path=tmp_path / "static.sqlite3",
        user_dao_factory=UserDao,
        static_dao_factory=StaticDao,
    )

    analysis = service.analyze_for_targets(
        target_character_ids=(9001,),
        primary_character_ids=(9001,),
        strategy="focused",
        target_grade="S",
    )

    # S is 50% of the current grading ceiling: 4 * 10 * 0.5 = 20.
    # The plan saved this drive at 5, so Hen2 contributes a 15-point gap.
    recommendation = next(
        row for row in analysis.recommendations
        if row.shape.shape_id == "EquipmentGeometry_Hen2"
    )
    assert recommendation.quality_gap == 15.0
    assert dict(analysis.owned_shape_counts) == {
        "EquipmentGeometry_Hen2": 0,
        "EquipmentGeometry_Hen3": 1,
    }
    assert dict(service.load_owned_shape_counts()) == {
        "EquipmentGeometry_Hen2": 0,
        "EquipmentGeometry_Hen3": 1,
    }
    # Both current slots of the selected role participate. Their gaps are
    # accumulated independently; one slot never overwrites another.
    assert {row.shape.shape_id for row in analysis.recommendations} == {
        "EquipmentGeometry_Hen2",
        "EquipmentGeometry_Hen3",
    }
    assert analysis.required_count == 2
    assert 3 in UserDao.requested_snapshots


def test_custom_percentage_uses_each_drive_area_without_changing_saved_scores(tmp_path) -> None:
    """A custom target applies per current slot and never rescales item scores."""

    class StaticDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_characters(self):
            return []

        def list_shapes(self):
            return [
                {"shape_id": "EquipmentGeometry_Hen2", "cell_count": 4},
                {"shape_id": "EquipmentGeometry_Hen3", "cell_count": 3},
            ]

        def list_equipment_attributes(self):
            return []

    class UserDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def current_inventory_snapshot_id(self):
            return 7

        def inventory_snapshot_summary(self, _snapshot_id):
            return {"source": "vision"}

        def list_inventory_items(self, _snapshot_id, *, kind):
            assert kind == "module"
            return [
                {"uid_slot": 1, "uid_serial": 1, "geometry": "Hen2", "grid_count": 4},
                {"uid_slot": 2, "uid_serial": 2, "geometry": "Hen3", "grid_count": 3},
            ]

        def list_current_loadout_slot_plans(self):
            return [
                {
                    "slot": {"character_id": 9001, "slot_key": "primary"},
                    "plan": {
                        "character_id": 9001,
                        "source_snapshot_id": 7,
                        "payload": {"assignment_scores": {"nte-module-1-1": 36.0}},
                        "assignments": [{"kind": "module", "uid_slot": 1, "uid_serial": 1}],
                    },
                },
                {
                    "slot": {"character_id": 9001, "slot_key": "second"},
                    "plan": {
                        "character_id": 9001,
                        "source_snapshot_id": 7,
                        "payload": {"assignment_scores": {"nte-module-2-2": 26.9}},
                        "assignments": [{"kind": "module", "uid_slot": 2, "uid_serial": 2}],
                    },
                },
            ]

        def list_custom_characters(self):
            return [{"character_id": 9001, "name_zh": "自建角色", "target_suit_id": "Suit_Custom"}]

    database_path = tmp_path / "user.sqlite3"
    database_path.touch()
    service = RewindShapeRecommendationService(
        user_database_path=database_path,
        static_database_path=tmp_path / "static.sqlite3",
        user_dao_factory=UserDao,
        static_dao_factory=StaticDao,
    )

    analysis = service.analyze_for_targets(
        target_character_ids=(9001,),
        strategy="balanced",
        target_grade="ACE",
        target_custom_percent=90.0,
    )

    # 4-grid 36.0 equals its 90% goal (40 * .9), while the 3-grid score 26.9
    # is below its independent 27.0 goal.  The custom character's two slots
    # must remain independent active-plan inputs.
    assert analysis.required_count == 1
    assert analysis.recommendations[0].shape.shape_id == "EquipmentGeometry_Hen3"
    assert analysis.recommendations[0].quality_gap == pytest.approx(0.1)
    assert target_percentage_score(90.0, 4) == 36.0


def test_custom_percentage_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="1.0% 与 100.0%"):
        target_percentage_score(0.0, 3)
    with pytest.raises(ValueError, match="1.0% 与 100.0%"):
        target_percentage_score(100.1, 3)


def test_focused_shortfall_reports_eight_slot_capacity_before_claiming_no_low_score_drive(tmp_path) -> None:
    """Focused mode must distinguish an eight-shape overflow from no plan input."""

    class StaticDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_characters(self):
            return [{"character_id": 1, "name_zh": "角色一"}]

        def list_shapes(self):
            return [
                {"shape_id": f"EquipmentGeometry_Shape{index}", "cell_count": 2}
                for index in range(9)
            ]

        def list_equipment_attributes(self):
            return []

    class UserDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def current_inventory_snapshot_id(self):
            return 9

        def inventory_snapshot_summary(self, snapshot_id):
            assert snapshot_id == 9
            return {"source": "nte_core"}

        def list_inventory_items(self, snapshot_id, *, kind):
            assert snapshot_id == 9
            assert kind == "module"
            return [
                {
                    "uid_slot": index + 1,
                    "uid_serial": index + 1,
                    "geometry": f"Shape{index}",
                    "grid_count": 2,
                }
                for index in range(9)
            ]

        def list_current_loadout_slot_plans(self):
            return [
                {
                    "slot": {"character_id": 1, "slot_key": "primary"},
                    "plan": {
                        "character_id": 1,
                        "source_snapshot_id": 9,
                        "payload": {
                            "assignment_scores": {
                                f"nte-module-{index + 1}-{index + 1}": 0.0
                                for index in range(9)
                            }
                        },
                        "assignments": [
                            {"kind": "module", "uid_slot": index + 1, "uid_serial": index + 1}
                            for index in range(9)
                        ],
                    },
                }
            ]

    database_path = tmp_path / "user.sqlite3"
    database_path.touch()
    service = RewindShapeRecommendationService(
        user_database_path=database_path,
        static_database_path=tmp_path / "static.sqlite3",
        user_dao_factory=UserDao,
        static_dao_factory=StaticDao,
    )

    analysis = service.analyze_for_targets(
        target_character_ids=(1,),
        primary_character_ids=(1,),
        strategy="focused",
        target_grade="S",
    )

    assert analysis.required_count == 9
    assert analysis.recommendations == ()
    assert analysis.notice == "所需驱动超过 8 个，建议降低评分等级或使用随机倒带抽取。"

    no_shortfall = service.analyze_for_targets(
        target_character_ids=(1,),
        primary_character_ids=(1,),
        strategy="focused",
        target_grade="D",
    )

    assert no_shortfall.required_count == 0
    assert no_shortfall.notice == "已读取所选角色的保存方案；没有低于 D 评分等级的已装配驱动。"


def test_target_role_picker_includes_account_custom_roles(tmp_path) -> None:
    class StaticDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_characters(self):
            return [{"character_id": 1004, "name_zh": "安魂曲"}]

        def get_character_graduation_template(self, _character_id):
            return None

    class UserDao:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_custom_characters(self):
            return [{"character_id": 9001, "name_zh": "自建角色", "target_suit_id": "Suit_Custom"}]

    database_path = tmp_path / "user.sqlite3"
    database_path.touch()
    service = RewindShapeRecommendationService(
        user_database_path=database_path,
        static_database_path=tmp_path / "static.sqlite3",
        user_dao_factory=UserDao,
        static_dao_factory=StaticDao,
    )

    assert [(role.character_id, role.name, role.default_suit_id, role.is_custom) for role in service.list_target_roles()] == [
        (1004, "安魂曲", None, False),
        (9001, "自建角色", "Suit_Custom", True),
    ]


def test_target_role_picker_excludes_transformations_and_merges_avatar_variants(tmp_path) -> None:
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
