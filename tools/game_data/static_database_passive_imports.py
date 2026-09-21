# 将技能目录中的被动和固有能力按正式身份导入图鉴。
from tools.game_data.static_database_build_support import StaticDatabaseError, enum_tail


def import_catalog_passives(builder):
    for character_id, in builder.connection.execute("SELECT character_id FROM character"):
        row = builder.rows["character_abilities"].get(str(character_id), {})
        for field, kind in (("PassiveAbilityList", "Passive"), ("PeculiarityList", "Peculiarity")):
            for entry in row.get(field) or ():
                identity, value = entry.get("Key"), entry.get("Value") or {}
                if not isinstance(identity, str) or not identity or enum_tail(value.get("AbilityType")) != kind:
                    raise StaticDatabaseError(f"角色被动身份或类型无效：{character_id}/{field}")
                index = value.get("AbilityIndex")
                if isinstance(index, bool) or not isinstance(index, int):
                    raise StaticDatabaseError(f"角色被动序号无效：{character_id}/{identity}")
                stages = [item.get("RequireTupoLevel") for item in value.get("LevelsCostItems") or ()]
                if any(isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 6 for stage in stages):
                    raise StaticDatabaseError(f"角色被动解锁阶段无效：{character_id}/{identity}")
                builder.connection.execute("INSERT INTO catalog_character_passive VALUES (?,?,?,?,?,?)", (
                    character_id, identity, kind, index, min(stages) if stages else None,
                    builder.source_row_id("character_abilities", str(character_id)),
                ))
