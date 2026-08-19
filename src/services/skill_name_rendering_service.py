# 将稳定 GA/GE 标识解析为官方中文技能名，供角色页和战报共同复用。
"""Qt-free localized skill-name presentation built from static game data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path

from src.integrations.bundled_resources import bundled_config_dir
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_ABILITY_CATEGORY_SUFFIXES = (
    ("_UltraSkill", "极轨终结"),
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
    ) -> None:
        self._ability_names = {
            str(row.get("ability_id") or ""): str(row.get("name_zh") or "").strip()
            for row in ability_rows
            if str(row.get("ability_id") or "")
            and str(row.get("name_zh") or "").strip()
            and not str(row.get("name_zh") or "").strip().isdigit()
        }
        self._damage_abilities = {
            str(row.get("damage_id") or ""): str(row.get("ability_id") or "")
            for row in damage_bindings
            if str(row.get("damage_id") or "")
            and str(row.get("ability_id") or "")
        }
        self._damage_semantics = {
            str(row.get("damage_id") or ""): (
                str(row.get("damage_name_zh") or "").strip(),
                bool(row.get("show_parent_ability", True)),
            )
            for row in semantic_rows
            if str(row.get("damage_id") or "")
            and str(row.get("damage_name_zh") or "").strip()
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

    def resolve_damage_name(
        self,
        damage_id: str,
        *,
        ability_id: str | None = None,
    ) -> str | None:
        stable_damage_id = str(damage_id or "").strip()
        stable_ability_id = str(ability_id or "").strip()
        if not stable_ability_id:
            stable_ability_id = self._damage_abilities.get(stable_damage_id, "")
        ability_name = self.resolve_ability_name(stable_ability_id)
        semantic = self._damage_semantics.get(stable_damage_id)
        if semantic is None:
            return ability_name
        component_name, show_parent_ability = semantic
        if show_parent_ability and ability_name:
            return f"{ability_name} · {component_name}"
        return component_name

    def render_damage_type_name(
        self,
        damage_type: str,
        *,
        fallback: str | None = None,
    ) -> str:
        stable_type = str(damage_type or "").strip()
        return _DAMAGE_TYPE_NAMES.get(
            stable_type,
            str(fallback or stable_type or "未知伤害"),
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
