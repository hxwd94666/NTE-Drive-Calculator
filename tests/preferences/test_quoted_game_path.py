# 验证游戏路径复制引号只在设置边界规范化，持久化和旧配置使用同一身份。
import json
from dataclasses import replace
from pathlib import PureWindowsPath

import pytest

from src.domain.preferences.work_mode import WorkModeSettings
from src.services.work_modes.service import WorkModeService

NTE_TEST_TIER = "core"
GAME = r"X:\Game Files\Neverness To Everness\Client\WindowsNoEditor\HT\Binaries\Win64\HTGame.exe"


@pytest.mark.parametrize("raw", [GAME, f'"{GAME}"', f'  "{GAME}"  ', f" \t{GAME}\t "])
def test_copy_wrapping_quotes_do_not_become_path_characters(raw):
    settings = WorkModeSettings(game_executable=raw)
    assert settings.game_executable == GAME
    assert PureWindowsPath(settings.game_executable).is_absolute()
    assert PureWindowsPath(settings.game_executable).name == "HTGame.exe"


@pytest.mark.parametrize("raw", [f'"{GAME}', f'{GAME}"', f'"{GAME}" --argument'])
def test_unpaired_quotes_or_command_arguments_are_not_reinterpreted(raw):
    assert WorkModeSettings(game_executable=raw).game_executable == raw


def test_edit_save_reload_and_equivalent_path_revision(tmp_path):
    path = tmp_path / "work_mode.json"
    service = WorkModeService(path)
    service.set_game_executable(GAME)
    revision = service.operation_revision
    service.set_game_executable(f'  "{GAME}"  ')
    assert service.settings.game_executable == GAME
    assert json.loads(path.read_bytes())["game_executable"] == GAME
    assert WorkModeService(path).settings.game_executable == GAME
    # 仅复制包装变化不是游戏目标变化，不撤销同一路径的已冻结操作身份。
    assert service.operation_revision == revision


def test_existing_quoted_configuration_is_normalized_without_rewriting_on_load(tmp_path):
    path = tmp_path / "work_mode.json"
    path.write_text(json.dumps({
        "schema_version": 1, "game_executable": f'"{GAME}"',
        "revision": 12, "deployment": {"fixture": "kept"},
    }), encoding="utf-8")
    before = path.read_bytes()
    service = WorkModeService(path)
    assert service.settings.game_executable == GAME
    assert service.settings.revision == 12
    assert service.deployment_record == {"fixture": "kept"}
    assert path.read_bytes() == before
    service.set_game_executable(service.settings.game_executable)
    assert json.loads(path.read_bytes())["game_executable"] == GAME


def test_clear_and_frozen_settings_replacement():
    original = WorkModeSettings(game_executable=GAME)
    assert replace(original, game_executable='  ""  ').game_executable == ""
    assert original.game_executable == GAME
