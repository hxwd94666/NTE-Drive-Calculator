# 验证精炼参数新旧官方字段只在导入边界归一，冲突及缺失不补造曲线身份。
import sqlite3

import pytest

from tools.game_data.build_static_database import StaticDatabaseBuilder
from tools.game_data.static_database_build_support import StaticDatabaseError


NTE_TEST_TIER = "core"


def import_parameter(parameter):
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE fork_star_level(pack_id, star_level, title, description, need_golds, buffs, source_row_id);
        CREATE TABLE fork_star_parameter(pack_id, star_level, ordinal, name_id, is_percent);
    """)
    builder = object.__new__(StaticDatabaseBuilder)
    builder.connection = connection
    builder.rows = {name: {} for name in ("fork_types", "fork_items", "fork_upgrades", "fork_modify",
                                          "fork_breakthroughs", "fork_buff_curves")}
    builder.rows["fork_stars"] = {"pack_fork_1": {"DataList": [parameter]}}
    builder.source_row_ids = {("fork_stars", "pack_fork_1"): 1}
    try:
        builder._import_forks()
        return connection.execute("SELECT ordinal,name_id,is_percent FROM fork_star_parameter").fetchall()
    finally:
        connection.close()


@pytest.mark.parametrize("parameter", [{"NameID": "curve_formal"}, {"NameId": "curve_formal"},
                                        {"NameID": "curve_formal", "NameId": "curve_formal"}])
def test_formal_field_spellings_preserve_parameter_identity_and_order(parameter):
    assert import_parameter({**parameter, "bIsPercent": True}) == [(0, "curve_formal", 1)]


@pytest.mark.parametrize("parameter", [{}, {"NameID": ""}, {"NameId": 7},
                                        {"NameID": "curve_a", "NameId": "curve_b"}])
def test_missing_or_conflicting_parameter_identity_fails(parameter):
    with pytest.raises(StaticDatabaseError):
        import_parameter(parameter)
