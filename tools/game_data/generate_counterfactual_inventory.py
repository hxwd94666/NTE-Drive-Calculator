# 从只读发行静态库生成可逐项人工审计的技能与效果文本目录。
"""Generate the counterfactual source inventory without mutating game data."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from catalog_characters import load_datatable, resolve_content_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "reference" / "counterfactual"

_TAG_RE = re.compile(r"<[^>]*>")
_INPUT_LABELS = {
    "_Melee": "A / Z",
    "_Skill": "E",
    "_UltraSkill": "Q",
    "_QTE": "QTE",
}


def _text(value: Any) -> str:
    result = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    result = _TAG_RE.sub("", result)
    result = "<br>".join(part.strip() for part in result.split("\n") if part.strip())
    return result.replace("|", "\\|") or "—"


def _input_label(ability_id: str) -> str:
    if "PerfectEvade" in ability_id:
        return "闪避反击"
    if "Parry" in ability_id or "Block" in ability_id:
        return "格挡反击"
    return next(
        (label for suffix, label in _INPUT_LABELS.items() if ability_id.endswith(suffix)),
        "被动 / 特殊",
    )


def _nonzero_scalings(row: sqlite3.Row) -> tuple[str, ...]:
    result = []
    for label, field in (
        ("攻击", "atk_rate_base_json"),
        ("防御", "def_rate_base_json"),
        ("生命", "hp_rate_base_json"),
    ):
        values = json.loads(row[field] or "[]")
        if any(float(value) != 0.0 for value in values):
            result.append(label)
    return tuple(result)


def _effect_shape(
    connection: sqlite3.Connection,
    effect_definition_id: str,
) -> dict[str, Any]:
    links = connection.execute(
        """
        SELECT target_asset_path, target_available
        FROM combat_effect_buff_link
        WHERE effect_definition_id = ? ORDER BY ordinal
        """,
        (effect_definition_id,),
    ).fetchall()
    targets = tuple(str(row["target_asset_path"]) for row in links)
    modifiers = []
    triggers = []
    for target in targets:
        modifiers.extend(connection.execute(
            """
            SELECT property_id, modifier_operation, magnitude_value,
                   calculation_asset_path
            FROM buff_modifier WHERE asset_path = ? ORDER BY ordinal
            """,
            (target,),
        ).fetchall())
        triggers.extend(connection.execute(
            """
            SELECT event_type, effect_type, target_effect_asset_path
            FROM buff_trigger_effect WHERE asset_path = ? ORDER BY ordinal
            """,
            (target,),
        ).fetchall())
    properties = tuple(sorted({
        str(row["property_id"])
        for row in modifiers
        if str(row["property_id"] or "").strip()
    }))
    calculations = tuple(sorted({
        str(row["calculation_asset_path"]).rsplit("/", 1)[-1]
        for row in modifiers
        if str(row["calculation_asset_path"] or "").strip()
    }))
    trigger_types = tuple(sorted({str(row["event_type"]) for row in triggers}))
    if not links:
        candidate = "固定轴零增量兜底；运行时链接待审计"
    elif calculations:
        candidate = "Calculation 已发现；专用语义待逐项确认"
    elif properties:
        candidate = "通用属性移除反事实候选"
    elif trigger_types:
        candidate = "触发/状态已发现；数值或专用机制待审计"
    else:
        candidate = "运行时对象已绑定；具体作用待审计"
    return {
        "links": len(links),
        "available": all(bool(row["target_available"]) for row in links),
        "properties": properties,
        "calculations": calculations,
        "triggers": trigger_types,
        "candidate": candidate,
    }


def _format_parameters(raw: str) -> str:
    try:
        rows = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return "参数 JSON 待修复"
    values = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if not isinstance(value, (int, float)):
            continue
        rendered = f"{float(value) * 100:g}%" if row.get("is_percent") else f"{value:g}"
        values.append(f"{row.get('name_id') or row.get('ordinal')}={rendered}")
    return _text("；".join(values))


def _asset_path(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    path = value.get("AssetPathName")
    if not isinstance(path, str) or not path:
        return None
    return path.removesuffix("_C").split(".", 1)[0]


def _load_passives(
    connection: sqlite3.Connection,
    content_root: Path,
) -> list[dict[str, Any]]:
    _, ability_rows = load_datatable(
        content_root / "DataTable/Character/DT_CharacterAbilityConfig.json"
    )
    _, effect_rows = load_datatable(
        content_root / "DataTable/Character/DT_CharacterAbilityEffectConfig.json"
    )
    characters = connection.execute(
        """
        SELECT c.character_id, c.name_zh,
               COALESCE(a.logical_character_key,
                        'character:' || CAST(c.character_id AS TEXT)) AS logical_key,
               a.classification
        FROM character AS c
        LEFT JOIN character_annotation AS a USING (character_id)
        ORDER BY c.character_id
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for character in characters:
        if character["classification"] == "combat_transformation":
            continue
        config = ability_rows.get(str(character["character_id"]), {})
        passives = config.get("PassiveAbilityList") if isinstance(config, dict) else None
        for entry in passives if isinstance(passives, list) else ():
            if not isinstance(entry, dict) or not isinstance(entry.get("Value"), dict):
                continue
            ability_id = entry.get("Key")
            if not isinstance(ability_id, str) or not ability_id:
                continue
            identity = (str(character["logical_key"]), ability_id)
            if identity in seen:
                continue
            seen.add(identity)
            value = entry["Value"]
            levels = value.get("LevelsCostItems")
            unlock = levels[0].get("RequireTupoLevel") if isinstance(levels, list) and levels else None
            effect = effect_rows.get(ability_id)
            effect_data = effect.get("GameplayEffectToActivate") if isinstance(effect, dict) else None
            result.append({
                "character_id": int(character["character_id"]),
                "character_name": str(character["name_zh"]),
                "logical_key": str(character["logical_key"]),
                "ability_id": ability_id,
                "ability_index": value.get("AbilityIndex"),
                "unlock_stage": unlock,
                "effect_path": _asset_path(effect_data),
            })
    return result


