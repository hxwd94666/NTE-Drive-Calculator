# 根据同一图鉴的正式头像、默认时装和道具关系生成图片清单。
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.game_assets.build_ui_assets import (
    DEFAULT_MANIFEST, _database_asset_entries, _encounter_database_asset_entries,
    _monster_asset_entries, build_assets,
)


def reference_recipe(source: Path, database: Path) -> dict:
    recipe = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    characters = json.loads((source / "DataTable/Character/DT_Character.json").read_text(encoding="utf-8"))[0]["Rows"]
    appearances = json.loads((source / "DataTable/Character/Appearance/DT_AppearanceData.json").read_text(encoding="utf-8"))[0]["Rows"]
    recipe["characters"], recipe["character_arts"], recipe["equipment_items"] = [], [], []
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        identities = {str(row[0]) for row in connection.execute("SELECT character_id FROM character")}
        for identity in sorted(identities):
            row = characters[identity]
            icon = (row.get("ItemIconBig") or row.get("ItemIcon") or {}).get("AssetPathName")
            if icon:
                recipe["characters"].append({"character_id": identity, "source_asset_path": icon, "output": f"characters/{identity}.png"})
        for identity, icon in connection.execute("SELECT item_id, icon_path FROM equipment_item WHERE kind = 'core'"):
            if icon:
                recipe["equipment_items"].append({"item_id": identity, "source_asset_path": icon, "output": f"equipment/core/{identity}.png"})
    for row in appearances.values():
        identity = str(row.get("CharacterID"))
        art = (row.get("PortraitImg") or {}).get("AssetPathName")
        if identity in identities and row.get("IsDefault") and row.get("AppearanceType") == "EAppearanceType::Fashion" and art:
            if any(str(item["character_id"]) == identity for item in recipe["character_arts"]):
                raise ValueError("同一角色存在多个默认立绘")
            recipe["character_arts"].append({"character_id": identity, "source_asset_path": art, "output": f"character_arts/{identity}.png"})
    return recipe


def reference_asset_requests(source: Path, database: Path):
    recipe = reference_recipe(source, database)
    for key in ("characters", "character_arts", "equipment_items", "attributes", "monster_family_icons"):
        for row in recipe.get(key, ()):
            yield row["source_asset_path"]
    for group in recipe["database_groups"]:
        for row in _database_asset_entries(group, database):
            yield row["source_asset_path"]
    for table in recipe["monster_tables"]:
        for row in _monster_asset_entries(source, table):
            yield row["source_asset_path"]
    for rows in _encounter_database_asset_entries(database):
        for row in rows:
            yield row["source_asset_path"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate_dir.resolve()
    if ROOT / "build" not in candidate.parents:
        parser.error("图片只生成到 build 候选目录")
    database = candidate / "game_static.sqlite3"
    recipe_path = candidate / "report/ui_asset_recipe.json"
    recipe_path.write_text(json.dumps(reference_recipe(args.source, database), ensure_ascii=False, indent=2), encoding="utf-8")
    output = candidate / "game_ui"
    manifest = build_assets(args.source, recipe_path, output, database)
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        identity = connection.execute("SELECT dataset_id FROM dataset_scope WHERE scope = 'reference'").fetchone()
    if identity is None:
        raise ValueError("仅支持独立图鉴数据集")
    manifest.update(dataset_id=identity[0], database_sha256=hashlib.sha256(database.read_bytes()).hexdigest().upper(), unresolved_assets=[])
    for metadata in manifest["files"].values():
        relative = metadata["source_asset_path"].split(".", 1)[0][len("/Game/"):] + ".png"
        metadata["source_sha256"] = hashlib.sha256((args.source / relative).read_bytes()).hexdigest().upper()
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": manifest["total_files"], "character_arts": len(manifest["character_arts"])}))


if __name__ == "__main__":
    main()
