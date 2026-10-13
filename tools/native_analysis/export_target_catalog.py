# 仅从发行静态库导出原生目标目录，运行时不调用 Python 推断战报。
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def export_catalog(database: Path) -> dict:
    from src.services.battle_report.targets.encounter.catalog import BattleEncounterCatalogService
    from src.services.battle_report.targets.catalog import BattleTargetCatalogService
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

    with StaticGameDataDao(database) as dao:
        candidates = [asdict(row) for row in BattleEncounterCatalogService.load(dao)]
        configs = dao.list_outer_realm_configs()
        effects = dao.list_gameplay_effects()
        feast = dao.list_feast_stages()
        metadata = dao.summary()
        ui_catalog = BattleTargetCatalogService.load(dao)
    # No account database, source payload or local path is included in this artifact.
    manifest_path = database.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "format_version": 1,
        "source_sha256": hashlib.sha256(database.read_bytes()).hexdigest().upper(),
        "dataset_id": manifest.get("database", {}).get("dataset_id", ""),
        "dataset_metadata": metadata["dataset"],
        "ui_catalog": ui_catalog,
        "candidates": candidates,
        "outer_realm_configs": [
            {key: row.get(key) for key in ("level_config_id", "starts_at_mainland", "ends_at_mainland", "season_buff")}
            for row in configs
        ],
        "gameplay_effects": [
            {key: row.get(key) for key in ("gameplay_effect_index", "gameplay_effect_id", "class_path")}
            for row in effects
        ],
        "feast_stages": [
            {"stage_id": row.get("stage_id"), "boss_monster_id": row.get("boss_monster_id"),
             "difficulties": [{key: d.get(key) for key in ("difficulty_id", "boss_name_zh")}
                              for d in row.get("difficulties", ())]}
            for row in feast
        ],
    }


def export_axis_catalog(database: Path) -> dict:
    from src.services.battle_report.actions.animation_window import BattleAnimationWindowService
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

    class SingleBinding:
        def __init__(self, dao, binding):
            self.dao = dao
            self.binding = binding

        def list_character_combat_bindings(self, _character_id):
            return [self.binding]

        def get_combat_ability_graph(self, path):
            return self.dao.get_combat_ability_graph(path)

        def get_combat_montage(self, path):
            return self.dao.get_combat_montage(path)

        def get_combat_blueprint_asset(self, path):
            return self.dao.get_combat_blueprint_asset(path)

    templates = {}
    with StaticGameDataDao(database) as dao:
        metadata = dao.summary()["dataset"]
        for character in dao.list_characters():
            character_id = int(character["character_id"])
            for binding in dao.list_character_combat_bindings(character_id):
                asset = str(binding.get("ability_asset_path") or "").strip()
                ability = str(binding.get("ability_id") or "").strip()
                if not asset or not ability or asset.casefold() in templates:
                    continue
                templates[asset.casefold()] = [asdict(c) for c in BattleAnimationWindowService.load_candidates(
                    SingleBinding(dao, binding), character_ids=(character_id,), ability_ids=(ability,),
                )]
    return {"format_version": 1, "dataset_metadata": metadata,
            "source_sha256": hashlib.sha256(database.read_bytes()).hexdigest().upper(),
            "animation_templates_by_asset": templates}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--axis", action="store_true")
    args = parser.parse_args()
    result = export_axis_catalog(args.database) if args.axis else export_catalog(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"axis_templates": len(result.get("animation_templates_by_asset", {})), "candidates": len(result.get("candidates", []))}))
