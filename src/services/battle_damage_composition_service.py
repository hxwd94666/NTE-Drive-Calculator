# 将聚合技能或正式逐击分类为可解释的角色伤害渠道。
"""Classify aggregate skills or formal hits into per-role damage channels."""

from __future__ import annotations

from collections import defaultdict

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterSummary,
    BattleDamageComposition,
    BattleHitReplayResult,
    BattleMaxHpReductionEvent,
    BattleRangeRoleSummary,
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

_NAMED_SPECIAL_LABELS = {
    "噩梦": ("special_nightmare", "噩梦"),
    "「噩梦」": ("special_nightmare", "噩梦"),
    "蚀心": ("special_zankou_erosion", "蚀心"),
    "鸩火": ("special_zankou_venom", "鸩火"),
}

_SPECIAL_EFFECT_LABELS = {
    "ge_player_kuhara_seedreaction_damage": ("direct_follow_up", "追加攻击"),
    "ge_player_daffodill_extraunbalance_damage": (
        "special_daffodill_extra_topple",
        "达芙蒂尔五觉·额外倾陷伤害",
    ),
    "ge_player_lacrimosa_blood_damage": ("special_nightmare", "噩梦"),
    "ge_player_lacrimosa_blood_damage_lv6": ("special_nightmare", "噩梦"),
    "ge_player_lacrimosa_anhunzhoutwo_damage": (
        "special_lacrimosa_dissonance",
        "失谐强化伤害",
    ),
    "ge_player_fadia_zhouyin_damage": (
        "special_fadia_shared_damage",
        "破灭体验共享伤害",
    ),
    "ge_player_zankou_dotdamage": ("special_zankou_erosion", "蚀心"),
    "ge_player_zankou_dotultradamage": ("special_zankou_venom", "鸩火"),
}

_DOT_EFFECTS = frozenset({
    "ge_player_lacrimosa_blood_damage",
    "ge_player_lacrimosa_blood_damage_lv6",
    "ge_player_zankou_dotdamage",
    "ge_player_zankou_dotultradamage",
    "ge_player_cang_ultraskill_damage",
    "ge_player_adler_skill_damage",
})

_ATTACHMENT_EFFECTS = frozenset({
    "ge_player_kuhara_seed_damage",
    "ge_player_kuhara_budboom_damage",
    "ge_player_kuhara_budend_damage",
})

_PUBLIC_OTHER = {
    "倾陷伤害": ("other_topple", "倾陷伤害"),
    "敌方飞弹反射伤害": ("other_reflected_projectile", "敌方飞弹反射伤害"),
    "深渊场地Buff": ("other_environment", "环境伤害"),
    "载具伤害": ("other_environment", "环境伤害"),
    "HP同步伤害": ("other_shared", "共享伤害"),
    "未归类": ("other_unattributed", "未归因伤害"),
}

_SYSTEM_COMPOSITION_CHANNELS = {
    "other_reflected_projectile",
    "other_environment",
    "other_shared",
}
_CHARACTER_ATTRIBUTABLE_SYSTEM_CHANNELS = {
    "other_reflected_projectile",
}

_DOT_CHANNELS = {
    "dot",
    "special_nightmare",
    "special_zankou_erosion",
    "special_zankou_venom",
}

_REACTION_CHANNELS = {
    key for key, _label in _REACTION_LABELS.values()
}

_COARSE_REACTION_LABELS = {
    key: f"环合·{label}" if key != "reaction_unknown" else label
    for key, label in _REACTION_LABELS.values()
}

_CHANNEL_ORDER = {
    "direct": 0,
    "direct_follow_up": 1,
    "dot": 2,
    "attachment": 3,
    "topple": 4,
    "max_hp_reduction": 5,
    "shared_damage": 6,
    "reaction_creation": 7,
    "reaction_hexed": 8,
    "reaction_remora": 9,
    "reaction_nova": 10,
    "reaction_scorch": 11,
    "reaction_stain": 12,
    "reaction_charge": 13,
    "reaction_discord": 14,
    "reaction_unknown": 15,
    "special": 16,
    "other_reflected_projectile": 17,
    "other_environment": 18,
    "other_shared": 19,
    "unattributed_topple": 20,
    "unattributed_missing_source": 21,
    "other_unattributed": 22,
}

