# 验证权重页面只读解析默认值且不覆盖账号手动权重。
from pathlib import Path

from src.services.characters.character_weight_service import ensure_account_character_weights, save_account_character_weights
from src.storage.sqlite.user_data_dao import UserDataDao

NTE_TEST_TIER = "core"


STATIC = Path(__file__).resolve().parents[2] / "data/game_static.sqlite3"


def test_editor_read_matches_persisted_defaults_without_seeding(tmp_path):
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    read = ensure_account_character_weights(database, (1051,), static_database_path=STATIC, persist_defaults=False)
    with UserDataDao(database) as dao:
        assert dao.get_character_weight_preferences(1051) is None
    cached = ensure_account_character_weights(database, (1051,), static_database_path=STATIC)
    for key in ("source_kind", "property_weights", "main_property_weights"):
        assert read[1051][key] == cached[1051][key]


def test_editor_read_keeps_explicit_account_values(tmp_path):
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    save_account_character_weights(database, 1051, {"CritBase": 2.25}, main_property_weights={"CritBase": 3.5}, static_database_path=STATIC)
    read = ensure_account_character_weights(database, (1051,), static_database_path=STATIC, persist_defaults=False)
    assert read[1051]["source_kind"] == "account"
    assert read[1051]["property_weights"]["CritBase"] == 2.25
    assert read[1051]["main_property_weights"]["CritBase"] == 3.5


def test_all_public_roles_keep_dao_zero_weight_filtering(tmp_path):
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    read = ensure_account_character_weights(database, static_database_path=STATIC, persist_defaults=False)
    cached = ensure_account_character_weights(database, static_database_path=STATIC)
    assert read.keys() == cached.keys()
    for cid, model in read.items():
        for key in ("property_weights", "main_property_weights"):
            assert model[key] == cached[cid][key]
            assert all(value > 0 for value in model[key].values())
