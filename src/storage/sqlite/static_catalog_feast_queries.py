# 游戏资料库争锋赏宴往期活动的窄只读查询。
"""Historical Feast queries backed by retained formal clone/profile rows."""

from __future__ import annotations

from typing import Any


class StaticCatalogHistoricalFeastQueriesMixin:
    """Project old Feast stages without fabricating removed rule options."""

    def historical_feast_setup(self, stage_id: str) -> dict[str, Any] | None:
        stage = self._one(
            """
            SELECT clone_id AS stage_id, name_zh, source_row_id
            FROM clone_activity
            WHERE clone_id = ? AND clone_type = 'DiyBossClone'
            """,
            (str(stage_id),),
        )
        if stage is None:
            return None
        stage["difficulties"] = self._rows(
            """
            WITH variants AS (
                SELECT p.static_table, p.monster_id, v.threshold_level,
                       v.profile_set, v.pack_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY p.static_table, p.monster_id
                           ORDER BY v.threshold_level
                       ) AS difficulty_id
                FROM monster_instance_profile AS p
                JOIN monster_instance_profile_variant AS v
                  USING (static_table, monster_id)
                WHERE v.variant_kind = 'clone_level'
            ), difficulty_labels AS (
                SELECT difficulty_id, MIN(name_zh) AS name_zh,
                       MIN(base_score) AS base_score,
                       MIN(score_rate) AS score_rate
                FROM feast_stage_difficulty GROUP BY difficulty_id
            )
            SELECT d.difficulty_ordinal + 1 AS difficulty_id,
                   labels.name_zh, labels.base_score, labels.score_rate,
                   variants.threshold_level AS monster_level,
                   variants.profile_set, variants.pack_id,
                   member.monster_template_name AS boss_monster_id,
                   '' AS boss_icon_path
            FROM clone_activity_difficulty AS d
            JOIN clone_spawn_member AS member USING (spawn_id)
            JOIN variants
              ON lower(variants.monster_id) = lower(member.monster_template_name)
             AND variants.difficulty_id = d.difficulty_ordinal + 1
            JOIN difficulty_labels AS labels
              ON labels.difficulty_id = d.difficulty_ordinal + 1
            WHERE d.clone_id = ?
            ORDER BY d.difficulty_ordinal
            """,
            (str(stage_id),),
        )
        if not stage["difficulties"]:
            return None
        stage["boss_monster_id"] = stage["difficulties"][0]["boss_monster_id"]
        stage["options"] = []
        stage["source"] = self.source_trace(stage.get("source_row_id"))
        return stage

    def historical_feast_encounter(
        self, stage_id: str, difficulty_id: int,
    ) -> dict[str, Any] | None:
        setup = self.historical_feast_setup(stage_id)
        if setup is None:
            return None
        difficulty = next(
            (
                row for row in setup["difficulties"]
                if int(row["difficulty_id"]) == int(difficulty_id)
            ),
            None,
        )
        if difficulty is None:
            return None
        row = {
            **difficulty,
            "stage_id": str(setup["stage_id"]),
            "name_zh": setup.get("name_zh"),
            "source": setup.get("source"),
            "source_row_id": setup.get("source_row_id"),
            "difficulty_name_zh": difficulty.get("name_zh"),
            "special_high_difficulty": 0,
            "options": [],
        }
        row["profile"] = self.combat_profile(
            row["profile_set"], row["pack_id"]
        )
        return row
