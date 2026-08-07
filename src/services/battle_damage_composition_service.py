# 将聚合技能伤害分类为可解释的角色伤害渠道。
"""Classify aggregate battle skills into explainable per-role damage channels."""

from __future__ import annotations

from collections import defaultdict

from src.domain.battle_report import (
    BattleCharacterSummary,
    BattleDamageComposition,
    BattleSkillSummary,
    DamageCompositionEntry,
    RoleDamageComposition,
)


_REACTION_LABELS = {
    "创生": ("reaction_creation", "创生"),
    "创生花": ("reaction_creation", "创生"),
    "覆纹": ("reaction_hexed", "覆纹"),
    "覆纹追加攻击": ("reaction_hexed", "覆纹"),
    "延滞": ("reaction_remora", "延滞"),
    "黯星": ("reaction_nova", "黯星"),
    "浊燃": ("reaction_scorch", "浊燃"),
    "浸染": ("reaction_stain", "浸染"),
    "盈蓄": ("reaction_charge", "盈蓄"),
    "失谐": ("reaction_discord", "失谐"),
    "环合": ("reaction_unknown", "环合（未细分）"),
    "环合伤害": ("reaction_unknown", "环合（未细分）"),
}

_DIRECT_CATEGORIES = {
    "普攻",
    "E技能",
    "Q技能",
    "闪避反击",
    "格挡反击",
    "Passive Damage",
    "Awakening Damage",
}

_SPECIAL_CATEGORIES = {"Special Damage", "特殊伤害"}

_PUBLIC_OTHER = {
    "倾陷伤害": ("other_topple", "倾陷伤害"),
    "深渊场地Buff": ("other_environment", "环境伤害"),
    "载具伤害": ("other_environment", "环境伤害"),
    "HP同步伤害": ("other_shared", "共享伤害"),
    "未归类": ("other_unattributed", "未归因伤害"),
}

_CHANNEL_ORDER = {
    "direct": 0,
    "special": 1,
    "reaction_creation": 2,
    "reaction_hexed": 3,
    "reaction_remora": 4,
    "reaction_nova": 5,
    "reaction_scorch": 6,
    "reaction_stain": 7,
    "reaction_charge": 8,
    "reaction_discord": 9,
    "reaction_unknown": 10,
    "other": 11,
    "other_topple": 12,
    "other_environment": 13,
    "other_shared": 14,
    "other_unattributed": 15,
}


def _normalized_label(value: str) -> str:
    label = str(value).strip()
    return label.removeprefix("环合·")


def _reaction_channel(skill: BattleSkillSummary) -> tuple[str, str] | None:
    for value in (skill.category, skill.name):
        channel = _REACTION_LABELS.get(_normalized_label(value))
        if channel is not None:
            return channel
    return None


def _public_other_channel(skill: BattleSkillSummary) -> tuple[str, str] | None:
    for value in (skill.category, skill.name):
        channel = _PUBLIC_OTHER.get(_normalized_label(value))
        if channel is not None:
            return channel
    return None


def _role_channel(skill: BattleSkillSummary) -> tuple[str, str]:
    reaction = _reaction_channel(skill)
    if reaction is not None:
        return reaction
    category = _normalized_label(skill.category)
    if category in _SPECIAL_CATEGORIES:
        return "special", "特殊伤害"
    if category in _DIRECT_CATEGORIES:
        return "direct", "直伤"
    return "other", "其他"


def _entries(
    damage_by_channel: dict[str, float],
    labels: dict[str, str],
    *,
    denominator: float,
    include_role_baseline: bool,
) -> tuple[DamageCompositionEntry, ...]:
    if include_role_baseline:
        labels = {
            "direct": "直伤",
            "special": "特殊伤害",
            "other": "其他",
            **labels,
        }
    rows = []
    for key, label in labels.items():
        damage = max(0.0, float(damage_by_channel.get(key, 0.0)))
        if not include_role_baseline and damage <= 0:
            continue
        if include_role_baseline and key not in {"direct", "special", "other"} and damage <= 0:
            continue
        rows.append(
            DamageCompositionEntry(
                key=key,
                label=label,
                damage=damage,
                share_percent=damage / denominator * 100.0 if denominator > 0 else 0.0,
            )
        )
    rows.sort(key=lambda row: (_CHANNEL_ORDER.get(row.key, 999), row.label))
    return tuple(rows)


class BattleDamageCompositionService:
    """Build per-role composition without importing Qt, databases or current account state."""

    @staticmethod
    def calculate(
        *,
        characters: tuple[BattleCharacterSummary, ...],
        skills: tuple[BattleSkillSummary, ...],
        segment_total_damage: float,
    ) -> BattleDamageComposition:
        known_character_ids = {character.character_id for character in characters}
        role_damage: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        role_labels: dict[int, dict[str, str]] = defaultdict(dict)
        public_damage: dict[str, float] = defaultdict(float)
        public_labels: dict[str, str] = {}

        for skill in skills:
            public_channel = _public_other_channel(skill)
            if public_channel is not None or skill.character_id not in known_character_ids:
                key, label = public_channel or ("other_unattributed", "未归因伤害")
                public_damage[key] += skill.damage
                public_labels[key] = label
                continue
            key, label = _role_channel(skill)
            role_damage[skill.character_id][key] += skill.damage
            role_labels[skill.character_id][key] = label

        roles = []
        for character in sorted(characters, key=lambda row: row.damage, reverse=True):
            channel_damage = dict(role_damage.get(character.character_id, {}))
            classified_damage = sum(channel_damage.values())
            reported_damage = max(0.0, character.damage)
            if reported_damage - classified_damage > 0.5:
                channel_damage["other"] = (
                    channel_damage.get("other", 0.0)
                    + reported_damage
                    - classified_damage
                )
            total_damage = max(reported_damage, sum(channel_damage.values()))
            roles.append(
                RoleDamageComposition(
                    character_id=character.character_id,
                    character_name=character.name,
                    total_damage=total_damage,
                    entries=_entries(
                        channel_damage,
                        dict(role_labels.get(character.character_id, {})),
                        denominator=total_damage,
                        include_role_baseline=True,
                    ),
                )
            )

        other_total = sum(public_damage.values())
        accounted_damage = sum(role.total_damage for role in roles) + other_total
        if segment_total_damage - accounted_damage > 0.5:
            public_damage["other_unattributed"] += (
                segment_total_damage - accounted_damage
            )
            public_labels["other_unattributed"] = "未归因伤害"
            other_total = sum(public_damage.values())
        return BattleDamageComposition(
            roles=tuple(roles),
            other_total_damage=other_total,
            other_share_percent=(
                other_total / segment_total_damage * 100.0
                if segment_total_damage > 0
                else 0.0
            ),
            other_entries=_entries(
                dict(public_damage),
                public_labels,
                denominator=segment_total_damage,
                include_role_baseline=False,
            ),
        )
