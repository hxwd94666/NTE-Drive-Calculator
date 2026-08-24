# 将稳定 GA/GE 标识解析为官方中文技能名，供角色页和战报共同复用。
"""Qt-free localized skill-name presentation built from static game data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path

from src.integrations.bundled_resources import bundled_config_dir
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_ABILITY_CATEGORY_SUFFIXES = (
    ("_UltraSkill", "极轨终结"),
    ("_SwitchSkillModB", "G技能"),
    ("_SwitchSkill", "G技能"),
    ("_QTE", "援护技"),
    ("_Melee", "普通攻击"),
    ("_Skill", "变轨技能"),
)

_DAMAGE_TYPE_NAMES = {
    "NORMAL": "物理伤害",
    "COSMOS": "光属性伤害",
    "NATURE": "灵属性伤害",
    "INCANTATION": "咒属性伤害",
    "CHAOS": "暗属性伤害",
    "PSYCHE": "魂属性伤害",
    "LAKSHANA": "相属性伤害",
    "PSYCHICALLY": "心灵伤害",
    "TRUE": "真实伤害",
    "DarkFlame": "暗焰伤害",
    "DragonFlame": "龙焰伤害",
    "HolyFlame": "圣焰伤害",
}

_BATTLE_CLASSIFICATION_NAMES = {
    "direct": "直伤",
    "direct_follow_up": "追加直伤",
    "weave": "覆纹追加攻击",
    "topple": "倾陷伤害",
    "mechanic": "机制伤害",
    "reaction": "环合 / 特殊伤害",
    "max_hp_reduction": "生命上限结算",
}

_ATTACK_TYPE_NAMES = {
    "normal": "普通攻击",
    "melee": "普通攻击",
    "普攻": "普通攻击",
    "skill": "变轨技能",
    "e技能": "变轨技能",
    "ultra": "极轨终结",
    "q技能": "极轨终结",
    "qte": "援护技",
    "follow_up": "追加攻击",
    "follow-up": "追加攻击",
    "special damage": "特殊伤害",
    "passive damage": "被动伤害",
    "awakening damage": "觉醒伤害",
    "other": "其他",
    "unknown": "未知类型",
}


@dataclass(frozen=True, slots=True)
class RenderedBattleSkillIdentity:
    """Localized presentation plus the stable GE identity that supported it."""

    skill_name: str
    damage_name: str
    gameplay_effect_id: str


def preferred_battle_damage_name(
    damage_name: str | None,
    source_skill_name: str | None,
    original_skill_name: str | None = None,
) -> str:
    """Prefer the damage item, then its rendered and original skill identities."""

    damage = str(damage_name or "").strip()
    if damage.casefold() not in {
        "",
        "未识别伤害",
        "未知伤害",
        "unknown damage",
        "unknown",
    }:
        return damage
    source = str(source_skill_name or "").strip()
    if source.casefold() not in {
        "",
        "未识别技能",
        "未知技能",
        "unknown skill",
        "unknown",
    }:
        return source
    original = str(original_skill_name or "").strip()
    if original.casefold() not in {"", "unknown", "none"}:
        return original
    return "未识别技能"


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _localized_attack_fallback(value: str | None) -> str:
    stable = str(value or "").strip()
    localized = render_attack_type(stable)
    if not localized or localized in {"其他", "未知类型"}:
        return ""
    if localized == stable and not _contains_cjk(localized):
        return ""
    return localized


def render_battle_classification(value: str) -> str:
    stable = str(value or "").strip()
    return _BATTLE_CLASSIFICATION_NAMES.get(stable, stable or "未知分类")


def render_attack_type(value: str) -> str:
    stable = str(value or "").strip()
    if not stable:
        return ""
    return _ATTACK_TYPE_NAMES.get(stable.casefold(), stable)


def render_battle_event_type(
    classification: str,
    attack_type: str = "",
    damage_attribute: str = "",
) -> str:
    """Render the hit-log type column without leaking protocol English."""

    values = [render_battle_classification(classification)]
    attack = render_attack_type(attack_type)
    if attack and attack not in values and attack != "未知类型":
        values.append(attack)
    attribute = str(damage_attribute or "").strip()
    if attribute and attribute.casefold() not in {"unknown", "none"}:
        localized = _DAMAGE_TYPE_NAMES.get(
            attribute,
            _DAMAGE_TYPE_NAMES.get(attribute.upper(), attribute),
        )
        if localized not in values:
            values.append(localized)
    return " · ".join(values)


def _load_gameplay_effect_semantics(path: Path) -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if document.get("format_version") != 1:
        return ()
    effects = document.get("effects")
    if not isinstance(effects, dict):
        return ()
    return tuple(
        {"damage_id": damage_id, **semantic}
        for damage_id, semantic in effects.items()
        if isinstance(damage_id, str) and isinstance(semantic, dict)
    )


def ability_category_name(ability_id: str) -> str | None:
    stable_id = str(ability_id or "").strip()
    if "_Passive_" in stable_id:
        return "被动"
    return next(
        (
            category
            for suffix, category in _ABILITY_CATEGORY_SUFFIXES
            if stable_id.endswith(suffix)
        ),
        None,
    )


class SkillNameRenderingService:
    """Resolve stable ability/effect identities without changing domain data."""

    def __init__(
        self,
        *,
        ability_rows: Iterable[Mapping[str, object]],
        damage_bindings: Iterable[Mapping[str, object]] = (),
        semantic_rows: Iterable[Mapping[str, object]] = (),
        gameplay_effect_rows: Iterable[Mapping[str, object]] = (),
    ) -> None:
        damage_rows = tuple(damage_bindings)
        self._ability_names = {
            str(row.get("ability_id") or ""): str(row.get("name_zh") or "").strip()
            for row in ability_rows
            if str(row.get("ability_id") or "")
            and str(row.get("name_zh") or "").strip()
            and not str(row.get("name_zh") or "").strip().isdigit()
        }
        self._damage_abilities = {
            str(row.get("damage_id") or ""): str(row.get("ability_id") or "")
            for row in damage_rows
            if str(row.get("damage_id") or "")
            and str(row.get("ability_id") or "")
        }
        self._damage_attributes = {
            str(row.get("damage_id") or "").casefold(): str(
                row.get("damage_type") or ""
            ).strip().casefold()
            for row in damage_rows
            if str(row.get("damage_id") or "").strip()
            and str(row.get("damage_type") or "").strip()
        }
        self._damage_semantics = {
            str(row.get("damage_id") or ""): {
                "damage_name": str(row.get("damage_name_zh") or "").strip(),
                "show_parent_ability": bool(row.get("show_parent_ability", True)),
                "ability_id": str(row.get("ability") or "").strip(),
                "attack_type": str(row.get("attack_type") or "").strip(),
                "override_observed_ability": bool(
                    row.get("override_observed_ability", False)
                ),
                "override_observed_attack_type": bool(
                    row.get("override_observed_attack_type", False)
                ),
            }
            for row in semantic_rows
            if str(row.get("damage_id") or "")
            and (
                str(row.get("damage_name_zh") or "").strip()
                or str(row.get("ability") or "").strip()
            )
        }
        self._gameplay_effect_ids = {
            int(row["gameplay_effect_index"]): str(
                row.get("gameplay_effect_id") or ""
            ).strip()
            for row in gameplay_effect_rows
            if row.get("gameplay_effect_index") is not None
            and str(row.get("gameplay_effect_id") or "").strip()
        }

    @classmethod
    def from_static_dao(
        cls,
        static_dao: StaticGameDataDao,
        *,
        semantics_path: str | Path | None = None,
    ) -> SkillNameRenderingService:
        resolved_semantics_path = (
            Path(semantics_path)
            if semantics_path is not None
            else bundled_config_dir() / "gameplay_effect_semantics.json"
        )
        return cls(
            ability_rows=static_dao.list_gameplay_ability_names(),
            damage_bindings=static_dao.list_skill_damage_name_bindings(),
            semantic_rows=_load_gameplay_effect_semantics(resolved_semantics_path),
            gameplay_effect_rows=static_dao.list_gameplay_effects(),
        )

    @classmethod
    def from_static_database(
        cls,
        database_path: str | Path | None = None,
        *,
        semantics_path: str | Path | None = None,
    ) -> SkillNameRenderingService:
        with StaticGameDataDao(database_path) as static_dao:
            return cls.from_static_dao(
                static_dao,
                semantics_path=semantics_path,
            )

    def resolve_ability_name(self, ability_id: str) -> str | None:
        return self._ability_names.get(str(ability_id or "").strip())

    def resolve_ability_id(
        self,
        ability_id: str | None,
        damage_id: str | None,
        *,
        fallback_damage_id: str | None = None,
    ) -> str:
        """Resolve an audited GE override before captured or tabular GA bindings."""

        observed_ability = str(ability_id or "").strip()
        damage_ids = tuple(dict.fromkeys((
            str(damage_id or "").strip(),
            str(fallback_damage_id or "").strip(),
        )))
        for stable_damage_id in damage_ids:
            semantic = self._damage_semantics.get(stable_damage_id)
            semantic_ability = str((semantic or {}).get("ability_id") or "")
            if semantic_ability and bool(
                (semantic or {}).get("override_observed_ability", False)
            ):
                return semantic_ability
        if observed_ability:
            return observed_ability
        for stable_damage_id in damage_ids:
            semantic = self._damage_semantics.get(stable_damage_id)
            semantic_ability = str((semantic or {}).get("ability_id") or "")
            if semantic_ability:
                return semantic_ability
            bound_ability = self._damage_abilities.get(stable_damage_id, "")
            if bound_ability:
                return bound_ability
        return ""

    def resolve_attack_type(
        self,
        damage_id: str | None,
        *,
        captured: str | None = None,
    ) -> str:
        """Override a captured type only when the audited semantic opts in."""

        stable_damage_id = str(damage_id or "").strip()
        observed = str(captured or "").strip()
        semantic = self._damage_semantics.get(stable_damage_id)
        semantic_attack = str((semantic or {}).get("attack_type") or "")
        if semantic_attack and bool(
            (semantic or {}).get("override_observed_attack_type", False)
        ):
            return semantic_attack
        return observed or semantic_attack

    def resolve_damage_name(
        self,
        damage_id: str,
        *,
        ability_id: str | None = None,
    ) -> str | None:
        stable_damage_id = str(damage_id or "").strip()
        stable_ability_id = str(ability_id or "").strip()
        semantic = self._damage_semantics.get(stable_damage_id)
        if not stable_ability_id:
            stable_ability_id = str(
                (semantic or {}).get("ability_id")
                or self._damage_abilities.get(stable_damage_id, "")
            )
        ability_name = self.resolve_ability_name(stable_ability_id)
        if semantic is None:
            return ability_name
        component_name = str(semantic["damage_name"])
        if not component_name:
            return ability_name
        if bool(semantic["show_parent_ability"]) and ability_name:
            return f"{ability_name} · {component_name}"
        return component_name

    def resolve_gameplay_effect_id(
        self,
        gameplay_effect_index: int | None,
        gameplay_effect_name: str | None = None,
    ) -> str:
        """Prefer captured GE identity and use the static index only when absent."""

        observed_name = str(gameplay_effect_name or "").strip()
        if observed_name:
            return observed_name
        if gameplay_effect_index is not None:
            resolved = self._gameplay_effect_ids.get(int(gameplay_effect_index))
            if resolved:
                return resolved
        return ""

    def resolve_damage_attribute(
        self,
        damage_id: str | None,
        *,
        captured: str | None = None,
    ) -> str:
        """Prefer a formal captured attribute and fill only missing values statically."""

        observed = str(captured or "").strip().casefold()
        if observed not in {"", "unknown", "none"}:
            return observed
        stable_id = str(damage_id or "").strip().casefold()
        return self._damage_attributes.get(stable_id, observed or "unknown")

    def render_axis_identity(
        self,
        *,
        ability_id: str | None,
        damage_id: str | None,
        gameplay_effect_index: int | None,
        gameplay_effect_name: str | None,
        damage_component: str | None = None,
        attack_type: str | None = None,
    ) -> RenderedBattleSkillIdentity:
        """Resolve user-facing labels while retaining the raw GE as evidence."""

        observed_ability = str(ability_id or "").strip()
        observed_damage = str(damage_id or "").strip()
        effect_id = self.resolve_gameplay_effect_id(
            gameplay_effect_index,
            gameplay_effect_name,
        )
        semantic = self._damage_semantics.get(effect_id or observed_damage)
        resolved_ability = self.resolve_ability_id(
            observed_ability,
            effect_id or observed_damage,
            fallback_damage_id=observed_damage,
        )
        if not any((
            observed_ability,
            observed_damage,
            effect_id,
            str(damage_component or "").strip(),
            str(attack_type or "").strip(),
        )):
            return RenderedBattleSkillIdentity(
                skill_name="未归因伤害",
                damage_name="来源字段缺失",
                gameplay_effect_id="",
            )
        official_ability = self.resolve_ability_name(resolved_ability)
        ability_category = ability_category_name(resolved_ability)
        if official_ability and ability_category:
            skill_name = f"{ability_category}：{official_ability}"
        else:
            skill_name = official_ability or ability_category or ""
        if _contains_cjk(observed_damage):
            damage_name = observed_damage
        elif semantic is not None and str(semantic.get("damage_name") or ""):
            semantic_damage = self.resolve_damage_name(
                observed_damage or effect_id,
                ability_id=resolved_ability,
            )
            damage_name = str(semantic_damage or "").strip()
        elif official_ability:
            # Match toolkit: an unmapped GE falls back through GE -> GA to the
            # official ability name. The stable GE remains available on the
            # returned identity but is not exposed as a user-facing label.
            damage_name = official_ability
        else:
            damage_name = ""
        if not damage_name:
            component = str(damage_component or "").strip()
            attack = _localized_attack_fallback(attack_type)
            damage_name = (
                component
                if _contains_cjk(component)
                else attack
                if attack
                else "未识别伤害"
            )
        if not skill_name:
            attack = _localized_attack_fallback(attack_type)
            skill_name = (
                damage_name
                if damage_name != "未识别伤害"
                else attack or "未识别技能"
            )
        return RenderedBattleSkillIdentity(
            skill_name=skill_name,
            damage_name=damage_name,
            gameplay_effect_id=effect_id,
        )

    def render_damage_type_name(
        self,
        damage_type: str,
        *,
        fallback: str | None = None,
    ) -> str:
        stable_type = str(damage_type or "").strip()
        return _DAMAGE_TYPE_NAMES.get(
            stable_type,
            _DAMAGE_TYPE_NAMES.get(
                stable_type.upper(),
                str(fallback or stable_type or "未知伤害"),
            ),
        )

    def render_ability_name(
        self,
        ability_id: str,
        *,
        fallback: str | None = None,
        include_category: bool = True,
    ) -> str:
        stable_id = str(ability_id or "").strip()
        official_name = self.resolve_ability_name(stable_id)
        category = ability_category_name(stable_id)
        if official_name and include_category and category:
            return f"{category}：{official_name}"
        return official_name or category or str(fallback or stable_id or "未知技能")