def _write_character_passives(
    connection: sqlite3.Connection,
    passives: list[dict[str, Any]],
    output: Path,
) -> None:
    lines = [
        "# 角色被动反事实文本盘点",
        "",
        "> 归属与解锁来自官方 `PassiveAbilityList`；名称和说明来自发行静态库。",
        "> 主角 1046/1051 按同一逻辑角色去重，以 1046 为反事实代表。",
        "> 行内“待确认”是自动目录的通用审计提示，不表示当前人工进度；整理状态与正式规则以 `review-notes.md` 为准。",
        "",
        f"当前共 `{len(passives)}` 个逻辑角色被动对象。",
        "",
    ]
    previous_character_id: int | None = None
    for passive in passives:
        ability = connection.execute(
            "SELECT name_zh FROM gameplay_ability_catalog WHERE ability_id = ?",
            (passive["ability_id"],),
        ).fetchone()
        descriptions = connection.execute(
            """
            SELECT description_zh FROM gameplay_ability_description
            WHERE ability_id = ? AND description_type = 'ADT_DES'
            ORDER BY ordinal
            """,
            (passive["ability_id"],),
        ).fetchall()
        effect_path = passive["effect_path"]
        modifiers = connection.execute(
            """
            SELECT property_id, calculation_asset_path FROM buff_modifier
            WHERE asset_path = ? ORDER BY ordinal
            """,
            (effect_path,),
        ).fetchall() if effect_path else ()
        semantics = sorted({
            str(row["property_id"] or row["calculation_asset_path"]).rsplit("/", 1)[-1]
            for row in modifiers if row["property_id"] or row["calculation_asset_path"]
        })
        if passive["character_id"] != previous_character_id:
            lines.extend((
                f"## {passive['character_name']}（{passive['character_id']}；"
                f"{passive['logical_key']}）",
                "",
            ))
            previous_character_id = passive["character_id"]
        lines.extend((
            f"### PASSIVE-{passive['character_id']}-{passive['ability_id']}："
            f"{_text(ability['name_zh'] if ability else passive['ability_id'])}",
            "",
            f"- 解锁：突破 {passive['unlock_stage']} 阶段。",
            f"- 官方说明：{_text(' '.join(str(row['description_zh'] or '') for row in descriptions))}",
            f"- 运行时根 Buff：`{effect_path or '未绑定'}`。",
            f"- 直接属性语义：{_text(' / '.join(semantics) or '无；属于触发/反应/专用逻辑')}。",
            "- 人工审计：待确认触发、对象、持续时间、乘区和伤害消费者。",
            "",
        ))
    output.write_text("\n".join(lines), encoding="utf-8")


