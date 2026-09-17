# 测试计算入口投影弧盘状态但始终保留工坊基础权重。
from __future__ import annotations

from unittest.mock import patch

from src.services import legacy_allocation_static_catalog as catalog_service
from src.storage.sqlite.user_data_dao import UserDataDao


def test_legacy_calculation_projection_uses_current_role_profile() -> None:
    detail = {
        "profile": {
            "persisted": True,
            "fork_id": "fork-current",
            "fork_level": 20,
            "fork_breakthrough_stage": 1,
            "fork_refinement_level": 2,
        },
        "forks": ({
            "fork_id": "fork-current",
            "name_zh": "当前弧盘",
            "upgrade_levels": ({
                "level": 20,
                "modifiers": ({"property_id": "CritBase", "value": 0.10},),
            },),
            "breakthroughs": ({
                "stage": 1,
                "max_fork_level": 20,
                "modifiers": (),
            },),
            "permanent_properties": ({
                "refinement_level": 2,
                "property_id": "CritBase",
                "property_value": 0.025,
            },),
        },),
    }
    projected = catalog_service._current_role_calculation_projection(detail)

    assert projected["default_weapon"] == "当前弧盘"
    assert projected["active_fork_crit_rate_bonus"] == 12.5
    assert "weights" not in projected
    assert "main_weights" not in projected


def test_legacy_catalog_freezes_role_projection_when_account_exists(tmp_path) -> None:
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="role-projection"):
        pass
    projection = {
        "default_weapon": "当前弧盘",
        "active_fork_crit_rate_bonus": 12.5,
    }

    def load_base_weights(engine) -> None:
        class BaseWeights(dict):
            def get(self, _key, _default=None):
                return {
                    "weights": {"暴击率%": 1.0},
                    "main_weights": {"攻击力%": 0.8},
                }

        engine.roles_db = BaseWeights()

    with (
        patch.object(
            catalog_service.ScoringEngine,
            "_load_roles_from_sqlite",
            load_base_weights,
        ),
        patch.object(catalog_service, "load_official_role_detail", return_value={}),
        patch.object(
            catalog_service,
            "_current_role_calculation_projection",
            return_value=projection,
        ) as project,
    ):
        catalog = catalog_service.build_legacy_allocation_static_catalog(
            config_dir="config",
            user_database_path=database,
        )

    role = next(iter(catalog.roles_db.values()))
    assert role["default_weapon"] == "当前弧盘"
    assert role["active_fork_crit_rate_bonus"] == 12.5
    assert role["weights"] == {"暴击率%": 1.0}
    assert role["main_weights"] == {"攻击力%": 0.8}
    assert project.called
