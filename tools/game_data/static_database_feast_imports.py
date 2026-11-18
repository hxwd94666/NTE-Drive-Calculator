# 从正式副本刷怪关系导入赏宴全部目标，预览数组只作来源事实而不承担战斗身份。
from __future__ import annotations

from tools.game_data.static_database_build_support import (
    StaticDatabaseError, asset_path, bool_int, enum_tail, optional_int, text_parts,
)

from tools.game_data.static_database_feast_mechanisms import import_mechanisms, option_score
from src.storage.sqlite.catalog.static_catalog_monster_name_queries import _numeric_identity


def _option_effect(option_id: str, buff_path: str | None) -> tuple[str, str | None]:
    normalized = (buff_path or option_id).casefold()
    if "timeop" in option_id.casefold():
        return "time_limit", None
    if "_hp_" in normalized or option_id.casefold().startswith("lifeop"):
        return "health_up", None
    if "_atk_" in normalized or option_id.casefold().startswith("attack"):
        return "attack_up", None
    for marker, damage_type in {"resistcosmos": "cosmos", "resistnature": "nature",
                               "resistincantation": "incantation", "resistchaos": "chaos",
                               "resistphyche": "psyche", "resistlakshana": "lakshana"}.items():
        if marker in normalized:
            return "resistance_up", damage_type
    return "unknown", None


