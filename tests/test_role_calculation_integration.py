# 测试计算入口投影弧盘状态但始终保留工坊基础权重。
from __future__ import annotations

from pathlib import Path

from src.services import legacy_allocation_static_catalog as catalog_service
from src.storage.sqlite.user_data_dao import UserDataDao


def test_incomplete_reference_does_not_replace_calculation_dataset() -> None:
    from src.app.context import ApplicationPaths
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
    for character_id in (1042, 1057):
        name, role = by_id[character_id]
        assert role["weights"]
        assert role["default_set"] in legacy.sets_db
        assert sum(cell == 0 for row in legacy.board_matrices[name] for cell in row) == 20


def test_saved_refinement_reaches_allocation_crit_projection(tmp_path) -> None:
    from src.services.official_role.page import load_official_role_detail
    from src.services.official_role.profiles import (
        OfficialRoleProfileService,
        OfficialRoleProfileUpdate,
    )

    root = Path(__file__).resolve().parents[1]
    user_database = tmp_path / "user.sqlite3"
    with UserDataDao(user_database, account_id="fork-projection") as dao:
        # 账号显式基础权重属于同一个正式角色 ID；精炼变化只改变面板，不重新解释权重。
        dao.seed_character_weight_preferences(
            1036, properties=[{"property_id": "CritBase", "weight": 1.7},
                              {"property_id": "AtkUp", "main_weight": 0.8}],
            source_dataset_id="fixture", source_kind="account",
        )
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
        catalog = catalog_service.build_legacy_allocation_static_catalog(
            config_dir=root / "config", user_database_path=user_database,
            static_database_path=root / "data/game_static.sqlite3",
        )
        role = next(row for row in catalog.roles_db.values() if row["character_id"] == 1036)
        assert role["active_fork_crit_rate_bonus"] == catalog_service._current_role_calculation_projection(
            detail,
        )["active_fork_crit_rate_bonus"]
        assert role["active_fork_crit_source_resolved"] is True
        assert role["weights"] == {"暴击率%": 1.7}
        assert role["main_weights"] == {"攻击力%": 0.8}
        values.append(role["active_fork_crit_rate_bonus"])
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


def test_missing_refinement_evidence_is_not_a_confirmed_zero() -> None:
    fork = {
        "fork_id": "fork-current", "name_zh": "当前弧盘",
        "permanent_properties": ({
            "refinement_level": 1, "property_id": "CritBase",
            "property_value": 0.2,
        },),
    }
    for refinement in (None, 2):
        projected = catalog_service._current_role_calculation_projection({
            "profile": {
                "fork_id": "fork-current", "fork_level": 1,
                "fork_refinement_level": refinement,
            },
            "forks": (fork,),
        })
        assert projected["active_fork_crit_rate_bonus"] is None
    # 当前弧盘身份缺失时也保留未知；不拿静态毕业模板补成已知面板。
    projected = catalog_service._current_role_calculation_projection({
        "profile": {"fork_id": "fork-unknown", "likeability_level_10_enabled": False},
        "forks": (fork,),
    })
    assert projected["default_weapon"] == ""
    assert projected["active_fork_crit_rate_bonus"] is None
    assert projected["likeability_level_10_enabled"] is False


def test_reviewed_fork_without_permanent_rows_keeps_level_crit_known() -> None:
    fork = {
        "fork_id": "fork-reviewed", "name_zh": "已审查弧盘",
        "permanent_review_status": "conditional_only",
        "upgrade_levels": ({
            "level": 80,
            "modifiers": ({"property_id": "CritBase", "value": 0.22},),
        },),
        # 成长投影要求等级与突破两份证据；空突破不是合法的零突破。
        "breakthroughs": ({"stage": 6, "max_fork_level": 80, "modifiers": ()},),
        "permanent_properties": (),
    }
    projected = catalog_service._current_role_calculation_projection({
        "profile": {
            "fork_id": "fork-reviewed", "fork_level": 80,
            "fork_refinement_level": 1,
        },
        "forks": (fork,),
    })

    assert projected["active_fork_crit_rate_bonus"] == 22.0


def test_explicit_unequipped_fork_has_confirmed_zero_crit() -> None:
    projected = catalog_service._current_role_calculation_projection({
        "profile": {"persisted": True, "fork_id": None,
                    "likeability_level_10_enabled": True},
        "forks": (),
    })

    assert projected["default_weapon"] == ""
    assert projected["active_fork_crit_rate_bonus"] == 0.0
    assert projected["likeability_level_10_enabled"] is True


