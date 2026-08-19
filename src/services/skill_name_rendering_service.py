# 将稳定 GA/GE 标识解析为官方中文技能名，供角色页和战报共同复用。
"""Qt-free localized skill-name presentation built from static game data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_ABILITY_CATEGORY_SUFFIXES = (
    ("_UltraSkill", "极轨终结"),
    ("_QTE", "援护技"),
    ("_Melee", "普通攻击"),
    ("_Skill", "变轨技能"),
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

    @classmethod
    def from_static_dao(
        cls,
        static_dao: StaticGameDataDao,
    ) -> SkillNameRenderingService:
        return cls(
            ability_rows=static_dao.list_gameplay_ability_names(),
            damage_bindings=static_dao.list_skill_damage_name_bindings(),
        )

    @classmethod
    def from_static_database(
        cls,
        database_path: str | Path | None = None,
    ) -> SkillNameRenderingService:
        with StaticGameDataDao(database_path) as static_dao:
            return cls.from_static_dao(static_dao)

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
        return self.resolve_ability_name(stable_ability_id)

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