class FeastImportMixin:
    def _feast_family_name(self, monster_id: str) -> str | None:
        """同一 mon/boss 数字家族在深渊刷怪与图鉴绑定中的唯一正式名；多名或无名返回 None。"""
        identity = _numeric_identity(monster_id)
        if identity is None:
            return None
        rows = self.connection.execute(
            "SELECT monster_class_path, monster_name_zh FROM abyss_monster_pool_entry "
            "WHERE trim(COALESCE(monster_name_zh, '')) <> '' UNION ALL "
            "SELECT b.monster_template_name, m.name_zh FROM monster_template_binding b "
            "JOIN monster_catalog m USING(monster_manual_id) WHERE m.name_zh IS NOT NULL").fetchall()
        names = {str(name) for formal, name in rows if _numeric_identity(formal) == identity}
        return next(iter(names)) if len(names) == 1 else None

    def _feast_targets(self, stage_id: str, difficulty_id: int):
        nodes = self.connection.execute(
            "SELECT difficulty_ordinal,difficulty_level,spawn_id FROM clone_activity_difficulty "
            "WHERE clone_id=? AND difficulty_level=?", (stage_id, difficulty_id),
        ).fetchall()
        if len(nodes) != 1:
            raise StaticDatabaseError(f"赏宴难度缺少唯一正式副本刷怪绑定：{stage_id}/{difficulty_id}")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(clone_spawn_member)")}
        level_column = "configured_monster_level" if "configured_monster_level" in columns else "NULL"
        members = self.connection.execute(
            "SELECT monster_template_name,source_row_id," + level_column + " FROM clone_spawn_member "
            "WHERE spawn_id=? ORDER BY wave_ordinal,entry_ordinal", (nodes[0][2],),
        ).fetchall()
        if not members:
            raise StaticDatabaseError(f"赏宴难度缺少正式刷怪目标：{stage_id}/{difficulty_id}")
        targets = {}
        primary_level = None
        for member_index, (template, source_id, configured_level) in enumerate(members):
            candidates = self.connection.execute(
                "SELECT static_table,monster_id FROM monster_instance_profile WHERE lower(monster_id)=lower(?)",
                (template,),
            ).fetchall()
            if len(candidates) != 1 and member_index != 0:
                continue  # 额外对象缺证据保留在正式刷怪表，由消费端标未知。
            if len(candidates) != 1:
                raise StaticDatabaseError(f"赏宴目标实例缺失或有歧义：{stage_id}/{template}")
            static_table, monster_id = candidates[0]
            variants = self.connection.execute(
                "SELECT threshold_level,profile_set,pack_id FROM monster_instance_profile_variant "
                "WHERE static_table=? AND monster_id=? AND variant_kind='clone_level' "
                "ORDER BY threshold_level", (static_table, monster_id),
            ).fetchall()
            if configured_level is not None and configured_level > 0:
                variant = next((value for value in variants if value[0] == configured_level), None)
                if variant is None:
                    if member_index == 0:
                        raise StaticDatabaseError(f"赏宴主目标明确等级缺少属性包：{stage_id}/{monster_id}")
                    continue
                if primary_level is None:
                    primary_level = variant[0]
            elif primary_level is None:
                if not 1 <= difficulty_id <= len(variants):
                    raise StaticDatabaseError(f"赏宴主目标难度缺少属性包：{stage_id}/{monster_id}/{difficulty_id}")
                variant = variants[difficulty_id - 1]
                primary_level = variant[0]
            else:
                variant = next((value for value in variants if value[0] == primary_level), None)
                if variant is None:
                    continue  # 不按附加目标自己的档位序号猜出本场等级。
            targets[(static_table, monster_id)] = (variant, source_id)
        return targets

    def _import_feast_catalog(self):
        options = self.rows["feast_options"]
        for option_id, row in sorted(options.items()):
            node = row.get("OptionNode") or {}
            buffs = node.get("BossBuffs") or ()
            buff_path = asset_path(buffs[0]) if buffs else None
            effect_kind, damage_type = _option_effect(option_id, buff_path)
            option_type = enum_tail(row.get("OptionType")) or "unknown"
            if option_type == "MechanismBuff":
                # 仅保存条件与来源；选中不意味着击杀、触发或任何受体已获得属性。
                effect_kind, damage_type = "mechanism_goal", None
            percent = node.get("AddPercent")
            self.connection.execute("INSERT INTO feast_option VALUES (?,?,?,?,?,?,?,?,?,?)", (
                option_id, option_type, effect_kind, damage_type,
                float(percent) / 100 if isinstance(percent, (int, float)) and effect_kind != "mechanism_goal" else None,
                optional_int(node.get("LimitSeconds")), option_score(node), buff_path,
                self.source_row_id("feast_options", option_id), text_parts(node.get("OptionDesc"))[0],
            ))
            for recipient, field in (("boss", "BossBuffs"), ("player", "PlayerBuffs")):
                for ordinal, effect in enumerate(node.get(field) or ()):
                    path = asset_path(effect)
                    if not path:
                        raise StaticDatabaseError(f"赏宴选项效果缺少正式路径：{option_id}/{field}/{ordinal}")
                    self.connection.execute("INSERT INTO feast_option_effect VALUES (?,?,?,?,?)", (
                        option_id, recipient, ordinal, path, self.source_row_id("feast_options", option_id),
                    ))
        import_mechanisms(self)
        for stage_id, row in sorted(self.rows["feast_stages"].items()):
            name = text_parts(row.get("DescName"))[0]
            if not name:
                raise StaticDatabaseError(f"赏宴关卡缺少名称：{stage_id}")
            clone = self.connection.execute("SELECT source_row_id FROM clone_activity WHERE clone_id=?", (stage_id,)).fetchone()
            if clone is None:
                raise StaticDatabaseError(f"赏宴关卡缺少正式副本：{stage_id}")
            difficulties = row.get("DifficultyMap") or []
            targets = {int(item["Key"]): self._feast_targets(stage_id, int(item["Key"])) for item in difficulties}
            identities = {identity for group in targets.values() for identity in group}
            if not identities:
                raise StaticDatabaseError(f"赏宴关卡没有正式目标：{stage_id}")
            # 多目标没有可冒充唯一 Boss 的默认身份；具体等级、属性包和来源随每个目标保存。
            sole = next(iter(identities))[1] if len(identities) == 1 else None
            self.connection.execute("INSERT INTO feast_stage VALUES (?,?,?,?,?,?,?)", (
                stage_id, name, sole, bool_int(row.get("bSpecialHighDifficulty")), self.source_row_id("feast_stages", stage_id),
                text_parts(row.get("MechanismCategoryTitle"))[0], optional_int(row.get("MechanismScoreLimit")),
            ))
            previews = row.get("PreviewActorArray")
            if previews is None:
                previews = [{"PreviewActorClass": row.get("PreviewActorClass")}]
            if not isinstance(previews, list) or not previews:
                raise StaticDatabaseError(f"赏宴预览列表缺失：{stage_id}")
            for ordinal, preview in enumerate(previews):
                path = asset_path(preview.get("PreviewActorClass"))
                if not path:
                    raise StaticDatabaseError(f"赏宴预览资源缺失：{stage_id}/{ordinal}")
                self.connection.execute("INSERT INTO feast_stage_member VALUES (?,?,?,?)",
                                        (stage_id, ordinal, sole if len(previews) == 1 else None, path))
            displays = {int(item["Key"]): item["Value"] for item in row.get("DifficultyDisplayData") or ()}
            for item in difficulties:
                difficulty_id = int(item["Key"])
                value, display = item["Value"], displays.get(difficulty_id, {})
                for (static_table, monster_id), (variant, source_id) in targets[difficulty_id].items():
                    names = self.connection.execute(
                        "SELECT DISTINCT m.name_zh FROM monster_template_binding b JOIN monster_catalog m USING(monster_manual_id) "
                        "WHERE lower(b.monster_template_name)=lower(?) AND m.name_zh IS NOT NULL", (monster_id,),
                    ).fetchall()
                    boss_name = str(names[0][0]) if len(names) == 1 else None
                    if len(targets[difficulty_id]) == 1:
                        boss_name = text_parts(display.get("Desc"))[0] or boss_name
                        # 关卡展示文案可能沿用换 Boss 前的旧名（如 DiyBossStage8 换为玛门后仍写墨菲克斯）；
                        # 目标数字家族在其他正式来源中有唯一名称且不一致时，以家族正式名为准。
                        family_name = self._feast_family_name(monster_id)
                        if family_name and boss_name and boss_name != family_name:
                            boss_name = family_name
                    self.connection.execute("INSERT INTO feast_stage_difficulty VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        stage_id, difficulty_id, static_table, monster_id,
                        text_parts(value.get("Desc"))[0] or str(difficulty_id), boss_name,
                        int(value.get("BaseScore") or 0), float(value.get("ScoreRate") or 0),
                        int(variant[0]), str(variant[1]), str(variant[2]),
                        asset_path(display.get("BossIconMini")) if len(targets[difficulty_id]) == 1 else None,
                        source_id, int(clone[0]),
                    ))
            for category_ordinal, category in enumerate(row.get("OptionChoices") or ()):
                for ordinal, option_id in enumerate(category.get("Options") or ()):
                    if option_id not in options:
                        raise StaticDatabaseError(f"赏宴选项不存在：{stage_id}/{option_id}")
                    self.connection.execute("INSERT INTO feast_stage_option VALUES (?,?,?,?,?)", (
                        stage_id, category_ordinal, ordinal,
                        text_parts(category.get("CategoryDesc"))[0] or f"选项 {category_ordinal + 1}", option_id,
                    ))
            # 普通分类内部互斥；挑战目标是另一个源数组，每个目标占独立分类，保留组合而不判完成。
            seen = set()
            for ordinal, choice in enumerate(row.get("MechanismOptionChoices") or ()):
                identity = choice.get("OptionID")
                if identity not in options or identity in seen:
                    raise StaticDatabaseError(f"赏宴挑战目标选项缺失或重复：{stage_id}/{identity}")
                if enum_tail(options[identity].get("OptionType")) != "MechanismBuff":
                    raise StaticDatabaseError(f"赏宴挑战目标选项类型不符：{stage_id}/{identity}")
                seen.add(identity)
                title = text_parts(row.get("MechanismCategoryTitle"))[0] or "挑战目标"
                self.connection.execute("INSERT INTO feast_stage_mechanism_option VALUES (?,?,?,?,?)", (
                    stage_id, ordinal, identity, title, optional_int(row.get("MechanismScoreLimit")),
                ))
