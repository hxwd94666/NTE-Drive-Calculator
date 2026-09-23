# 从同一版本来源构建独立角色和弧盘目录，不补入旧版战斗资产。
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.game_data.build_static_database import StaticDatabaseBuilder, build_database
from tools.game_data.static_database_build_support import (
    IMPORTER_VERSION, SCHEMA_PATHS, TABLE_PATHS, StaticDatabaseError,
)

SOURCE_NAMES = (
    "character", "character_abilities", "character_ability_effects", "character_breakthroughs",
    "character_upgrades", "player_pack", "player_modify", "skill_damage", "skill_damage_modifiers",
    "equipment", "equipment_attributes", "equipment_shapes", "character_equipment_slots",
    "equipment_slot_modify", "equipment_suits", "equipment_plans", "equipment_strength",
    "equipment_curves", "equipment_core_random", "fork_types", "fork_items", "fork_upgrades",
    "fork_stars", "fork_buff_curves", "fork_breakthroughs", "fork_modify", "likeability_roles",
    "likeability_modify", "cultivation_guides", "gameplay_ability_tips", "gameplay_effect_mapping",
    "equipment_modify", "equipment_buff_curves", "item_catalog", "capital_item_catalog",
    "item_qualities", "string_item",
)


class RoleCatalogBuilder(StaticDatabaseBuilder):
    source_tables = {name: TABLE_PATHS[name] for name in SOURCE_NAMES}
    catalog_scope = "role_page"

    def _import_reference_details(self):
        """角色目录的专用构建器可追加只读图鉴投影。"""

    def _import_role_progression_catalog(self) -> None:
        """Import only fork-related item facts required by role-page displays."""

        if self.catalog_scope != "role_page":
            return
        item_ids = self._collect_fork_progression_item_ids()
        self._import_progression_items(item_ids)
        self._import_progression_aliases()
        self._import_fork_exp_materials()
        self._import_item_quality_terms()

    def build(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for path in SCHEMA_PATHS:
            self.connection.executescript(path.read_text(encoding="utf-8"))
            self.connection.execute("INSERT INTO schema_migration VALUES (?, ?)", (int(path.name.split("_", 1)[0]), now))
        self.connection.execute("INSERT INTO dataset VALUES (?, ?, ?)", (self.dataset_id, IMPORTER_VERSION, now))
        self.connection.execute("INSERT INTO dataset_scope VALUES (?, ?)", (self.dataset_id, self.catalog_scope))
        self._mirror_sources()
        self._mirror_awaken_sources()
        self._mirror_character_effect_curve_sources()
        self._select_role_rows()
        for operation in (
            self._import_characters, self._import_character_awakens, self._import_character_panel_growth,
            self._import_character_skills, self._import_skill_damage, self._import_equipment_attributes,
            self._import_official_character_shape_bonuses, self._import_character_likeability_bonuses,
            self._import_equipment_shapes, self._import_equipment_suits, self._import_equipment_items,
            self._import_equipment_progression, self._import_equipment_plans, self._import_default_character_weights,
            self._import_forks, self._import_role_progression_catalog,
            self._import_gameplay_abilities, self._import_gameplay_effect_catalog,
            self._import_cultivation_guides, self._import_combat_curves,
        ):
            operation()
        self._import_reference_details()
        self.fork_permanent_property_audit = [{"status": "unavailable", "reason": "combat_blueprints_not_in_role_catalog"}]
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise StaticDatabaseError(f"角色目录外键校验失败：{violations[:10]}")
        self.connection.commit()
        return self._database_counts()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    candidate = args.candidate_dir.resolve()
    if ROOT / "build" not in candidate.parents:
        parser.error("角色目录必须先生成到仓库 build 下的候选目录")
    report = build_database(
        args.source, candidate / "game_static.sqlite3", candidate / "report",
        dataset_id=args.dataset_id, as_of=args.as_of, include_source_payloads=False,
        manifest_path=candidate / "manifest.json", builder_type=RoleCatalogBuilder, populate_templates=False,
    )
    (candidate / "report/role_catalog_scope.json").write_text(json.dumps({
        "dataset_id": args.dataset_id, "catalog_scope": "role_page",
        "excluded_roles": report["excluded_roles"],
        "unavailable": ["combat_blueprints", "fork_permanent_effects", "graduation_templates"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset_id": args.dataset_id, "counts": report["database_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