_MISSING_SOURCE_LABELS = {
    "",
    "未识别技能",
    "未知技能",
    "未识别伤害",
    "未知伤害",
    "未归因伤害",
    "来源字段缺失",
    "unknown",
    "unknown skill",
    "unknown damage",
    "none",
}


def _normalized_label(value: str) -> str:
    label = str(value).strip()
    return label.removeprefix("环合·")


def _has_hit_source_evidence(hit: BattleAnalysisHit) -> bool:
    return any(
        str(value or "").strip().casefold() not in _MISSING_SOURCE_LABELS
        for value in (
            hit.ability_id,
            hit.gameplay_effect_id,
            hit.damage_component,
            hit.attack_type,
            hit.skill_name,
            hit.damage_name,
        )
    )


def _is_explicit_reaction_effect(gameplay_effect_name: str | None) -> bool:
    effect = str(gameplay_effect_name or "").casefold()
    return "reaction" in effect and "qte" not in effect


def _is_qte_direct_damage(
    ability_name: str | None,
    gameplay_effect_name: str | None,
    attack_type: str | None = None,
) -> bool:
    ability = str(ability_name or "").casefold()
    effect = str(gameplay_effect_name or "").casefold()
    attack = str(attack_type or "").strip()
    if _is_explicit_reaction_effect(gameplay_effect_name):
        return False
    return "qte" in ability or "qte" in effect or attack.startswith("环合·")


def _reaction_channel(skill: BattleSkillSummary) -> tuple[str, str] | None:
    for value in (skill.category, skill.name):
        channel = _REACTION_LABELS.get(_normalized_label(value))
        if channel is not None:
            return channel
    return None


def _named_special_channel(skill: BattleSkillSummary) -> tuple[str, str] | None:
    for value in (skill.category, skill.name):
        channel = _NAMED_SPECIAL_LABELS.get(_normalized_label(value))
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
    if _is_qte_direct_damage(
        skill.ability_name,
        skill.gameplay_effect_name,
        skill.category,
    ):
        return "direct", "直伤"
    reaction = _reaction_channel(skill)
    if reaction is not None:
        return reaction
    named_special = _named_special_channel(skill)
    if named_special is not None:
        return named_special
    category = _normalized_label(skill.category)
    if category in _SPECIAL_CATEGORIES:
        return "special", "特殊伤害"
    if category in _DIRECT_CATEGORIES:
        return "direct", "直伤"
    return "other", "其他"


def classify_battle_hit_channel(hit: BattleAnalysisHit) -> tuple[str, str]:
    """Return the stable timeline lane for one normalized hit."""

    if hit.direction != "outgoing":
        return "incoming", "承伤"
    effect = hit.gameplay_effect_id.casefold()
    if effect in _ATTACHMENT_EFFECTS:
        return "attachment", "附着物"
    effect_channel = _SPECIAL_EFFECT_LABELS.get(effect)
    if effect_channel is not None:
        return effect_channel
    if effect in _DOT_EFFECTS:
        return "dot", "持续伤害"
    if _is_qte_direct_damage(
        hit.ability_id,
        hit.gameplay_effect_id,
        hit.attack_type,
    ):
        return "direct", "直伤"
    values = tuple(
        _normalized_label(value)
        for value in (hit.damage_name, hit.skill_name, hit.attack_type)
    )
    for value in values:
        public_other = _PUBLIC_OTHER.get(value)
        if public_other is not None:
            return public_other
        named_special = _NAMED_SPECIAL_LABELS.get(value)
        if named_special is not None:
            return named_special
        reaction = _REACTION_LABELS.get(value)
        if reaction is not None:
            return reaction
    if hit.classification == "weave":
        return "reaction_hexed", "覆纹"
    if hit.classification == "topple":
        return "other_topple", "倾陷伤害"
    if hit.classification == "reaction":
        return "reaction_unknown", "环合 / 特殊伤害"
    if hit.classification == "direct_follow_up":
        return "direct_follow_up", "追加攻击"
    if hit.classification == "direct":
        return "direct", "直伤"
    return "other", "其他"


