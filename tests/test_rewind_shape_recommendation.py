# 验证倒带形状推荐的策略、缺分与库存分配。
from __future__ import annotations

from collections import Counter

from src.domain.rewind_shape_recommendation import (
    RewindPricingRule,
    RewindShape,
    recommend_rewind_plans,
    recommend_rewind_shape_quantities,
    recommend_rewind_shapes,
    recommend_score_shortfall_shapes,
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
            "rewind_qualities": ["purple", "gold"],
            "rewind_drive_customization": "enabled",
        }
    )

    assert first_service.load_preferences() == {
        "target_character_ids": [1004],
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



def test_balanced_repeats_use_score_gap_times_reciprocal_stock_with_top_ratio_remainder() -> None:
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

    # Base seats are 1:1:1:1.  After subtracting that base from the ratio,
    # 4:2:1:0 shares four open seats as 2:1:0:0; the one residual seat goes
    # to the highest original ratio, giving the requested 4:2:1:1 result.
    assert {row.shape.shape_id: row.quantity for row in result} == {
        "shape_a": 4,
        "shape_b": 2,
        "shape_c": 1,
        "shape_d": 1,
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

        def list_active_loadout_plans_by_role(self):
            return {
                "角色一": {
                    "source_snapshot_id": 3,
                    "payload": {"assignment_scores": {"nte-module-4-5": 5.0, "nte-module-6-7": 99.0}},
                    "assignments": [
                        {"kind": "module", "uid_slot": 4, "uid_serial": 5},
                        {"kind": "module", "uid_slot": 6, "uid_serial": 7},
                    ],
                }
            }

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

    # S is 50% of the current grading ceiling: 4 * 10 * 0.5 = 20.
    # The plan saved this drive at 5, so Hen2 contributes a 15-point gap.
    recommendation = next(
        row for row in analysis.recommendations
        if row.shape.shape_id == "EquipmentGeometry_Hen2"
    )
    assert recommendation.quality_gap == 15.0
    assert analysis.required_count == 1
    assert dict(analysis.owned_shape_counts) == {
        "EquipmentGeometry_Hen2": 0,
        "EquipmentGeometry_Hen3": 1,
    }
    assert dict(service.load_owned_shape_counts()) == {
        "EquipmentGeometry_Hen2": 0,
        "EquipmentGeometry_Hen3": 1,
    }
    # The score above S for Hen3 is deliberately ignored, rather than offsetting
    # the Hen2 deficit or becoming a negative gap.
    assert all(row.shape.shape_id != "EquipmentGeometry_Hen3" for row in analysis.recommendations)
    assert 3 in UserDao.requested_snapshots


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
