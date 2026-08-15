# 测试鉴定与状态规则共享账号级角色权重。
"""Account-scoped weights must be shared by identification and state rules."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class PostActionAccountWeightTests(unittest.TestCase):
    def test_management_character_ids_resolve_avatar_variants_to_scoring_role(self):
        from src.services.post_action_evaluator import _selected_role_names

        self.assertEqual(
            ["主角"],
            _selected_role_names(
                {"主角": {"character_id": 1046}},
                [1051],
            ),
        )

    def test_management_custom_character_id_resolves_to_its_scoring_role(self):
        from src.services.post_action_evaluator import _selected_role_names

        class StaticDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get_logical_character_key(self, _character_id):
                return None

        with patch("src.services.post_action_evaluator.StaticGameDataDao", StaticDao):
            self.assertEqual(
                ["自建角色"],
                _selected_role_names(
                    {"自建角色": {"character_id": 9001}},
                    [9001],
                ),
            )

    def test_evaluator_passes_account_database_to_scoring_engine(self):
        from src.domain.post_actions import default_post_action_config
        from src.services.post_action_evaluator import PostActionEvaluator

        captured = {}

        class FakeScoring:
            roles_db = {}

            def __init__(self, config_dir, *, user_database_path=None):
                captured["config_dir"] = config_dir
                captured["user_database_path"] = user_database_path

        config = default_post_action_config()
        config["discard"]["enabled"] = True
        with patch("src.services.post_action_evaluator.ScoringEngine", FakeScoring):
            PostActionEvaluator(
                post_actions_config=config,
                config_dir="test-config",
                user_database_path="account.sqlite3",
            ).evaluate([], [])

        self.assertEqual("test-config", captured["config_dir"])
        self.assertEqual("account.sqlite3", str(captured["user_database_path"]))

    def test_scoring_engine_loads_custom_role_account_weights_for_scan_management(self):
        from src.optimizer.scoring import ScoringEngine

        class StaticDao:
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
            account_path.touch()
            with (
                patch("src.optimizer.scoring.StaticGameDataDao", StaticDao),
                patch("src.optimizer.scoring.UserDataDao", UserDao),
            ):
                scoring = ScoringEngine("config", user_database_path=account_path)

        self.assertEqual(
            {"character_id": 9001, "weights": {"暴击率%": 0.8, "攻击力": 0.25}, "main_weights": {"暴击率%": 0.9}},
            scoring.roles_db["自建角色"],
        )

    def test_score_context_passes_account_database_to_blueprint_source(self):
        from src.domain.post_actions import PostActionScoreContext

        captured = {}

        class FakeOrchestrator:
            def __init__(self, *, config_dir, user_database_path=None):
                captured["config_dir"] = config_dir
                captured["user_database_path"] = user_database_path
                self.roles_db = {}

            def solve_blueprints(self, _role_names):
                return {}

        with patch("src.domain.post_actions.NTEPipelineOrchestrator", FakeOrchestrator):
            context = PostActionScoreContext.from_config_dir(
                "test-config", user_database_path="account.sqlite3"
            )

        self.assertFalse(context.strict)
        self.assertEqual("test-config", captured["config_dir"])
        self.assertEqual("account.sqlite3", captured["user_database_path"])

    def test_tape_set_display_wrapper_matches_scoring_context_and_type_filter(self):
        from src.domain.post_actions import (
            PostActionScoreContext,
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
        context = PostActionScoreContext(
            strict=True,
            tape_roles_by_set={"「森林萤火之心」": {"零"}},
        )

        # Contexts built before the compatibility fix may only contain the
        # official bracketed key, so lookup itself must be wrapper-tolerant.
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