def classify_battle_hit_reaction_trigger(
    hit: BattleAnalysisHit,
) -> tuple[str, str] | None:
    """Return a reaction triggered by this hit without changing its damage lane.

    A QTE is direct damage, but an ``环合·浊燃`` QTE still proves that its
    source character participated in triggering scorch for equipment effects.
    """

    for value in (hit.damage_name, hit.skill_name, hit.attack_type):
        reaction = _REACTION_LABELS.get(_normalized_label(value))
        if reaction is not None:
            return reaction
    if hit.classification == "reaction":
        return "reaction_unknown", "环合 / 特殊伤害"
    return None


def _coarse_role_channel(key: str, label: str) -> tuple[str, str]:
    if key in _CHARACTER_ATTRIBUTABLE_SYSTEM_CHANNELS:
        return key, label
    if key in _REACTION_CHANNELS:
        return key, _COARSE_REACTION_LABELS.get(key, label)
    if key in _DOT_CHANNELS:
        return "dot", "持续伤害"
    if key == "attachment":
        return "attachment", "附着物"
    if key in {"other_topple", "special_daffodill_extra_topple"}:
        return "topple", "倾陷"
    if key in {"direct", "direct_follow_up", "special_lacrimosa_dissonance"}:
        return "direct", "直伤"
    if key == "special_fadia_shared_damage":
        return "shared_damage", "共享伤害"
    if key == "max_hp_reduction":
        return "max_hp_reduction", "生命上限结算"
    return "special", "特殊机制伤害"


def _fine_hit_channel(
    hit: BattleAnalysisHit,
    key: str,
    label: str,
) -> tuple[str, str]:
    if key in _CHARACTER_ATTRIBUTABLE_SYSTEM_CHANNELS:
        return key, label
    coarse_key, _coarse_label = _coarse_role_channel(key, label)
    if key in _REACTION_CHANNELS or key == "other_topple":
        return coarse_key, _coarse_label
    identity = (
        hit.gameplay_effect_id.strip()
        or "|".join((
            hit.ability_id.strip(),
            hit.damage_name.strip(),
            hit.damage_component.strip(),
            "follow_up" if hit.is_follow_up else "primary",
        ))
    ).casefold()
    skill_name = hit.skill_name.strip()
    damage_name = hit.damage_name.strip()
    if skill_name and damage_name and skill_name != damage_name:
        display = f"{skill_name} · {damage_name}"
    else:
        display = damage_name or skill_name or label
    return f"{coarse_key}|{identity}", display


def _entry_order(key: str) -> int:
    return _CHANNEL_ORDER.get(key.split("|", 1)[0], 999)


