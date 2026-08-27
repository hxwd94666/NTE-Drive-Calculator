# 战报目标/环境选择所需的静态目录查询。
"""Read-only encounter-selection queries."""

from __future__ import annotations

from typing import Any


class StaticGameDataEncounterQueriesMixin:
    def list_open_world_target_catalog(self) -> list[dict[str, Any]]:
        targets = self._rows(
            """
            SELECT monster_manual_id AS target_id, name_zh, enemy_type,
                   place_zh, trace_type, sort_order
            FROM monster_catalog
            ORDER BY sort_order, name_zh, monster_manual_id
            """
        )
        variants = self._rows(
            """
            SELECT b.monster_manual_id AS target_id,
                   b.monster_template_name, b.binding_kind,
                   p.monster_id, p.monster_level,
                   p.default_profile_set AS profile_set,
                   p.default_pack_id AS pack_id
            FROM monster_template_binding AS b
            LEFT JOIN monster_instance_profile AS p
              ON p.static_table = 'monster_static_big_world'
             AND lower(p.monster_id) = lower(b.monster_template_name)
            ORDER BY b.monster_manual_id,
                     CASE b.binding_kind
                         WHEN 'monster_tag' THEN 0
                         WHEN 'world_boss_id' THEN 1
                         ELSE 2 END,
                     b.monster_template_name
            """
        )
        variants_by_target: dict[str, list[dict[str, Any]]] = {}
        for variant in variants:
            if variant.get("profile_set") and variant.get("pack_id"):
                variant["profile"] = self.get_enemy_combat_profile(
                    variant["profile_set"],
                    variant["pack_id"],
                )
            variants_by_target.setdefault(variant["target_id"], []).append(variant)
        world_boss_variants: dict[str, list[dict[str, Any]]] = {}
        for row in self.list_world_boss_target_fingerprint_rows():
            variant = {
                "target_id": row["target_id"],
                "monster_template_name": row["monster_template_name"],
                "binding_kind": "world_boss_id",
                "monster_id": row["monster_template_name"],
                "monster_level": row["monster_level"],
                "profile_set": row["profile_set"],
                "pack_id": row["pack_id"],
                "profile": self.get_enemy_combat_profile(
                    row["profile_set"], row["pack_id"]
                ),
            }
            world_boss_variants.setdefault(row["target_id"], []).append(variant)
        for target in targets:
            target_id = target["target_id"]
            target["variants"] = (
                world_boss_variants.get(target_id, [])
                if target.get("enemy_type") == "WeeklyBoss"
                else variants_by_target.get(target_id, [])
            )
        return targets

    def list_clone_activity_catalog(self) -> list[dict[str, Any]]:
        fingerprints = {
            (
                str(row["clone_id"]),
                int(row["difficulty_ordinal"]),
                int(row["wave_ordinal"]),
                int(row["entry_ordinal"]),
                str(row["monster_template_name"]).casefold(),
            ): row
            for row in self.list_clone_encounter_fingerprint_rows()
        }
        categories = self._rows(
            """
            SELECT category_id, clone_type, name_zh, ordinal
            FROM clone_activity_category ORDER BY ordinal, category_id
            """
        )
        for category in categories:
            activities = self._rows(
                """
                SELECT clone_id, clone_type, name_zh, description_zh
                FROM clone_activity
                WHERE category_id = ? AND show_in_adventure = 1
                ORDER BY clone_id
                """,
                (category["category_id"],),
            )
            for activity in activities:
                difficulties = self._rows(
                    """
                    SELECT difficulty_ordinal, difficulty_level, team_level,
                           stamina_cost, drop_id, spawn_id,
                           kill_monster_time_limit
                    FROM clone_activity_difficulty
                    WHERE clone_id = ? ORDER BY difficulty_ordinal
                    """,
                    (activity["clone_id"],),
                )
                for difficulty in difficulties:
                    members = self._rows(
                        """
                        SELECT s.wave_ordinal, s.entry_ordinal,
                               s.monster_template_path, s.monster_template_name,
                               s.monster_count, b.monster_manual_id,
                               b.binding_kind, m.name_zh AS monster_name_zh,
                               m.enemy_type
                        FROM clone_spawn_member AS s
                        LEFT JOIN monster_template_binding AS b
                          ON b.monster_template_name = s.monster_template_name
                        LEFT JOIN monster_catalog AS m
                          ON m.monster_manual_id = b.monster_manual_id
                        WHERE s.spawn_id = ?
                        ORDER BY s.wave_ordinal, s.entry_ordinal,
                                 CASE b.binding_kind
                                     WHEN 'monster_tag' THEN 0
                                     WHEN 'world_boss_id' THEN 1
                                     ELSE 2 END
                        """,
                        (difficulty["spawn_id"],),
                    ) if difficulty.get("spawn_id") else []
                    for member in members:
                        fingerprint = fingerprints.get((
                            str(activity["clone_id"]),
                            int(difficulty["difficulty_ordinal"]),
                            int(member["wave_ordinal"]),
                            int(member["entry_ordinal"]),
                            str(member["monster_template_name"]).casefold(),
                        ))
                        if fingerprint is None:
                            continue
                        member["monster_level"] = fingerprint["monster_level"]
                        member["profile_set"] = fingerprint["profile_set"]
                        member["pack_id"] = fingerprint["pack_id"]
                        member["profile"] = fingerprint["profile"]
                    difficulty["spawn_members"] = members
                activity["difficulties"] = difficulties
            category["activities"] = activities
        return categories

    def list_open_world_target_presets(self) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT m.monster_manual_id AS target_id, m.name_zh, m.enemy_type,
                   m.place_zh, p.monster_level, p.default_profile_set AS profile_set,
                   p.default_pack_id AS pack_id
            FROM monster_catalog AS m
            JOIN monster_instance_profile AS p
              ON p.static_table = 'monster_static_big_world'
             AND lower(p.monster_id) = lower(m.monster_manual_id)
            WHERE p.default_pack_id IS NOT NULL
            ORDER BY m.sort_order, m.name_zh, m.monster_manual_id
            """
        )
        for row in rows:
            row["profile"] = self.get_enemy_combat_profile(
                row["profile_set"], row["pack_id"]
            )
        return rows

    def list_world_boss_target_fingerprint_rows(self) -> list[dict[str, Any]]:
        """Return official 异象追猎 bosses and every world-level HP variant."""

        return self._rows(
            """
            SELECT m.monster_manual_id AS target_id, m.name_zh,
                   b.monster_template_name,
                   v.threshold_level AS monster_level,
                   v.profile_set, v.pack_id,
                   e.health_base, e.health_up, e.health_add,
                   e.defense_base, e.defense_up, e.defense_add,
                   e.topple_limit
            FROM monster_catalog AS m
            JOIN monster_template_binding AS b
              ON b.monster_manual_id = m.monster_manual_id
             AND b.binding_kind = 'world_boss_id'
            JOIN monster_instance_profile AS p
              ON p.static_table = 'monster_static_big_world'
             AND lower(p.monster_id) = lower(b.monster_template_name)
            JOIN monster_instance_profile_variant AS v
              ON v.static_table = p.static_table
             AND lower(v.monster_id) = lower(p.monster_id)
             AND v.variant_kind = 'world_level'
            JOIN enemy_combat_profile AS e
              ON e.profile_set = v.profile_set AND e.pack_id = v.pack_id
            WHERE m.enemy_type = 'WeeklyBoss'
            ORDER BY m.monster_manual_id, v.threshold_level
            """
        )

    def list_clone_encounter_fingerprint_rows(self) -> list[dict[str, Any]]:
        """Return calculation-ready clone roster rows at each difficulty."""

        base_rows = self._rows(
            """
            SELECT c.category_id, c.name_zh AS category_name_zh,
                   a.clone_id, a.name_zh AS activity_name_zh,
                   d.difficulty_ordinal, d.difficulty_level, d.team_level,
                   d.spawn_id, s.wave_ordinal, s.entry_ordinal,
                   s.monster_template_path, s.monster_template_name,
                   s.monster_count
            FROM clone_activity AS a
            JOIN clone_activity_category AS c
              ON c.category_id = a.category_id
            JOIN clone_activity_difficulty AS d USING (clone_id)
            JOIN clone_spawn_member AS s USING (spawn_id)
            WHERE a.show_in_adventure = 1
            ORDER BY c.ordinal, a.clone_id, d.difficulty_ordinal,
                     s.wave_ordinal, s.entry_ordinal
            """
        )
        identities: dict[str, tuple[str, str]] = {}
        for row in self._rows(
            """
            SELECT b.monster_template_name, b.monster_manual_id, m.name_zh,
                   b.binding_kind
            FROM monster_template_binding AS b
            JOIN monster_catalog AS m USING (monster_manual_id)
            ORDER BY CASE b.binding_kind
                         WHEN 'monster_tag' THEN 0
                         WHEN 'world_boss_id' THEN 1
                         ELSE 2 END,
                     b.monster_manual_id
            """
        ):
            identities.setdefault(
                str(row["monster_template_name"]).casefold(),
                (str(row["monster_manual_id"]), str(row["name_zh"])),
            )
        profile_tables = {
            str(row["monster_id"]).casefold(): str(row["static_table"])
            for row in self._rows(
                "SELECT static_table, monster_id FROM monster_instance_profile"
            )
        }
        variants: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT static_table, monster_id, threshold_level, profile_set, pack_id
            FROM monster_instance_profile_variant
            WHERE variant_kind = 'clone_level'
            ORDER BY threshold_level
            """
        ):
            key = (
                str(row["static_table"]).casefold(),
                str(row["monster_id"]).casefold(),
            )
            variants.setdefault(key, []).append(row)
        combat_profiles = {
            (str(row["profile_set"]), str(row["pack_id"])): row
            for row in self._rows(
                """
                SELECT profile_set, pack_id, health_base, health_up, health_add,
                       defense_base, defense_up, defense_add, topple_limit
                FROM enemy_combat_profile
                """
            )
        }
        detailed_profiles: dict[tuple[str, str], dict[str, Any]] = {}
        results = []
        for base in base_rows:
            monster_name = str(base["monster_template_name"])
            folded_name = monster_name.casefold()
            static_table = profile_tables.get(folded_name, "")
            available = variants.get((static_table.casefold(), folded_name), ())
            team_level = float(base.get("team_level") or 0.0)
            selected = next(
                (
                    row
                    for row in available
                    if float(row.get("threshold_level") or 0.0) >= team_level
                ),
                available[-1] if available else None,
            )
            if selected is None:
                continue
            profile = combat_profiles.get(
                (str(selected["profile_set"]), str(selected["pack_id"]))
            )
            if profile is None:
                continue
            profile_key = (
                str(selected["profile_set"]),
                str(selected["pack_id"]),
            )
            if profile_key not in detailed_profiles:
                detailed_profiles[profile_key] = (
                    self.get_enemy_combat_profile(*profile_key) or {}
                )
            target_id, target_name = identities.get(
                folded_name,
                (monster_name, monster_name),
            )
            results.append({
                **base,
                "target_id": target_id,
                "target_name": target_name,
                "monster_level": selected["threshold_level"],
                "profile_set": selected["profile_set"],
                "pack_id": selected["pack_id"],
                "profile": detailed_profiles[profile_key],
                **{
                    key: profile.get(key)
                    for key in (
                        "health_base",
                        "health_up",
                        "health_add",
                        "defense_base",
                        "defense_up",
                        "defense_add",
                        "topple_limit",
                    )
                },
            })
        return results

    def list_outer_realm_configs(self) -> list[dict[str, Any]]:
        configs = self._rows(
            """
            SELECT r.level_config_id, MAX(l.level_id) AS max_level,
                   r.starts_at_mainland, r.ends_at_mainland,
                   r.inference_ordinal
            FROM outer_realm_rotation AS r
            JOIN abyss_level AS l USING (level_config_id)
            WHERE r.inference_ordinal IS NOT NULL
            GROUP BY r.level_config_id
            ORDER BY r.inference_ordinal
            """
        )
        for config in configs:
            config["season_buff"] = self.get_outer_realm_season_buff(
                config["level_config_id"]
            )
        return configs

    def get_outer_realm_season_buff(
        self,
        level_config_id: str,
    ) -> dict[str, Any] | None:
        buff = self._one(
            """
            SELECT level_config_id, season_name_zh, buff_id, buff_name_zh,
                   description_zh, gameplay_effect_path, add_to_character
            FROM outer_realm_season_buff WHERE level_config_id = ?
            """,
            (str(level_config_id).strip(),),
        )
        if buff is not None:
            buff["components"] = self._rows(
                """
                SELECT component_ordinal, trigger_kind, property_id,
                       property_value, duration_seconds,
                       trigger_cooldown_seconds, stack_limit_count, curve_id
                FROM outer_realm_season_buff_component
                WHERE level_config_id = ? ORDER BY component_ordinal
                """,
                (buff["level_config_id"],),
            )
        return buff

    def get_outer_realm_topple_recovery(
        self,
        level_config_id: str,
        level_id: int,
        fight_stage: str,
    ) -> dict[str, float] | None:
        selected_stage = (
            str(fight_stage).strip()
            if str(fight_stage).strip().startswith("EAbyssFightStage::")
            else ""
        )
        rows = self._rows(
            """
            SELECT DISTINCT e.topple_limit, e.topple_reduce_reset
            FROM abyss_level_monster_spawn AS s
            JOIN abyss_monster_pool_entry AS p USING (monster_pool_id)
            JOIN enemy_combat_profile AS e
              ON e.profile_set = p.attribute_profile_set
             AND e.pack_id = p.attribute_pack_id
            WHERE s.level_config_id = ? AND s.level_id = ?
              AND (? = '' OR s.fight_stage = ?)
            """,
            (
                str(level_config_id).strip(), int(level_id),
                selected_stage, selected_stage,
            ),
        )
        if len(rows) != 1:
            return None
        return {
            "topple_limit": float(rows[0]["topple_limit"]),
            "topple_recovery_speed": float(rows[0]["topple_reduce_reset"]),
        }

    def list_outer_realm_target_presets(self) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT l.level_config_id, l.level_id, l.name_zh,
                   s.fight_stage, s.spawn_ordinal, s.wave,
                   p.monster_ordinal, p.monster_class_path, p.monster_name_zh,
                   p.monster_count,
                   p.monster_level, p.attribute_profile_set AS profile_set,
                   p.attribute_pack_id AS pack_id,
                   e.health_base, e.health_up, e.health_add,
                   e.defense_base, e.defense_up, e.defense_add,
                   e.topple_limit
            FROM abyss_level AS l
            JOIN abyss_level_monster_spawn AS s
              USING (level_config_id, level_id)
            JOIN abyss_monster_pool_entry AS p USING (monster_pool_id)
            LEFT JOIN enemy_combat_profile AS e
              ON e.profile_set = p.attribute_profile_set
             AND e.pack_id = p.attribute_pack_id
            ORDER BY l.level_config_id, l.level_id, s.fight_stage,
                     s.spawn_ordinal, p.monster_ordinal
            """
        )
        resistance_rows = self._rows(
            """
            SELECT r.profile_set, r.pack_id, r.damage_type, r.resistance_base
            FROM enemy_element_resistance AS r
            JOIN abyss_monster_pool_entry AS p
              ON p.attribute_profile_set = r.profile_set
             AND p.attribute_pack_id = r.pack_id
            GROUP BY r.profile_set, r.pack_id, r.damage_type, r.resistance_base
            """
        )
        resistance_map: dict[tuple[str, str], dict[str, float]] = {}
        for resistance in resistance_rows:
            key = (resistance["profile_set"], resistance["pack_id"])
            resistance_map.setdefault(key, {})[resistance["damage_type"]] = float(
                resistance["resistance_base"]
            )
        for row in rows:
            row["resistances"] = resistance_map.get(
                (row["profile_set"], row["pack_id"]),
                {},
            )
        return rows

    def list_monster_display_names(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT monster_manual_id, name_zh, enemy_type, place_zh
            FROM monster_catalog ORDER BY sort_order, name_zh
            """
        )

    def list_feast_stages(self) -> list[dict[str, Any]]:
        """Return all official feast stages in stable stage order."""

        stages = self._rows(
            """
            SELECT stage_id, name_zh, boss_monster_id, special_high_difficulty
            FROM feast_stage
            ORDER BY CAST(substr(stage_id, 13) AS INTEGER), stage_id
            """
        )
        for stage in stages:
            stage_id = stage["stage_id"]
            difficulties = self._rows(
                """
                SELECT d.*, p.health_base, p.health_up, p.health_add,
                       p.defense_base, p.defense_up, p.defense_add, p.topple_limit
                FROM feast_stage_difficulty AS d
                JOIN enemy_combat_profile AS p
                  ON p.profile_set = d.profile_set AND p.pack_id = d.pack_id
                WHERE d.stage_id = ? ORDER BY d.difficulty_id
                """,
                (stage_id,),
            )
            for difficulty in difficulties:
                profile = self.get_enemy_combat_profile(
                    difficulty["profile_set"], difficulty["pack_id"]
                )
                difficulty["resistances"] = (profile or {}).get("resistances", {})
            stage["difficulties"] = difficulties
            categories: dict[int, dict[str, Any]] = {}
            for row in self._rows(
                """
                SELECT s.category_ordinal, s.option_ordinal, s.category_name_zh,
                       o.option_id, o.option_type, o.effect_kind, o.damage_type,
                       o.add_value, o.limit_seconds, o.score, o.buff_asset_path
                FROM feast_stage_option AS s
                JOIN feast_option AS o USING (option_id)
                WHERE s.stage_id = ?
                ORDER BY s.category_ordinal, s.option_ordinal
                """,
                (stage_id,),
            ):
                category = categories.setdefault(row["category_ordinal"], {
                    "category_ordinal": row["category_ordinal"],
                    "name_zh": row["category_name_zh"],
                    "options": [],
                })
                category["options"].append(row)
            stage["option_categories"] = list(categories.values())
        return stages

    def list_divination_buffs(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT buff_id, name_zh, description_zh, property_id,
                   property_value, is_percent
            FROM divination_buff ORDER BY buff_id
            """
        )
