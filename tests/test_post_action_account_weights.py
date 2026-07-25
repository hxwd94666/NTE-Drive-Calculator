"""Account-scoped weights must be shared by identification and state rules."""

import unittest
from unittest.mock import patch


class PostActionAccountWeightTests(unittest.TestCase):
    def test_evaluator_passes_account_database_to_scoring_engine(self):
        from src.features.scanning.post_action_evaluator import PostActionEvaluator
        from src.features.scanning.post_actions import default_post_action_config

        captured = {}

        class FakeScoring:
            roles_db = {}

            def __init__(self, config_dir, *, user_database_path=None):
                captured["config_dir"] = config_dir
                captured["user_database_path"] = user_database_path

        config = default_post_action_config()
        config["discard"]["enabled"] = True
        with patch("src.features.scanning.post_action_evaluator.ScoringEngine", FakeScoring):
            PostActionEvaluator(
                post_actions_config=config,
                config_dir="test-config",
                user_database_path="account.sqlite3",
            ).evaluate([], [])

        self.assertEqual("test-config", captured["config_dir"])
        self.assertEqual("account.sqlite3", str(captured["user_database_path"]))

    def test_score_context_passes_account_database_to_blueprint_source(self):
        from src.features.scanning.post_actions import PostActionScoreContext

        captured = {}

        class FakeOrchestrator:
            def __init__(self, *, config_dir, user_database_path=None):
                captured["config_dir"] = config_dir
                captured["user_database_path"] = user_database_path
                self.roles_db = {}

            def solve_blueprints(self, _role_names):
                return {}

        with patch("src.features.scanning.post_actions.NTEPipelineOrchestrator", FakeOrchestrator):
            context = PostActionScoreContext.from_config_dir(
                "test-config", user_database_path="account.sqlite3"
            )

        self.assertFalse(context.strict)
        self.assertEqual("test-config", captured["config_dir"])
        self.assertEqual("account.sqlite3", captured["user_database_path"])

    def test_tape_set_display_wrapper_matches_scoring_context_and_type_filter(self):
        from src.features.scanning.post_actions import (
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