def _topple_role_contributions(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> tuple[tuple[int, str, float], ...]:
    if replay is None:
        return ()
    values: dict[int, float] = defaultdict(float)
    labels: dict[int, str] = {}
    for factor in replay.factors:
        prefix = "topple_character:"
        if not factor.factor_id.startswith(prefix) or factor.value <= 0:
            continue
        try:
            character_id = int(factor.factor_id.removeprefix(prefix))
        except ValueError:
            continue
        values[character_id] += float(factor.value)
        labels[character_id] = factor.label.removesuffix("倾陷贡献")
    predicted_total = sum(values.values())
    if predicted_total <= 0:
        return ()
    return tuple(
        (
            character_id,
            labels.get(character_id, f"角色 {character_id}"),
            hit.damage * contribution / predicted_total,
        )
        for character_id, contribution in values.items()
    )


def _entries(
    damage_by_channel: dict[str, float],
    labels: dict[str, str],
    *,
    denominator: float,
    include_role_baseline: bool,
) -> tuple[DamageCompositionEntry, ...]:
    del include_role_baseline
    rows = []
    for key, label in labels.items():
        damage = max(0.0, float(damage_by_channel.get(key, 0.0)))
        if damage <= 0:
            continue
        rows.append(
            DamageCompositionEntry(
                key=key,
                label=label,
                damage=damage,
                share_percent=damage / denominator * 100.0 if denominator > 0 else 0.0,
            )
        )
    rows.sort(key=lambda row: (_entry_order(row.key), row.label))
    return tuple(rows)


def _finalize_composition(
    *,
    role_rows: tuple[tuple[int, str, float], ...],
    role_damage: dict[int, dict[str, float]],
    role_labels: dict[int, dict[str, str]],
    public_damage: dict[str, float],
    public_labels: dict[str, str],
    segment_total_damage: float,
    system_damage: dict[str, float] | None = None,
    system_labels: dict[str, str] | None = None,
    pending_topple_attribution: bool = False,
    unresolved_topple_attribution: bool = False,
) -> BattleDamageComposition:
    system_damage = dict(system_damage or {})
    system_labels = dict(system_labels or {})
    roles = []
    for character_id, character_name, reported_value in sorted(
        role_rows,
        key=lambda row: row[2],
        reverse=True,
    ):
        channel_damage = dict(role_damage.get(character_id, {}))
        classified_damage = sum(channel_damage.values())
        reported_damage = max(0.0, reported_value)
        if reported_damage - classified_damage > 0.5:
            channel_damage["other"] = (
                channel_damage.get("other", 0.0)
                + reported_damage
                - classified_damage
            )
        total_damage = max(reported_damage, sum(channel_damage.values()))
        entries = _entries(
            channel_damage,
            dict(role_labels.get(character_id, {})),
            denominator=total_damage,
            include_role_baseline=True,
        )
        if total_damage <= 0.0 or not entries:
            continue
        roles.append(RoleDamageComposition(
            character_id=character_id,
            character_name=character_name,
            total_damage=total_damage,
            entries=entries,
        ))

    other_total = sum(public_damage.values())
    system_total = sum(system_damage.values())
    accounted_damage = (
        sum(role.total_damage for role in roles) + system_total + other_total
    )
    if segment_total_damage - accounted_damage > 0.5:
        public_damage["other_unattributed"] = (
            public_damage.get("other_unattributed", 0.0)
            + segment_total_damage
            - accounted_damage
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
        system_total_damage=system_total,
        system_share_percent=(
            system_total / segment_total_damage * 100.0
            if segment_total_damage > 0
            else 0.0
        ),
        system_entries=_entries(
            system_damage,
            system_labels,
            denominator=segment_total_damage,
            include_role_baseline=False,
        ),
        pending_topple_attribution=pending_topple_attribution,
        unresolved_topple_attribution=unresolved_topple_attribution,
    )


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
        system_damage: dict[str, float] = defaultdict(float)
        system_labels: dict[str, str] = {}

        for skill in skills:
            public_channel = _public_other_channel(skill)
            has_character_owner = skill.character_id in known_character_ids
            character_attributable = (
                public_channel is not None
                and public_channel[0] in _CHARACTER_ATTRIBUTABLE_SYSTEM_CHANNELS
                and has_character_owner
            )
            if (
                public_channel is not None
                and public_channel[0] in _SYSTEM_COMPOSITION_CHANNELS
                and not character_attributable
            ):
                key, label = public_channel
                system_damage[key] += skill.damage
                system_labels[key] = label
                continue
            if character_attributable and public_channel is not None:
                key, label = public_channel
                role_damage[skill.character_id][key] += skill.damage
                role_labels[skill.character_id][key] = label
                continue
            if public_channel is not None or skill.character_id not in known_character_ids:
                key, label = ("other_unattributed", "未归因伤害")
                public_damage[key] += skill.damage
                public_labels[key] = label
                continue
            key, label = _role_channel(skill)
            if key == "other":
                public_damage["other_unattributed"] += skill.damage
                public_labels["other_unattributed"] = "未归因伤害"
                continue
            role_damage[skill.character_id][key] += skill.damage
            role_labels[skill.character_id][key] = label

        return _finalize_composition(
            role_rows=tuple(
                (character.character_id, character.name, character.damage)
                for character in characters
            ),
            role_damage={key: dict(value) for key, value in role_damage.items()},
            role_labels={key: dict(value) for key, value in role_labels.items()},
            public_damage=dict(public_damage),
            public_labels=public_labels,
            system_damage=dict(system_damage),
            system_labels=system_labels,
            segment_total_damage=segment_total_damage,
        )

    @staticmethod
    def calculate_from_hits(
        *,
        roles: tuple[BattleRangeRoleSummary, ...],
        hits: tuple[BattleAnalysisHit, ...],
        segment_total_damage: float,
        max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = (),
        hit_replays: tuple[BattleHitReplayResult, ...] = (),
        role_identities: tuple[tuple[int, str], ...] = (),
        grouping: str = "coarse",
    ) -> BattleDamageComposition:
        """Build selected-range composition from immutable formal hit evidence."""

        if grouping not in {"coarse", "fine"}:
            raise ValueError(f"unsupported damage composition grouping: {grouping}")
        role_names = {
            role.character_id: role.character_name for role in roles
        }
        role_names.update({int(key): str(value) for key, value in role_identities})
        replay_by_event = {row.event_id: row for row in hit_replays}
        role_damage: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        role_labels: dict[int, dict[str, str]] = defaultdict(dict)
        public_damage: dict[str, float] = defaultdict(float)
        public_labels: dict[str, str] = {}
        system_damage: dict[str, float] = defaultdict(float)
        system_labels: dict[str, str] = {}
        pending_topple_attribution = False
        unresolved_topple_attribution = False
        for hit in hits:
            if hit.direction != "outgoing" or hit.damage <= 0:
                continue
            if not _has_hit_source_evidence(hit):
                public_damage["unattributed_missing_source"] += hit.damage
                public_labels["unattributed_missing_source"] = "来源字段缺失"
                continue
            key, label = classify_battle_hit_channel(hit)
            if key in {"other_topple", "special_daffodill_extra_topple"}:
                contributions = _topple_role_contributions(
                    hit,
                    replay_by_event.get(hit.event_id),
                )
                if not contributions:
                    has_replay = hit.event_id in replay_by_event
                    public_damage["unattributed_topple"] += hit.damage
                    public_labels["unattributed_topple"] = (
                        "倾陷归属证据不足"
                        if has_replay
                        else "倾陷归属待计算"
                    )
                    if has_replay:
                        unresolved_topple_attribution = True
                    else:
                        pending_topple_attribution = True
                    continue
                for character_id, character_name, damage in contributions:
                    role_names.setdefault(character_id, character_name)
                    role_damage[character_id]["topple"] += damage
                    role_labels[character_id]["topple"] = "倾陷"
                continue
            character_attributable = (
                key in _CHARACTER_ATTRIBUTABLE_SYSTEM_CHANNELS
                and hit.character_id is not None
            )
            if key in _SYSTEM_COMPOSITION_CHANNELS and not character_attributable:
                system_damage[key] += hit.damage
                system_labels[key] = label
                continue
            if hit.character_id is None:
                public_damage["other_unattributed"] += hit.damage
                public_labels["other_unattributed"] = "未归因伤害"
                continue
            character_id = int(hit.character_id)
            role_names.setdefault(character_id, hit.character_name)
            role_key, role_label = (
                _fine_hit_channel(hit, key, label)
                if grouping == "fine"
                else _coarse_role_channel(key, label)
            )
            role_damage[character_id][role_key] += hit.damage
            role_labels[character_id][role_key] = role_label
        for event in max_hp_events:
            if event.effective_hp_loss <= 0:
                continue
            if event.source_character_id is not None:
                character_id = int(event.source_character_id)
                role_names.setdefault(character_id, event.source_character_name)
                event_key = "max_hp_reduction"
                event_label = "生命上限结算"
                if grouping == "fine":
                    event_key = f"max_hp_reduction|{event.mechanic_kind}"
                    event_label = event.mechanic_name
                role_damage[character_id][event_key] += event.effective_hp_loss
                role_labels[character_id][event_key] = event_label
            else:
                public_damage["other_unattributed"] += event.effective_hp_loss
                public_labels["other_unattributed"] = (
                    "未归因生命上限结算"
                )

        return _finalize_composition(
            role_rows=tuple(
                (
                    character_id,
                    character_name,
                    sum(role_damage.get(character_id, {}).values()),
                )
                for character_id, character_name in role_names.items()
                if sum(role_damage.get(character_id, {}).values()) > 0
            ),
            role_damage={key: dict(value) for key, value in role_damage.items()},
            role_labels={key: dict(value) for key, value in role_labels.items()},
            public_damage=dict(public_damage),
            public_labels=public_labels,
            system_damage=dict(system_damage),
            system_labels=system_labels,
            segment_total_damage=segment_total_damage,
            pending_topple_attribution=pending_topple_attribution,
            unresolved_topple_attribution=unresolved_topple_attribution,
        )