def _write_character_skills(
    connection: sqlite3.Connection,
    output: Path,
) -> None:
    rows = connection.execute(
        """
        SELECT c.character_id, c.name_zh AS character_name,
               s.skill_id, s.ability_index, s.gameplay_tag,
               a.name_zh AS skill_name,
               COALESCE(n.logical_character_key,
                        'character:' || CAST(c.character_id AS TEXT))
                   AS logical_character_key,
               n.classification
        FROM character_skill AS s
        JOIN character AS c USING (character_id)
        LEFT JOIN character_annotation AS n USING (character_id)
        LEFT JOIN gameplay_ability_catalog AS a ON a.ability_id = s.skill_id
        ORDER BY c.character_id, s.ability_index, s.skill_id
        """
    ).fetchall()
    by_character: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_character[(int(row["character_id"]), str(row["character_name"]))].append(row)
    supplemental_rows = connection.execute(
        """
        SELECT b.character_id, b.ability_id, b.ability_asset_path,
               COUNT(DISTINCT d.damage_id) AS damage_count
        FROM character_combat_ability_binding AS b
        JOIN combat_ability_effect_binding AS e
          ON e.ability_asset_path = b.ability_asset_path
        JOIN skill_damage AS d ON d.damage_id = e.effect_id
        LEFT JOIN character_skill AS s
          ON s.character_id = b.character_id AND s.skill_id = b.ability_id
        WHERE s.skill_id IS NULL
        GROUP BY b.character_id, b.ability_id, b.ability_asset_path
        ORDER BY b.character_id, b.binding_kind, b.ordinal, b.ability_id
        """
    ).fetchall()
    supplemental_by_character: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in supplemental_rows:
        supplemental_by_character[int(row["character_id"])].append(row)
    logical_keys = {str(row["logical_character_key"]) for row in rows}
    lines = [
        "# 角色技能反事实文本盘点",
        "",
        "> 本文件由 `tools/game_data/generate_counterfactual_inventory.py` 从只读发行静态库生成。",
        "> “自动预判”和行内“待确认”不是当前人工进度；整理状态与正式语义以 `review-notes.md` 为准。",
        "",
        f"当前共 `{len(by_character)}` 条含技能的静态角色记录、"
        f"`{len(logical_keys)}` 个逻辑角色、`{len(rows)}` 个原始技能目录项，另有 "
        f"`{len(supplemental_rows)}` 个不在角色技能目录中的绑定直伤 Ability。",
        "同一逻辑角色的静态形态不会被误计为多个角色；具体归并和公式兜底以人工审计结论为准。",
        "",
    ]
    missing_characters = connection.execute(
        """
        SELECT c.character_id, c.name_zh, n.logical_character_key,
               n.canonical_character_id, n.classification
        FROM character AS c
        LEFT JOIN character_annotation AS n USING (character_id)
        WHERE c.character_id NOT IN (SELECT character_id FROM character_skill)
        ORDER BY c.character_id
        """
    ).fetchall()
    if missing_characters:
        transformations = [
            row for row in missing_characters
            if row["classification"] == "combat_transformation"
        ]
        unresolved = [row for row in missing_characters if row not in transformations]
        if transformations:
            transformation_text = "、".join(
                f"{row['name_zh']}（{row['character_id']} → "
                f"{row['canonical_character_id']}）"
                for row in transformations
            )
            lines.extend((
                f"另有 `{len(transformations)}` 条已确认战斗形态没有角色技能目录："
                f"{transformation_text}。",
                "它们保留身份与原始 Ability 绑定，但不作为独立角色进入本期正式伤害审计。",
                "",
            ))
        if unresolved:
            unresolved_text = "、".join(
                f"{row['name_zh']}（{row['character_id']}）" for row in unresolved
            )
            lines.extend((
                f"仍有 `{len(unresolved)}` 条静态记录缺少技能目录且尚未完成身份分类："
                f"{unresolved_text}。",
                "",
            ))
    for (character_id, character_name), skills in by_character.items():
        logical_character_key = str(skills[0]["logical_character_key"])
        lines.extend((
            f"## {character_name}（{character_id}；{logical_character_key}）",
            "",
            "| 审计 ID | 输入 | 技能 | 正式 Ability | 伤害项 | 倍率来源 | 运行时效果 | 自动预判 | 人工审计 |",
            "| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |",
        ))
        for skill in skills:
            ability_id = str(skill["skill_id"])
            damage_rows = connection.execute(
                """
                SELECT atk_rate_base_json, def_rate_base_json, hp_rate_base_json
                FROM skill_damage WHERE ability_id = ? ORDER BY damage_id
                """,
                (ability_id,),
            ).fetchall()
            scaling_sets = {_nonzero_scalings(row) for row in damage_rows}
            scaling_labels = sorted({value for values in scaling_sets for value in values})
            ability_paths = tuple(
                str(row["ability_asset_path"])
                for row in connection.execute(
                    """
                    SELECT ability_asset_path FROM character_combat_ability_binding
                    WHERE character_id = ? AND ability_id = ?
                    """,
                    (character_id, ability_id),
                )
            )
            effect_count = sum(
                connection.execute(
                    """
                    SELECT count(*) FROM combat_ability_effect_binding
                    WHERE ability_asset_path = ?
                    """,
                    (path,),
                ).fetchone()[0]
                for path in ability_paths
            )
            if damage_rows and all(len(values) == 1 for values in scaling_sets):
                candidate = "固定轴通用逐击候选"
            elif damage_rows:
                candidate = "倍率来源混合；专用逐击待审计"
            elif effect_count:
                candidate = "无直接伤害表；Buff/状态技能待审计"
            else:
                candidate = "固定轴零增量兜底；分支与引用待审计"
            audit_id = f"SKILL-{character_id}-{ability_id}"
            lines.append(
                "| "
                + " | ".join((
                    audit_id,
                    _input_label(ability_id),
                    _text(skill["skill_name"] or ability_id),
                    ability_id,
                    str(len(damage_rows)),
                    _text(" / ".join(scaling_labels) or "无直接倍率"),
                    str(effect_count),
                    candidate,
                    "待确认全部分支、状态和消费者",
                ))
                + " |"
            )
        for binding in supplemental_by_character.get(character_id, ()):
            ability_id = str(binding["ability_id"])
            damage_rows = connection.execute(
                """
                SELECT DISTINCT d.atk_rate_base_json, d.def_rate_base_json,
                                d.hp_rate_base_json
                FROM combat_ability_effect_binding AS e
                JOIN skill_damage AS d ON d.damage_id = e.effect_id
                WHERE e.ability_asset_path = ?
                ORDER BY d.damage_id
                """,
                (str(binding["ability_asset_path"]),),
            ).fetchall()
            scaling_labels = sorted({
                value
                for row in damage_rows
                for value in _nonzero_scalings(row)
            })
            effect_count = int(connection.execute(
                """
                SELECT count(*) FROM combat_ability_effect_binding
                WHERE ability_asset_path = ?
                """,
                (str(binding["ability_asset_path"]),),
            ).fetchone()[0])
            lines.append(
                "| "
                + " | ".join((
                    f"BOUND-{character_id}-{ability_id}",
                    _input_label(ability_id),
                    ability_id,
                    ability_id,
                    str(binding["damage_count"]),
                    _text(" / ".join(scaling_labels) or "无直接倍率"),
                    str(effect_count),
                    "绑定直伤 Ability；纳入逐击反事实",
                    "待确认输入分支、控制效果和资源值",
                ))
                + " |"
            )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def _write_awakenings(connection: sqlite3.Connection, output: Path) -> None:
    rows = connection.execute(
        """
        SELECT c.character_id, c.name_zh AS character_name,
               a.effect_id, a.ordinal, a.title_zh, a.description_zh,
               e.effect_definition_id
        FROM character_awaken_effect AS a
        JOIN character AS c USING (character_id)
        JOIN combat_effect_definition AS e
          ON e.owner_kind = 'character_awaken'
         AND e.owner_id = CAST(a.character_id AS TEXT) || ':' || a.effect_id
        ORDER BY c.character_id, a.ordinal, a.effect_id
        """
    ).fetchall()
    by_character: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_character[(int(row["character_id"]), str(row["character_name"]))].append(row)
    lines = [
        "# 角色觉醒反事实文本盘点",
        "",
        "> 自动生成静态事实；“自动预判”和行内“待确认”不是当前人工进度，整理状态与正式规则以 `review-notes.md` 为准。",
        "",
        f"当前共 `{len(rows)}` 条觉醒/共鸣效果。",
        "",
    ]
    for (character_id, character_name), effects in by_character.items():
        lines.extend((
            f"## {character_name}（{character_id}）",
            "",
            "| 审计 ID | 序号 | 名称 | 官方说明 | 运行时链接 | 属性 / Calculation | 自动预判 | 人工审计 |",
            "| --- | ---: | --- | --- | ---: | --- | --- | --- |",
        ))
        for row in effects:
            shape = _effect_shape(connection, str(row["effect_definition_id"]))
            semantics = " / ".join((*shape["properties"], *shape["calculations"])) or "—"
            skill_level_bonus = connection.execute(
                """
                SELECT count(*) FROM character_awaken_skill_level_bonus
                WHERE character_id = ? AND effect_id = ?
                """,
                (character_id, str(row["effect_id"])),
            ).fetchone()[0]
            candidate = (
                "技能等级变化已结构化；其他附带效果仍待审计"
                if skill_level_bonus
                else shape["candidate"]
            )
            lines.append(
                "| "
                + " | ".join((
                    f"AWAKEN-{character_id}-{row['effect_id']}",
                    str(row["ordinal"]),
                    _text(row["title_zh"] or row["effect_id"]),
                    _text(row["description_zh"]),
                    str(shape["links"]),
                    _text(semantics),
                    candidate,
                    "待确认触发、对象、乘区和轴变化",
                ))
                + " |"
            )
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def _write_forks(connection: sqlite3.Connection, output: Path) -> None:
    rows = connection.execute(
        """
        SELECT e.effect_definition_id, e.owner_id, e.description_zh,
               e.parameters_json, f.name_zh AS fork_name
        FROM combat_effect_definition AS e
        LEFT JOIN fork_item AS f ON f.star_pack_id = e.owner_id
        WHERE e.owner_kind = 'fork_star'
        ORDER BY e.owner_id, e.effect_definition_id, f.fork_id
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["owner_id"])].append(row)
    lines = [
        "# 弧盘精炼反事实文本盘点",
        "",
        "> 相同弧盘的五个精炼等级合并为一个审计单元；具体数值仍逐级列出。",
        "",
        f"当前共 `{len(grouped)}` 组精炼效果、`{len(rows)}` 个精炼等级。",
        "",
        "| 审计 ID | 弧盘 | 官方说明 | 精炼参数 | 运行时语义 | 自动预判 | 人工审计 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for star_pack_id, levels in grouped.items():
        unique_levels = {}
        fork_names = []
        for row in levels:
            unique_levels[str(row["effect_definition_id"])] = row
            if row["fork_name"] and str(row["fork_name"]) not in fork_names:
                fork_names.append(str(row["fork_name"]))
        ordered = [unique_levels[key] for key in sorted(unique_levels)]
        parameter_text = "<br>".join(
            f"精{index}: {_format_parameters(row['parameters_json'])}"
            for index, row in enumerate(ordered, start=1)
        )
        shapes = [
            _effect_shape(connection, str(row["effect_definition_id"]))
            for row in ordered
        ]
        semantics = sorted({
            value
            for shape in shapes
            for value in (*shape["properties"], *shape["calculations"])
        })
        candidate = "；".join(dict.fromkeys(shape["candidate"] for shape in shapes))
        lines.append(
            "| "
            + " | ".join((
                f"FORK-{star_pack_id}",
                _text(" / ".join(fork_names) or star_pack_id),
                _text(ordered[0]["description_zh"] if ordered else ""),
                parameter_text,
                _text(" / ".join(semantics) or "—"),
                candidate,
                "待确认触发、叠层、消费者和精炼联动",
            ))
            + " |"
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_suits(connection: sqlite3.Connection, output: Path) -> None:
    from src.services.battle_equipment_suit_service import (
        BattleEquipmentSuitService,
    )

    rows = connection.execute(
        """
        SELECT s.suit_id, s.name_zh AS suit_name, e.required_count,
               e.modify_pack_id, e.description_zh, d.effect_definition_id
        FROM equipment_suit_effect AS e
        JOIN equipment_suit AS s USING (suit_id)
        JOIN combat_effect_definition AS d
          ON d.owner_kind = 'equipment_suit'
         AND d.owner_id = e.suit_id
         AND d.effect_definition_id =
             'equipment_suit:' || e.suit_id || ':' || CAST(e.required_count AS TEXT)
        ORDER BY s.suit_id, e.required_count
        """
    ).fetchall()
    policies = {
        (row.suit_id, row.required_count): row.fixed_axis_policy
        for row in BattleEquipmentSuitService.catalog()
    }
    lines = [
        "# 空幕套装反事实文本盘点",
        "",
        "> 本文件由静态套装目录和 `BattleEquipmentSuitService` 的稳定审计目录共同生成。",
        "",
        f"当前共 `{len(rows)}` 条套装效果。",
        "",
        "| 审计 ID | 空幕套装 | 件数 | 官方说明 | 当前固定轴重放 |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        key = (str(row["suit_id"]), int(row["required_count"]))
        lines.append(
            "| "
            + " | ".join((
                f"SUIT-{row['suit_id']}-{row['required_count']}",
                _text(row["suit_name"]),
                str(row["required_count"]),
                _text(row["description_zh"]),
                _text(policies.get(key, "未进入稳定空幕目录")),
            ))
            + " |"
        )
    lines.extend((
        "",
        "## 证据与估计边界",
        "",
        "- 数值、持续时间和上限来自正式说明、套装曲线与规范化 Buff；触发时点仍是可删除重算的推断。",
        "- 黯星与延滞可由结算击按已知持续时间向前回填；浸染从已识别反应事件向后投影 12 秒。",
        "- 目标状态目前没有敌方实例键，多目标只作为低置信估计；普通扣血没有受击者证据时不反猜层数。",
        "- 分属性受击套装按正式伤害事件计层，不根据伤害量反猜被聚合的目标数量。",
    ))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_system_mechanics(output: Path) -> None:
    rows = (
        ("SYSTEM-direct", "直伤与追加攻击", "结构化重放", "通用攻击/生命/防御倍率、暴击期望和目标乘区", "继续逐角色核对特殊倍率"),
        ("SYSTEM-unbalance-meter", "敌方倾陷条与逐击倾陷值", "静态值已结构化", "每个敌方目标独立持有倾陷条；每个逐击按自己的 UnbalValue 削减，所谓额外削减只是该击数值更高", "补齐按目标的倾陷条时序重放"),
        ("SYSTEM-heterochrome-meter", "角色环合条、切换与 QTE", "静态值已结构化", "每个场上角色独立持有环合条；每击按 HeterochromeAdd 增长，满后切换相邻角色触发 QTE 与对应环合", "补齐场上角色、相邻关系和切换状态机"),
        ("SYSTEM-nightmare", "噩梦", "结构化估计", "层数、独立到期、E/Q施加与五觉生命结算", "核对所有技能施加点和提前结算"),
        ("SYSTEM-zankou-dot", "蚀心 / 鸩火", "结构化估计", "逐击层数、形态与持续伤害消费者", "核对所有角色/技能触发和形态边界"),
        ("SYSTEM-scorch", "浊燃", "结构化重放", "等级基础值、30%倍率、50%固定暴击率、环合强度和目标乘区", "扩充不同队伍与目标样本"),
        ("SYSTEM-creation", "创生", "结构化重放", "等级基础值、环合强度和目标乘区", "核对复制创生花等派生来源"),
        ("SYSTEM-dark-star", "黯星", "结构化重放", "等级基础值、环合强度、不暴击和目标乘区", "核对法帝娅层数与多目标归属"),
        ("SYSTEM-topple", "倾陷", "结构化估计", "逐角色格子求和、敌方UnbalMax档位、穿透和抗性", "扩充敌人档位和逐角色真实样本"),
        ("SYSTEM-max-hp", "最大生命减少", "结构化估计", "单目标观测结算、安魂曲五觉与法帝娅归因", "补多目标实例证据"),
        ("SYSTEM-target", "防御 / 抗性 / 易伤", "结构化重放", "用户确认或共享目标属性包", "补齐目标自动识别和敌方动态Debuff"),
        ("SYSTEM-multi-target", "多目标逐击", "固定轴估计", "正式目标ID存在时分别统计；缺ID不串联", "等待抓包或运行时目标证据"),
        ("SYSTEM-axis-change", "新增/删除逐击与循环变化", "固定轴零增量兜底", "原轴100%保留，不凭空生成动作", "需要动作生成与能量/冷却状态机"),
        ("SYSTEM-replacement", "技能替换 / 攻击模组", "已识别待建模", "保留已观测逐击并按当前GE识别", "安魂曲偷取技能、G模组和敌神者等需角色状态机"),
    )
    lines = [
        "# 公共与特殊机制反事实文本盘点",
        "",
        "> 本表记录当前代码已经存在的重放边界；不是游戏静态资产全集。",
        "",
        "| 审计 ID | 机制 | 当前层级 | 已有边界 | 下一审计重点 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        "| " + " | ".join(_text(value) for value in row) + " |"
        for row in rows
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_index(
    connection: sqlite3.Connection,
    passive_count: int,
    output: Path,
) -> None:
    counts = {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in (
            "character",
            "character_skill",
            "character_awaken_effect",
            "combat_effect_definition",
            "buff_definition",
            "buff_modifier",
            "buff_trigger_effect",
            "combat_effect_buff_link",
        )
    }
    output.write_text(
        f"""# 战斗反事实文本盘点

本目录先盘点游戏静态事实与当前实现边界，再据此设计数据库和设置页展示；现在不把人工推测写回静态库。

## 当前基线

- 角色：{counts['character']}
- 角色技能目录：{counts['character_skill']}
- 角色被动（逻辑角色去重）：{passive_count}
- 觉醒/共鸣效果：{counts['character_awaken_effect']}
- 可配置战斗效果：{counts['combat_effect_definition']}
- 规范化 Buff / GE：{counts['buff_definition']}
- Buff 属性修改：{counts['buff_modifier']}
- Buff 触发关系：{counts['buff_trigger_effect']}
- 来源到 Buff / GE 绑定：{counts['combat_effect_buff_link']}

## 审计文件

| 文件 | 审计范围 |
| --- | --- |
| [角色技能](character-skills.md) | A/Z/E/Q/QTE 与技能伤害项 |
| [角色被动](character-passives.md) | 突破被动、触发、状态与专用反应逻辑 |
| [伤害类型](damage-types.md) | 官方字段、持续伤害种类与人工确认边界 |
| [角色觉醒](awakenings.md) | 一至六觉及三/六觉共鸣效果 |
| [弧盘精炼](forks.md) | 每种弧盘五档精炼参数与运行时效果 |
| [空幕套装](equipment-suits.md) | 二件套和四件套效果 |
| [公共与特殊机制](system-mechanics.md) | DOT、环合、倾陷、最大生命、多目标和轴变化 |
| [人工审计结论](review-notes.md) | 用户确认的触发、乘区、消费者和反事实规则 |

## 状态口径

- `结构化重放`：公式输入和消费者已结构化，仍可另外标注运行时状态置信度。
- `结构化估计`：数值公式已建模，但触发、层数、目标或状态来自推算。
- `固定轴估计`：保留真实逐击，用同技能、同类型、面板比值或零增量兜底。
- `已识别待建模`：身份明确，但尚无安全的反事实策略。
- `当前不影响固定轴`：只改变治疗、护盾、移动、资源或未来动作，不能误写为精确零收益。

## 人工审计方式

用户按稳定审计 ID 提供结论，例如 `AWAKEN-1003-Effect2`。每次确认至少记录：触发、状态时序、作用对象、
数值乘区、伤害消费者、是否改变逐击/循环，以及证据来源。人工结论写入 `review-notes.md`，自动重新生成目录
不会覆盖它。
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    database = args.database.resolve()
    output = args.output_dir.resolve()
    content_root = resolve_content_root(args.source)
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        passives = _load_passives(connection, content_root)
        _write_index(connection, len(passives), output / "README.md")
        _write_character_skills(connection, output / "character-skills.md")
        _write_character_passives(connection, passives, output / "character-passives.md")
        _write_awakenings(connection, output / "awakenings.md")
        _write_forks(connection, output / "forks.md")
        _write_suits(connection, output / "equipment-suits.md")
        _write_system_mechanics(output / "system-mechanics.md")
    finally:
        connection.close()
    review_notes = output / "review-notes.md"
    if not review_notes.exists():
        review_notes.write_text(
            "# 反事实人工审计结论\n\n"
            "本文件只记录已经由用户或强证据确认的语义；待确认问题保留在各自动目录中。\n\n"
            "## 已确认\n\n"
            "当前尚未从新目录开始逐项确认。\n",
            encoding="utf-8",
        )
    print(f"Generated counterfactual inventory: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
