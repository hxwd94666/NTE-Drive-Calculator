# 验证 1.4 角色在倒带与状态管理读取同一独立目录。
"""Public catalog routing for newly available equipment roles."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_rewind_and_management_offer_new_reference_roles(tmp_path: Path) -> None:
    from src.features.scanning.post_action_dialog import _load_role_options
    from src.services.rewind_shape_recommendation_service import RewindShapeRecommendationService

    root = Path(__file__).resolve().parents[1] / "data" / "role_catalog"
    database = root / "game_static.sqlite3"
    assets = root / "game_ui"
    roles = RewindShapeRecommendationService(
        user_database_path=tmp_path / "unused.sqlite3",
        static_database_path=database,
        asset_root=assets,
    ).list_target_roles()
    assert {1042, 1057}.issubset({role.character_id for role in roles})
    options = _load_role_options(None, database, assets)
    avatars = {character_id: portrait for character_id, _name, portrait, _custom in options}
    assert all(Path(avatars[character_id]).is_file() for character_id in (1042, 1057))


def test_post_action_scoring_uses_the_selected_catalog() -> None:
    from src.domain.post_actions import PostActionScoreContext, default_post_action_config
    from src.services.post_action_evaluator import PostActionEvaluator

    captured: dict[str, object] = {}

    class Scoring:
        roles_db = {"角色": {"character_id": 1042}}

        def __init__(self, _config_dir, **kwargs):
            captured.update(kwargs)

        def evaluate_global_inventory(self, _inventory):
            return None

    config = default_post_action_config()
    config["discard"]["enabled"] = True
    catalog = Path("reference.sqlite3")
    with (
        patch("src.services.post_action_evaluator.ScoringEngine", Scoring),
        patch.object(PostActionScoreContext, "from_config_dir", return_value=PostActionScoreContext()),
    ):
        PostActionEvaluator(post_actions_config=config, static_database_path=catalog).evaluate([], [])
    assert captured["static_database_path"] == catalog
