# 测试鉴定与状态规则共享账号级角色权重。
"""Account-scoped weights must be shared by identification and state rules."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from src.domain.equipment.matching import EquipmentMatchContext, RoleSuitMatch


class PostActionAccountWeightTests(unittest.TestCase):
    def test_each_scoring_module_resolves_and_uses_its_own_character_ids(self):
        from src.domain.post_actions import default_post_action_config
        from src.models.equipment import Drive
        from src.services.inventory.post_action_evaluation import PostActionEvaluator

        class Scoring:
            roles_db = {"低分角色": {"character_id": 1003}, "高分角色": {"character_id": 1004}}

            def __init__(self, *_args, **_kwargs):
                pass

            def evaluate_global_inventory(self, _items):
                pass

            def get_grade_tag(self, score, _area):
                return "SSS" if score >= 2.0 else "D"

        config = default_post_action_config()
        config["discard"].update(enabled=True, role_scope="selected", selected_character_ids=[1003])
        config["lock"].update(enabled=True, role_scope="selected", selected_character_ids=[1004])
        item = Drive(uid="module-role-scope", quality="Gold", area=3, shape_id="H_3",
                     main_stats={"攻击力": 1.0, "生命值": 1.0}, sub_stats={}, role_scores={"低分角色": 0.0, "高分角色": 2.0})
        module = "src.services.inventory.post_action_evaluation"
        resolve = lambda _roles, ids, _path: ["低分角色" if value == 1003 else "高分角色" for value in ids]
        context = EquipmentMatchContext(matches=tuple(
            RoleSuitMatch(name, data["character_id"], "suit", "套装", True, frozenset({"H_3"}), frozenset())
            for name, data in Scoring.roles_db.items()))
        with patch(f"{module}.EquipmentIdentificationService.from_paths",
                   return_value=SimpleNamespace(scoring=Scoring(), context=context)), \
                patch(f"{module}._selected_role_names", side_effect=resolve):
            evaluator = PostActionEvaluator(post_actions_config=config)
            self.assertEqual("locked", evaluator.evaluate([(1, item, "normal")], [item]).state_changes[0]["target_state"])
            config["lock"]["selected_character_ids"] = [1003]
            self.assertEqual("discarded", evaluator.evaluate([(1, item, "normal")], [item]).state_changes[0]["target_state"])
            config["discard"]["selected_character_ids"] = [1004]
            self.assertEqual([], evaluator.evaluate([(1, item, "normal")], [item]).state_changes)

    def test_management_character_ids_resolve_avatar_variants_to_scoring_role(self):
        from src.services.inventory.post_action_evaluation import _selected_role_names

        self.assertEqual(
            ["主角"],
            _selected_role_names(
                {"主角": {"character_id": 1046}},
                [1051],
            ),
        )

    def test_management_custom_character_id_resolves_to_its_scoring_role(self):
        from src.services.inventory.post_action_evaluation import _selected_role_names

        class StaticDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get_logical_character_key(self, _character_id):
                return None

        with patch("src.services.inventory.post_action_evaluation.StaticGameDataDao", StaticDao):
            self.assertEqual(
                ["自建角色"],
                _selected_role_names(
                    {"自建角色": {"character_id": 9001}},
                    [9001],
                ),
            )

    def test_evaluator_passes_account_and_static_paths_to_shared_matcher(self):
        from src.domain.post_actions import default_post_action_config
        from src.services.inventory.post_action_evaluation import PostActionEvaluator

        captured = {}
        evaluated = []
        scoring = SimpleNamespace(roles_db={}, evaluate_global_inventory=evaluated.append)
        def make_matcher(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(scoring=scoring, context=EquipmentMatchContext(settings_identity="frozen"))
        config = default_post_action_config()
        config["discard"]["enabled"] = True
        with patch("src.services.inventory.post_action_evaluation.EquipmentIdentificationService.from_paths",
                   side_effect=make_matcher):
            result = PostActionEvaluator(post_actions_config=config, config_dir="test-config",
                user_database_path="account.sqlite3", static_database_path="static.sqlite3").evaluate([], [])
        self.assertEqual(Path("test-config"), captured["config_dir"])
        self.assertEqual(Path("account.sqlite3"), captured["user_database_path"])
        self.assertEqual(Path("static.sqlite3"), captured["static_database_path"])
        self.assertEqual([[]], evaluated)
        self.assertEqual("frozen", result.matching_identity)

    def test_scoring_engine_loads_custom_role_account_weights_for_scan_management(self):
        from src.optimizer.scoring import ScoringEngine

        opened_static_paths = []

        class StaticDao:
            def __init__(self, path):
                opened_static_paths.append(path)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_equipment_attributes(self):
                return [
                    {"attribute_id": "CritBase", "display_name_zh": "暴击率", "show_percent": True},
                    {"attribute_id": "AtkAdd", "display_name_zh": "攻击力", "show_percent": False},
                ]

            def list_role_template_characters(self, _preferred_ids):
                return []

        class UserDao:
            def __init__(self, _path):
                pass

            def close(self):
                return None

            def list_observed_character_ids(self):
                return ()

            def list_custom_characters(self):
                return [{"character_id": 9001, "name_zh": "自建角色"}]

            def get_character_weight_preferences(self, character_id):
                self_id = int(character_id)
                self.assertEqual(9001, self_id)
                return {
                    "property_weights": {"CritBase": 0.8, "AtkAdd": 0.25},
                    "main_property_weights": {"CritBase": 0.9},
                }

            def assertEqual(self, expected, actual):
                if expected != actual:
                    raise AssertionError(f"{actual!r} != {expected!r}")

        with TemporaryDirectory() as temp_dir:
            account_path = Path(temp_dir) / "account.sqlite3"
            static_path = Path(temp_dir) / "static.sqlite3"
            account_path.touch()
            with (
                patch("src.optimizer.scoring.StaticGameDataDao", StaticDao),
                patch("src.optimizer.scoring.UserDataDao", UserDao),
            ):
                scoring = ScoringEngine(
                    "config",
                    user_database_path=account_path,
                    static_database_path=static_path,
                )

        self.assertEqual([static_path], opened_static_paths)
        self.assertEqual(
            {"character_id": 9001, "weights": {"暴击率%": 0.8, "攻击力": 0.25}, "main_weights": {"暴击率%": 0.9}},
            scoring.roles_db["自建角色"],
        )

    def test_tape_set_display_wrapper_matches_scoring_context_and_type_filter(self):
        from src.domain.post_actions import (
            _type_range_matches,
            _usable_role_names,
        )
        from src.models.equipment import Tape

        tape = Tape(
            uid="tape-1",
            quality="Gold",
            area=15,
            set_name="森林萤火之心",
            main_stats="攻击力%",
            sub_stats={"暴击率%": 1.0},
        )
        context = EquipmentMatchContext(matches=(RoleSuitMatch(
            "零", 1001, "suit", "「森林萤火之心」", True, frozenset(), frozenset()),))

        # 视觉卡带没有正式套装 ID，名称包装在同一适配边界规范化。
        roles, mode = _usable_role_names(tape, ["零"], context)
        self.assertEqual(["零"], roles)
        self.assertEqual("matched_usable_roles", mode)
        self.assertTrue(
            _type_range_matches(
                tape,
                {"set_names": ["「森林萤火之心」"]},
            )
        )


if __name__ == "__main__":
    unittest.main()
