# 验证成长纯值提取的原缺失错误、空列表警告和最大等级/突破选择，不读取真实账号或组件。
import pytest

import src.services.official_role.native_profiles as profiles
from src.storage.sqlite.user_data_support import UserDataValidationError

NTE_TEST_TIER = "core"


class StaticRows:
    rows = []
    character_present = True

    def __init__(self, _path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get_character(self, character_id):
        if not self.character_present:
            return None
        return {"character_id": character_id, "name_zh": f"角色 {character_id}"}

    def list_character_panel_growth(self, _character_id):
        return self.rows

    def list_forks(self):
        return []


@pytest.fixture
def static_rows(monkeypatch):
    monkeypatch.setattr(profiles, "StaticGameDataDao", StaticRows)
    monkeypatch.setattr(StaticRows, "rows", [])
    monkeypatch.setattr(StaticRows, "character_present", True)
    return StaticRows


def test_missing_character_keeps_formal_catalog_error(static_rows):
    static_rows.character_present = False
    with pytest.raises(UserDataValidationError) as raised:
        profiles.load_template_growth_defaults((100,), static_database_path="fake-static")
    assert str(raised.value) == "角色状态包含当前正式静态目录中不存在的角色"


def test_empty_growth_keeps_loader_error_and_batch_warning(static_rows):
    with pytest.raises(UserDataValidationError) as raised:
        profiles.load_template_growth_defaults((100,), static_database_path="fake-static")
    assert str(raised.value) == "正式角色模板缺少成长数据，无法核对稀疏角色状态"
    patches, defaults, warnings = profiles.prepare_native_role_patches(
        [{"character_id": 100, "character_level": 40}],
        static_database_path="fake-static", growth_defaults_loader=None,
    )
    assert (patches, defaults) == ([], {})
    assert warnings == ("角色 100（100）：正式角色模板缺少成长数据",)


def test_equal_level_uses_maximum_stage_in_both_public_callers(static_rows):
    static_rows.rows = [
        {"level": 20, "breakthrough_stage": 0},
        {"level": 40, "breakthrough_stage": 0},
        {"level": 40, "breakthrough_stage": 1},
    ]
    expected = {100: {"character_level": 40, "breakthrough_stage": 1}}
    assert profiles.load_template_growth_defaults(
        (100,), static_database_path="fake-static",
    ) == expected
    patches, defaults, warnings = profiles.prepare_native_role_patches(
        [{"character_id": 100, "character_level": 40}],
        static_database_path="fake-static", growth_defaults_loader=None,
    )
    assert defaults == expected
    assert patches == [{"character_id": 100, "character_level": 40}]
    assert warnings == ()


def test_missing_batch_static_input_keeps_original_failure():
    with pytest.raises(ValueError) as raised:
        profiles.prepare_native_role_patches(
            [{"character_id": 100, "character_level": 40}],
            static_database_path=None, growth_defaults_loader=None,
        )
    assert str(raised.value) == "角色状态同步缺少本次冻结的静态数据库路径"
