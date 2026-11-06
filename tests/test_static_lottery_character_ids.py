# 验证卡池字段改名不丢失角色关系，冲突和缺失不伪装成空卡池。
import pytest

from tools.game_data.static_database_lottery_sources import lottery_character_ids
from tools.game_data.static_database_build_support import StaticDatabaseError


def test_old_and_current_formal_keys_keep_the_same_ids():
    assert lottery_character_ids({"CharacterIDs": ["1010", "1021"]}, "fixture") == [1010, 1021]
    assert lottery_character_ids({"CharacterIds": ["1010", "1021"]}, "fixture") == [1010, 1021]
    assert lottery_character_ids({"CharacterIDs": ["1010"], "CharacterIds": ["1010"]}, "fixture") == [1010]


@pytest.mark.parametrize("properties", [
    {}, {"CharacterIds": []}, {"CharacterIds": [True]}, {"CharacterIds": ["None"]},
    {"CharacterIDs": ["1010"], "CharacterIds": ["1074"]},
])
def test_missing_invalid_or_conflicting_ids_stop_import(properties):
    with pytest.raises(StaticDatabaseError):
        lottery_character_ids(properties, "fixture")
