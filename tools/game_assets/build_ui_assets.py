# 从开发者准备的游戏文件中构建小尺寸、可审计的界面图片资源。
"""Build a bounded UI asset subset from a prepared official-file directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("ui_asset_manifest.json")
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "game_ui"
CONTENT_ROOT_ENV = "NTE_OFFICIAL_CONTENT_ROOT"
STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"
MANIFEST_ENV = "NTE_UI_ASSET_MANIFEST"
OUTPUT_ENV = "NTE_UI_ASSET_OUTPUT"
LOCAL_CONFIG_ENV = "NTE_LOCAL_CONFIG"
DEFAULT_STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"

_DATABASE_GROUPS = {
    "equipment_modules": (
        "item_id",
        "SELECT item_id, icon_path FROM equipment_item WHERE kind = 'module' ORDER BY item_id",
        "equipment/module/{identity}.png",
        128,
    ),
    "fork_items": (
        "fork_id",
        "SELECT fork_id, icon_path FROM fork_item ORDER BY fork_id",
        "forks/{identity}.png",
        128,
    ),
}


def _source_file(content_root: Path, asset_path: str) -> Path:
    package_path = str(asset_path).split(".", 1)[0]
    prefix = "/Game/"
    if not package_path.startswith(prefix):
        raise ValueError(f"资源路径必须以 {prefix} 开头：{asset_path}")
    return content_root / f"{package_path[len(prefix):]}.png"


def _safe_output(output_root: Path, relative_path: str) -> Path:
    target = (output_root / relative_path).resolve()
    root = output_root.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"输出路径越过目标目录：{relative_path}")
    if target.suffix.lower() != ".png":
        raise ValueError(f"界面资源必须输出为 PNG：{relative_path}")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_local_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"本机配置文件不存在：{resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"本机配置必须是 JSON 对象：{resolved}")
    return data


def _configured_path(
    explicit: Path | None,
    environment_name: str,
    config: dict[str, Any],
    config_key: str,
    default: Path | None = None,
) -> Path | None:
    if explicit is not None:
        return explicit
    environment_value = os.environ.get(environment_name)
    if environment_value:
        return Path(environment_value)
    config_value = config.get(config_key)
    if isinstance(config_value, str) and config_value:
        return Path(config_value)
    return default


def _database_asset_entries(
    group_name: str,
    static_database_path: Path,
) -> list[dict[str, str]]:
    try:
        identity_field, query, output_template, _max_dimension = _DATABASE_GROUPS[group_name]
    except KeyError as exc:
        raise ValueError(f"未知的静态资源组：{group_name}") from exc
    if not static_database_path.is_file():
        raise FileNotFoundError(f"静态游戏数据库不存在：{static_database_path}")
    with sqlite3.connect(static_database_path) as connection:
        rows = connection.execute(query).fetchall()
    entries = []
    for identity, source_asset_path in rows:
        if not isinstance(source_asset_path, str) or not source_asset_path:
            raise ValueError(f"{group_name}/{identity} 缺少官方图标路径")
        entries.append({
            identity_field: str(identity),
            "source_asset_path": source_asset_path,
            "output": output_template.format(identity=identity),
        })
    return entries


def _monster_asset_entries(content_root: Path, table: dict[str, Any]) -> list[dict[str, str]]:
    static_table = str(table["static_table"])
    source_table = content_root / str(table["source_table"])
    source_data = json.loads(source_table.read_text(encoding="utf-8"))
    rows = source_data[0].get("Rows", {}) if source_data else {}
    if not isinstance(rows, dict):
        raise ValueError(f"怪物表 Rows 不是对象：{source_table}")
    entries = []
    for monster_id, row in sorted(rows.items()):
        if not isinstance(row, dict):
            continue
        icon = row.get("icon")
        source_asset_path = icon.get("AssetPathName") if isinstance(icon, dict) else None
        if not isinstance(source_asset_path, str) or not source_asset_path.startswith("/Game/"):
            continue
        source_name = Path(source_asset_path.split(".", 1)[0]).name
        entries.append({
            "key": f"{static_table}:{monster_id}",
            "source_asset_path": source_asset_path,
            "output": f"monsters/{static_table}/{source_name}.png",
        })
    return entries


def build_assets(
    content_root: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_root: Path = DEFAULT_OUTPUT,
    static_database_path: Path = DEFAULT_STATIC_DATABASE,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content_root = content_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    static_database_path = static_database_path.expanduser().resolve()
    if not content_root.is_dir():
        raise FileNotFoundError(f"游戏文件 Content 目录不存在：{content_root}")

    built_by_output: dict[str, dict[str, Any]] = {}
    result_maps: dict[str, dict[str, str]] = {}
    groups: list[tuple[str, str, list[dict[str, str]], int]] = [
        ("characters", "character_id", manifest.get("characters", []), 256),
        ("attributes", "key", manifest.get("attributes", []), 96),
        ("equipment_items", "item_id", manifest.get("equipment_items", []), 128),
    ]
    for group_name in manifest.get("database_groups", []):
        identity_field, _query, _output, max_dimension = _DATABASE_GROUPS[str(group_name)]
        groups.append(
            (
                str(group_name),
                identity_field,
                _database_asset_entries(str(group_name), static_database_path),
                max_dimension,
            )
        )
    monster_entries: list[dict[str, str]] = []
    for monster_table in manifest.get("monster_tables", []):
        monster_entries.extend(_monster_asset_entries(content_root, monster_table))
    if monster_entries:
        groups.append(("monster_icons", "key", monster_entries, 128))

    for group_name, identity_field, entries, max_dimension in groups:
        result_map = result_maps.setdefault(group_name, {})
        for entry in entries:
            relative_output = str(entry["output"]).replace("\\", "/")
            identity = str(entry[identity_field])
            result_map[identity] = relative_output
            if relative_output in built_by_output:
                continue
            source = _source_file(content_root, str(entry["source_asset_path"]))
            if not source.is_file():
                raise FileNotFoundError(f"清单资源不存在：{source}")
            target = _safe_output(output_root, relative_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                image.load()
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")
                image.save(target, format="PNG", optimize=True, compress_level=9)
                width, height = image.size
            built_by_output[relative_output] = {
                "source_asset_path": entry["source_asset_path"],
                "width": width,
                "height": height,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }

    result = {
        "manifest_version": int(manifest["manifest_version"]),
        "source_data_table": manifest["source_data_table"],
        **result_maps,
        "files": built_by_output,
        "total_files": len(built_by_output),
        "total_bytes": sum(row["bytes"] for row in built_by_output.values()),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            Path(os.environ[LOCAL_CONFIG_ENV])
            if os.environ.get(LOCAL_CONFIG_ENV)
            else None
        ),
        help=f"仓库外的本机 JSON 配置；也可设置 {LOCAL_CONFIG_ENV}",
    )
    parser.add_argument(
        "--content-root",
        type=Path,
        default=None,
        help=f"开发者准备的 Content 目录；也可设置 {CONTENT_ROOT_ENV}",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"资源选择清单；也可设置 {MANIFEST_ENV}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"轻量 PNG 输出目录；也可设置 {OUTPUT_ENV}",
    )
    parser.add_argument(
        "--static-database",
        type=Path,
        default=None,
        help=f"提供驱动与弧盘图标映射的静态数据库；也可设置 {STATIC_DATABASE_ENV}",
    )
    args = parser.parse_args()
    config = _load_local_config(args.config)
    args.content_root = _configured_path(
        args.content_root,
        CONTENT_ROOT_ENV,
        config,
        "official_content_root",
    )
    args.manifest = _configured_path(
        args.manifest,
        MANIFEST_ENV,
        config,
        "ui_asset_manifest",
        DEFAULT_MANIFEST,
    )
    args.output = _configured_path(
        args.output,
        OUTPUT_ENV,
        config,
        "ui_asset_output",
        DEFAULT_OUTPUT,
    )
    args.static_database = _configured_path(
        args.static_database,
        STATIC_DATABASE_ENV,
        config,
        "game_static_database",
        DEFAULT_STATIC_DATABASE,
    )
    return args


def main() -> int:
    args = parse_args()
    if args.content_root is None:
        raise SystemExit("必须通过 --content-root 或 NTE_OFFICIAL_CONTENT_ROOT 指定 Content 目录")
    result = build_assets(
        args.content_root,
        args.manifest,
        args.output,
        args.static_database,
    )
    print(
        f"已构建 {result['total_files']} 个轻量界面资源，"
        f"总计 {result['total_bytes'] / (1024 * 1024):.2f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
