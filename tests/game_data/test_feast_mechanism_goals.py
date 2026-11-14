# 验证挑战目标的独立选择、原始效果归属与未知结算边界，不将条件效果投影为常驻属性。
import sqlite3

import pytest

from tests.game_data.test_static_feast_targets import builder
from tests.game_data.test_static_feast_targets import database as database
from tests.game_data.test_static_feast_targets import upgrade_feast_schema
from tools.game_data.static_database_build_support import StaticDatabaseError
from src.services.static_catalog.monsters.feast import apply_feast_options, selected_feast_options


NTE_TEST_TIER = "core"


def goal_builder(db):
    value = builder(db)
    stage = value.rows["feast_stages"]["stage"]
    stage.update(MechanismCategoryTitle={"LocalizedString": "挑战目标"}, MechanismScoreLimit=1400,
                 MechanismOptionChoices=[{"OptionID": "goal_a"}, {"OptionID": "goal_b"}])
    value.rows["feast_options"] = {identity: {
        "OptionType": "EDiyBossOptionType::MechanismBuff", "OptionNode": {
            "OptionDesc": {"TableId": "/Game/Text/ST_UI_hpy.ST_UI_hpy", "Key": "goal_description", "LocalizedString": "当目标阵亡时，其余目标防御提升"}, "Score": 700,
            "BossBuffs": [{"AssetPathName": "/Game/Buff/Conditional.Conditional_C"},
                          {"AssetPathName": "/Game/Buff/Other.Other_C"}],
            "PlayerBuffs": [{"AssetPathName": "/Game/Buff/Player.Player_C"}]}}
        for identity in ("goal_a", "goal_b")}
    value.source_row_ids.update({("feast_options", identity): 3 for identity in value.rows["feast_options"]})
    value.rows["feast_mechanism_text"] = {"goal_description": "当目标阵亡时，其余目标防御提升"}
    value.source_row_ids[("feast_mechanism_text", "goal_description")] = 3
    return value


def test_independent_goals_keep_description_cap_and_all_recipient_bindings(database):
    value = goal_builder(database)
    value._import_feast_catalog()
    stage = dict(database.execute("SELECT * FROM feast_stage").fetchone())
    assert (stage["mechanism_category_title"], stage["mechanism_score_limit"]) == ("挑战目标", 1400)
    options = [dict(row) for row in database.execute(
        "SELECT s.*,o.* FROM feast_all_stage_option s JOIN feast_option o USING(option_id) ORDER BY category_ordinal")]
    assert [o["category_ordinal"] for o in options] == [0, 1]
    assert all(o["effect_kind"] == "mechanism" and o["add_value"] is None for o in options)
    assert all(o["description_zh"] == "当目标阵亡时，其余目标防御提升" for o in options)
    selected = selected_feast_options(options, ("goal_a", "goal_b"))
    profile = {"defense_up": .2, "health_up": .1, "resistances": []}
    assert apply_feast_options(profile, selected) == profile
    bindings = database.execute("SELECT recipient_kind,COUNT(*) FROM feast_option_effect GROUP BY recipient_kind").fetchall()
    assert [(r[0], r[1]) for r in bindings] == [("boss", 4), ("player", 2)]
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_goal_missing_membership_source_fails_instead_of_disappearing(database):
    value = goal_builder(database)
    value.rows["feast_stages"]["stage"]["MechanismOptionChoices"].append({"OptionID": "absent"})
    with pytest.raises(StaticDatabaseError, match="目标|选项"):
        value._import_feast_catalog()


def test_schema44_upgrade_preserves_legacy_options_and_does_not_invent_cap(database):
    database.execute("INSERT INTO feast_stage VALUES('legacy','旧关卡','boss_actual',0,1)")
    database.execute("INSERT INTO feast_option VALUES('old','type','health_up',NULL,.1,NULL,5,NULL,1)")
    database.execute("INSERT INTO feast_stage_option VALUES('legacy',0,0,'生命','old')")
    upgrade_feast_schema(database)
    assert database.execute("SELECT mechanism_score_limit FROM feast_stage").fetchone()[0] is None
    assert database.execute("SELECT add_value FROM feast_option").fetchone()[0] == .1
    assert database.execute("SELECT COUNT(*) FROM feast_stage_option").fetchone()[0] == 1
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("UPDATE feast_stage SET mechanism_score_limit=-1")
