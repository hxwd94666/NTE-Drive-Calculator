# 构建独立只读图鉴，按正式配置展示轨外，不提升为战报计算证据。
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.game_data.build_role_catalog import RoleCatalogBuilder
from tools.game_data.build_static_database import build_database
from tools.game_data.static_database_build_support import (
    TABLE_PATHS, asset_path, bool_int, text_parts,
)
from tools.game_data.static_database_progression_imports import ProgressionImportMixin


class ReferenceCatalogBuilder(RoleCatalogBuilder):
    source_tables = {**TABLE_PATHS,
        "lottery_heiyu": "DataAssets/Lottery/DA_LotteryModuleHeiYu.json",
        "lottery_mingyin": "DataAssets/Lottery/DA_LotteryModuleMingYin.json",
    }
    limited_lottery_sources = (*ProgressionImportMixin.limited_lottery_sources, "lottery_heiyu", "lottery_mingyin")
    catalog_scope = "reference"

    def _import_character_release_annotations(self):
        super()._import_character_release_annotations()
        for identity, row in self.rows["character"].items():
            if self.connection.execute("SELECT 1 FROM character_release_annotation WHERE character_id=?", (identity,)).fetchone():
                continue
            quality = {"EItemQuality::ITEM_QUALITY_ORANGE": "S", "EItemQuality::ITEM_QUALITY_PURPLE": "A"}.get(row.get("ItemQuality"))
            memberships = self.connection.execute(
                "SELECT DISTINCT acquisition_type FROM character_acquisition_membership WHERE character_id=?",
                (identity,),
            ).fetchall()
            acquisition = memberships[0][0] if len(memberships) == 1 else None
            self.connection.execute("INSERT INTO character_release_annotation VALUES (?,?,?,?,?,?,?,?)",
                (identity, quality, "official" if quality else None, acquisition,
                 "official" if acquisition else None, None, None, self.source_row_id("character", identity)))

    def _import_reference_details(self):
        for operation in (
            self._import_combat_context, self._import_enemy_combat_profiles,
            self._import_roguelike_modifiers, self._import_monster_instance_profiles,
            self._import_abyss_bindings, self._import_monster_catalog,
            self._import_equipment_effect_sources, self._import_encounter_catalogs,
            self._import_progression_catalog,
        ):
            operation()

    def _import_outer_realm_buffs(self):
        # 图鉴只投影正式赛季表，不消费战报的已审计组件表或任务推断序号。
        for config_id, season in sorted(self.rows["abyss_seasons"].items()):
            if config_id not in self.rows["abyss_clone_levels"]:
                continue
            name = text_parts(season.get("SeasonName"))[0]
            if not name:
                continue
            buff_id = str(season.get("BuffID") or "")
            buff = self.rows["abyss_buff_configs"].get(buff_id, {})
            entries = buff.get("CloneBuffArray") or ()
            effect = entries[0] if len(entries) == 1 else {}
            self.connection.execute(
                "INSERT INTO catalog_outer_realm_season VALUES (?,?,?,?,?,?,?,?,?)",
                (config_id, name, buff_id, text_parts(buff.get("BuffName"))[0] or "",
                 text_parts(buff.get("BuffDesc"))[0] or "", asset_path(effect.get("BuffGE")),
                 bool_int(effect.get("bAddToCharacter")) if effect else None,
                 self.source_row_id("abyss_seasons", config_id),
                 self.source_row_id("abyss_buff_configs", buff_id) if buff else None),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    candidate = args.candidate_dir.resolve()
    if ROOT / "build" not in candidate.parents:
        parser.error("图鉴必须先生成到 build 下的候选目录")
    report = build_database(
        args.source, candidate / "game_static.sqlite3", candidate / "report",
        dataset_id=args.dataset_id, as_of=args.as_of, include_source_payloads=False,
        manifest_path=candidate / "manifest.json", builder_type=ReferenceCatalogBuilder,
        populate_templates=False,
    )
    print(json.dumps({"dataset_id": args.dataset_id, "counts": report["database_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
