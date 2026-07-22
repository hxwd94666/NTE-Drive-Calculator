# 从开发者准备的游戏文件中构建小尺寸、可审计的界面图片资源。
"""Build a bounded UI asset subset from a prepared official-file directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("ui_asset_manifest.json")
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "game_ui"
CONTENT_ROOT_ENV = "NTE_OFFICIAL_CONTENT_ROOT"


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


def build_assets(
    content_root: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_root: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content_root = content_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not content_root.is_dir():
        raise FileNotFoundError(f"游戏文件 Content 目录不存在：{content_root}")

    built_by_output: dict[str, dict[str, Any]] = {}
    character_map: dict[str, str] = {}
    attribute_map: dict[str, str] = {}
    equipment_item_map: dict[str, str] = {}
    groups = (
        ("characters", "character_id", character_map, 256),
        ("attributes", "key", attribute_map, 96),
        ("equipment_items", "item_id", equipment_item_map, 128),
    )
    for group_name, identity_field, result_map, max_dimension in groups:
        for entry in manifest.get(group_name, []):
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
        "characters": character_map,
        "attributes": attribute_map,
        "equipment_items": equipment_item_map,
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
        "--content-root",
        type=Path,
        default=Path(os.environ[CONTENT_ROOT_ENV]) if os.environ.get(CONTENT_ROOT_ENV) else None,
        help=f"开发者准备的 Content 目录；也可设置 {CONTENT_ROOT_ENV}",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.content_root is None:
        raise SystemExit("必须通过 --content-root 或 NTE_OFFICIAL_CONTENT_ROOT 指定 Content 目录")
    result = build_assets(args.content_root, args.manifest, args.output)
    print(
        f"已构建 {result['total_files']} 个轻量界面资源，"
        f"总计 {result['total_bytes'] / (1024 * 1024):.2f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
