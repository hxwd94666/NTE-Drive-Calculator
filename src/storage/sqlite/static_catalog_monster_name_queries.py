# 游戏资料库怪物正式数字家族的只读名称候选查询。
"""Structured mon/boss family queries used only for conservative display fallback."""

from __future__ import annotations

from typing import Any


def _numeric_identity(value: object) -> tuple[str, int] | None:
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if text.casefold().endswith("_c"):
        text = text[:-2]
    parts = text.casefold().split("_")
    if len(parts) < 2 or parts[0] not in {"mon", "boss"}:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


class StaticCatalogMonsterNameQueriesMixin:
    """Keep numeric-family display evidence separate from exact identity links."""

    def profile_family_candidates(
        self, monster_template_name: str,
    ) -> list[dict[str, Any]]:
        identity = _numeric_identity(monster_template_name)
        if identity is None:
            return []
        rows = self._rows(
            """SELECT static_table, monster_id
               FROM monster_instance_profile
               WHERE lower(monster_id) LIKE ?
               ORDER BY static_table, monster_id""",
            (f"{identity[0]}_%",),
        )
        return [
            row for row in rows
            if _numeric_identity(row.get("monster_id")) == identity
        ]

    def profile_family_display_evidence(
        self, monster_template_name: str,
    ) -> list[dict[str, Any]]:
        """Return structured official names in the same numeric display family."""

        identity = _numeric_identity(monster_template_name)
        if identity is None:
            return []
        rows = self._rows(
            """
            SELECT b.monster_template_name AS formal_id, m.name_zh,
                   'manual_binding' AS evidence_kind,
                   b.monster_manual_id AS evidence_id
            FROM monster_template_binding AS b
            JOIN monster_catalog AS m USING (monster_manual_id)
            WHERE lower(b.monster_template_name) LIKE ?
            UNION ALL
            SELECT s.boss_monster_id, d.boss_name_zh,
                   'feast_boss_name', s.stage_id
            FROM feast_stage AS s
            JOIN feast_stage_difficulty AS d USING (stage_id)
            WHERE trim(COALESCE(d.boss_name_zh, '')) <> ''
            UNION ALL
            SELECT monster_class_path, monster_name_zh,
                   'outer_realm_spawn_name', monster_pool_id
            FROM abyss_monster_pool_entry
            WHERE trim(COALESCE(monster_name_zh, '')) <> ''
            ORDER BY evidence_kind, evidence_id, formal_id
            """,
            (f"{identity[0]}_%",),
        )
        return [
            row for row in rows
            if _numeric_identity(row.get("formal_id")) == identity
        ]
