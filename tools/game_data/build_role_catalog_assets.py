# 仅按独立目录中的正式角色和弧盘引用生成带哈希的轻量图片。
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from PIL import Image


def asset_requests(source: Path, database: Path):
    characters = json.loads((source / "DataTable/Character/DT_Character.json").read_text(encoding="utf-8"))[0]["Rows"]
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        for (identity,) in connection.execute("SELECT character_id FROM character ORDER BY character_id"):
            path = (characters[str(identity)].get("ItemIcon") or {}).get("AssetPathName")
            if path:
                yield "characters", str(identity), path
        for identity, path in connection.execute("SELECT fork_id, icon_path FROM fork_item ORDER BY fork_id"):
            if path:
                yield "fork_items", identity, path


def build_assets(source: Path, database: Path, output: Path):
    manifest = {"format_version": 1, "characters": {}, "fork_items": {}, "files": {}, "unresolved_assets": []}
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        identity = connection.execute("SELECT dataset_id FROM dataset_scope WHERE scope IN ('role_page', 'reference')").fetchone()
    if not identity:
        raise ValueError("图片构建仅接收独立角色目录")
    manifest.update(dataset_id=identity[0], database_sha256=hashlib.sha256(database.read_bytes()).hexdigest().upper())
    output.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()
    for group, identity, reference in asset_requests(source, database):
        if not reference.startswith("/Game/"):
            raise ValueError("图片缺少正式 Game 资源路径")
        relative = reference.split(".", 1)[0][len("/Game/"):] + ".png"
        original = (source_root / relative).resolve()
        if source_root not in original.parents:
            raise ValueError("图片来源越过 Content 根目录")
        if not original.is_file():
            manifest["unresolved_assets"].append({"group": group, "id": identity, "source": relative})
            continue
        destination = f"{group}/{identity}.png"
        target = (output / destination).resolve()
        if output.resolve() not in target.parents:
            raise ValueError("图片输出越过目录边界")
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(original) as image:
            image = image.convert("RGBA")
            image.thumbnail((128, 128), Image.Resampling.LANCZOS)
            image.save(target, optimize=True)
        manifest[group][identity] = destination
        manifest["files"][destination] = {
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest().upper(),
            "source": relative,
            "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest().upper(),
        }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = build_assets(args.source, args.database, args.output)
    print(json.dumps({"files": len(manifest["files"]), "unresolved": manifest["unresolved_assets"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
