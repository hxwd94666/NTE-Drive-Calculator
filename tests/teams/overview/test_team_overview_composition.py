# 验证来源简报继续读取完整主库，公共图片单独注入角色目录而非引入旧毕业计算。
from types import SimpleNamespace
from unittest.mock import Mock

from src.ui.composition import team_composition
from tests.teams.overview.team_overview_fixture import overview_case

NTE_TEST_TIER = 'core'


def composed(tmp_path, monkeypatch):
    service, team, _ = overview_case(tmp_path)
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    reference = root / 'data/role_catalog/game_static.sqlite3'
    context = SimpleNamespace(
        account=SimpleNamespace(active_account_id=service.account_id, user_database_path=service.database_path),
        generation=service.generation,
        paths=SimpleNamespace(static_database_path=service.static_database_path,
                              bundled_config_dir=root / 'config',
                              cultivation_database_path=reference,
                              game_ui_asset_root=root / 'data/role_catalog/game_ui'),
        register_account_lifecycle=lambda _owner: lambda: None)
    captured = {}
    def controller(**kwargs):
        captured.update(kwargs)
        return Mock()
    monkeypatch.setattr(team_composition, 'TeamController', controller)
    team_composition.build_team_controller(context, None)
    return captured, context, team


def test_providers_freeze_game_model_and_separate_public_art(tmp_path, monkeypatch):
    providers, context, _ = composed(tmp_path, monkeypatch)
    overview = providers['overview_service_provider']()
    management = providers['service_provider']()
    assert overview.static_database_path == context.paths.static_database_path
    assert management.static_database_path == context.paths.static_database_path
    assert management.asset_root == context.paths.game_ui_asset_root


def test_actual_composed_overview_has_four_frozen_equipment_sources(tmp_path, monkeypatch):
    providers, _context, team = composed(tmp_path, monkeypatch)
    result, = providers['overview_service_provider']().load((team,))
    assert result.ready_count == 4
    assert all(member.items and member.fingerprint for member in result.members)


def test_graduation_provider_freezes_account_generation_without_starting_analysis(tmp_path, monkeypatch):
    from concurrent.futures import CancelledError
    import pytest
    providers, context, _ = composed(tmp_path, monkeypatch)
    service = providers["axis_graduation_service_provider"]()
    service.checkpoint()
    context.generation += 1
    with pytest.raises(CancelledError):
        service.checkpoint()
