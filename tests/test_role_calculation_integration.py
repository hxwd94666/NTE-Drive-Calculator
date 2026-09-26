# 测试计算入口投影弧盘状态但始终保留工坊基础权重。
from __future__ import annotations

from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from src.services import legacy_allocation_static_catalog as catalog_service
from src.storage.sqlite.user_data_dao import UserDataDao


def test_incomplete_reference_does_not_replace_calculation_dataset() -> None:
    from src.app.context import ApplicationPaths
    from src.features.weighted_allocation.weighted_static_catalog import (
        get_weighted_static_catalog,
    )
    from src.integrations.role_catalog_release import read_role_catalog

    root = Path(__file__).resolve().parents[1]
    release = read_role_catalog(root / "data" / "role_catalog")
    paths = ApplicationPaths.from_roots(
        root=root, app_dir=root, data_root=root,
        bundled_config_dir=root / "config", asset_dir=root / "assets",
        app_icon_path=root / "assets" / "app_icon.ico",
    )
    assert paths.equipment_allocation_database_path == paths.static_database_path
    assert paths.equipment_allocation_database_path != release.database_path
    legacy = catalog_service.build_legacy_allocation_static_catalog(
        config_dir=root / "config",
        static_database_path=paths.equipment_allocation_database_path,
    )
    by_id = {
        int(role["character_id"]): (name, role)
        for name, role in legacy.roles_db.items()
    }
    weighted = get_weighted_static_catalog(
        release.asset_root, paths.equipment_allocation_database_path,
    )
    for character_id in (1042, 1057):
        name, role = by_id[character_id]
        assert role["weights"]
        assert role["default_set"] in legacy.sets_db
        assert sum(cell == 0 for row in legacy.board_matrices[name] for cell in row) == 20
        assert character_id in weighted.plans_by_character_id


def test_allocation_source_retains_fork_permanent_properties() -> None:
    from src.app.context import ApplicationPaths
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

    root = Path(__file__).resolve().parents[1]
    paths = ApplicationPaths.from_roots(
        root=root, app_dir=root, data_root=root,
        bundled_config_dir=root / "config", asset_dir=root / "assets",
        app_icon_path=root / "assets" / "app_icon.ico",
    )
    with StaticGameDataDao(paths.equipment_allocation_database_path) as dao:
        fork = next(
            row for row in dao.list_fork_templates()
            if row["fork_id"] == "fork_DemonBlade"
        )
    assert {
        int(row["refinement_level"]): float(row["property_value"])
        for row in fork["permanent_properties"]
    } == {1: 0.16, 2: 0.20, 3: 0.24, 4: 0.28, 5: 0.32}


def test_weighted_allocation_restores_full_role_calculation() -> None:
    from src.app.context import ApplicationPaths
    from src.features.weighted_allocation.dependencies import (
        WeightedAllocationDependencies,
    )

    root = Path(__file__).resolve().parents[1]
    paths = ApplicationPaths.from_roots(
        root=root, app_dir=root, data_root=root,
        bundled_config_dir=root / "config", asset_dir=root / "assets",
        app_icon_path=root / "assets" / "app_icon.ico",
    )
    context = SimpleNamespace(
        account=SimpleNamespace(
            active_account_id="fixture",
            user_database_path=root / "accounts/fixture/user_data.sqlite3",
        ),
        generation=1,
        paths=paths,
        account_settings=object(),
    )
    dependencies = WeightedAllocationDependencies.from_context(context)
    assert dependencies.static_database_path == paths.static_database_path
    assert not dependencies.equipment_only


def test_saved_refinement_reaches_allocation_crit_projection(tmp_path) -> None:
    from src.services.official_role_page_service import load_official_role_detail
    from src.services.official_role_profile_service import (
        OfficialRoleProfileService,
        OfficialRoleProfileUpdate,
    )

    root = Path(__file__).resolve().parents[1]
    user_database = tmp_path / "user.sqlite3"
    with UserDataDao(user_database, account_id="fork-projection"):
        pass
    service = OfficialRoleProfileService(user_database)
    values = []
    for refinement in (1, 5):
        service.save_profiles([OfficialRoleProfileUpdate(
            1036, 80, 6, 0, (), False, "fork_DemonBlade", 80, 6,
            refinement, None, {}, 0,
        )])
        detail = load_official_role_detail(
            user_database, 1036, include_inventory_contexts=False,
            static_database_path=root / "data/game_static.sqlite3",
        )
        values.append(catalog_service._current_role_calculation_projection(
            detail,
        )["active_fork_crit_rate_bonus"])
    assert values[1] - values[0] == 16.0


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
