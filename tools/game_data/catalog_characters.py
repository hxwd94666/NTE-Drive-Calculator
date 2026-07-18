# 从整理好的游戏官方文件目录生成可审计的角色数据源清单。
"""从整理好的游戏官方数据目录生成可审计的角色数据源清单。

这是开发者工具，只读取已经准备好的 ``Content`` 数据目录，不修改或搜索游戏安装
文件。生成的报告不是运行时输入；游戏数据更新后可以重新生成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERRIDES = Path(__file__).with_name("character_overrides.json")


class CatalogError(RuntimeError):
    """来源数据不符合预期结构。"""


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_content_root(source: Path) -> Path:
    source = source.expanduser().resolve()
    candidates = (source, source / "Content")
    for candidate in candidates:
        if (candidate / "DataTable").is_dir():
            return candidate
    raise CatalogError(f"在指定目录下找不到 Content/DataTable：{source}")


def load_datatable(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise CatalogError(f"DataTable 文件外层结构不符合预期：{path}")
    table = payload[0]
    rows = table.get("Rows")
    if not isinstance(rows, dict):
        raise CatalogError(f"DataTable 的 Rows 必须是对象：{path}")
    return table, rows


def _text_value(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("LocalizedString", "SourceString", "Key"):
        text = value.get(key)
        if isinstance(text, str) and text:
            return text
    return None


def _asset_path(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    path = value.get("AssetPathName")
    return path if isinstance(path, str) and path else None


def _mainland_show_time(row: dict[str, Any]) -> tuple[str | None, date | None]:
    element = row.get("ElementData")
    if not isinstance(element, dict) or not element.get("bCheckShowTime"):
        return None, None
    show_time = element.get("ShowTime")
    mainland = show_time.get("MainlandTime") if isinstance(show_time, dict) else None
    if not isinstance(mainland, dict):
        return None, None
    try:
        value = datetime(
            int(mainland["Year"]),
            int(mainland["Month"]),
            int(mainland["Day"]),
            int(mainland.get("Hour", 0)),
            int(mainland.get("minute", 0)),
            int(mainland.get("Second", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None, None
    return value.isoformat(timespec="seconds"), value.date()


def summarize_ability_profile(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    proactive = row.get("CharacterAbilityList")
    passive = row.get("PassiveAbilityList")
    proactive = proactive if isinstance(proactive, list) else []
    passive = passive if isinstance(passive, list) else []
    return {
        "proactive_count": len(proactive),
        "passive_count": len(passive),
        "proactive_keys": [item.get("Key") for item in proactive if isinstance(item, dict)],
        "passive_keys": [item.get("Key") for item in passive if isinstance(item, dict)],
    }


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_descriptor(content_root: Path, path: Path, row_count: int) -> dict[str, Any]:
    return {
        "path": path.relative_to(content_root).as_posix(),
        "row_count": row_count,
        "sha256": file_sha256(path),
    }


def build_catalog(
    source: Path,
    model_path: Path | None,
    overrides_path: Path,
    *,
    as_of: date,
) -> dict[str, Any]:
    content_root = resolve_content_root(source)
    character_path = content_root / "DataTable" / "Character" / "DT_Character.json"
    ability_path = content_root / "DataTable" / "Character" / "DT_CharacterAbilityConfig.json"
    pack_path = content_root / "DataTable" / "PackData" / "DT_PlayerPackData.json"

    _, character_rows = load_datatable(character_path)
    _, ability_rows = load_datatable(ability_path)
    _, pack_rows = load_datatable(pack_path)
    model = read_json(model_path) if model_path is not None else {}
    overrides = read_json(overrides_path)
    if not isinstance(model, dict):
        raise CatalogError("my_roles_model.json 必须是以显示名称为键的对象")
    if not isinstance(overrides, dict):
        raise CatalogError("角色覆盖配置必须是对象")

    model_names = set(model)
    aliases = overrides.get("name_aliases", {})
    character_overrides = overrides.get("character_overrides", {})
    profile_overrides = overrides.get("ability_profile_overrides", {})
    if not all(isinstance(value, dict) for value in (aliases, character_overrides, profile_overrides)):
        raise CatalogError("覆盖配置的各分区必须是对象")

    pack_rows_casefold = {str(key).casefold(): str(key) for key in pack_rows}

    characters: list[dict[str, Any]] = []
    matched_model_names: set[str] = set()
    for character_id, row in character_rows.items():
        if not isinstance(row, dict):
            raise CatalogError(f"DT_Character 的第 {character_id} 条记录必须是对象")
        character_id = str(character_id)
        element = row.get("ElementData") if isinstance(row.get("ElementData"), dict) else {}
        source_name = _text_value(row.get("ItemName")) or f"unknown:{character_id}"
        override = character_overrides.get(character_id, {})
        legacy_model_name = override.get("model_name") or aliases.get(source_name, source_name)
        present_in_legacy_model = legacy_model_name in model_names
        if present_in_legacy_model:
            matched_model_names.add(legacy_model_name)

        show_time, show_date = _mainland_show_time(row)
        if override.get("classification"):
            classification = str(override["classification"])
            classification_basis = f"override:{override.get('confidence', 'unspecified')}"
        elif show_date is not None and show_date > as_of:
            classification = "scheduled_character"
            classification_basis = "future_mainland_show_time"
        else:
            classification = "available_character"
            classification_basis = "game_character_table"

        prop_modify_id = element.get("PropModifyID")
        resolved_pack_row_id = (
            pack_rows_casefold.get(prop_modify_id.casefold())
            if isinstance(prop_modify_id, str)
            else None
        )
        normal_profile = ability_rows.get(character_id)
        characters.append(
            {
                "character_id": character_id,
                "source_name": source_name,
                "item_name_key": row.get("ItemName", {}).get("Key")
                if isinstance(row.get("ItemName"), dict)
                else None,
                "logical_character_key": override.get(
                    "logical_character_key", f"character:{character_id}"
                ),
                "legacy_model_name": legacy_model_name if present_in_legacy_model else None,
                "present_in_legacy_model": present_in_legacy_model,
                "classification": classification,
                "classification_basis": classification_basis,
                "canonical_character_id": override.get("canonical_character_id", character_id),
                "variant_key": override.get("variant_key"),
                "classification_reason": override.get("reason"),
                "prop_modify_id": prop_modify_id,
                "resolved_pack_row_id": resolved_pack_row_id,
                "pack_row_exists": resolved_pack_row_id is not None,
                "pack_row_case_mismatch": resolved_pack_row_id is not None
                and resolved_pack_row_id != prop_modify_id,
                "element_type": element.get("CharacterElementType"),
                "group_type": element.get("CharacterGroupType"),
                "actor_class": _asset_path(element.get("CharacterActorClass")),
                "icon": _asset_path(row.get("ItemIcon")),
                "mainland_show_time": show_time,
                "ability_profile": summarize_ability_profile(normal_profile),
            }
        )

    minigame_profiles: list[dict[str, Any]] = []
    for profile_id, override in profile_overrides.items():
        row = ability_rows.get(profile_id)
        normal_id = str(override.get("character_id", ""))
        normal_row = ability_rows.get(normal_id)
        minigame_profiles.append(
            {
                "profile_id": profile_id,
                **override,
                "source_row_exists": isinstance(row, dict),
                "profile": summarize_ability_profile(row),
                "normal_profile_id": normal_id,
                "normal_profile_exists": isinstance(normal_row, dict),
                "identical_to_normal_profile": _json_equal(row, normal_row)
                if isinstance(row, dict) and isinstance(normal_row, dict)
                else None,
            }
        )

    normal_ability_ids = sorted(
        key for key in ability_rows if not str(key).startswith("ft_character_")
    )
    unclassified_special_profiles = sorted(
        key
        for key in ability_rows
        if str(key).startswith("ft_character_") and key not in profile_overrides
    )
    used_pack_rows = {
        item["resolved_pack_row_id"]
        for item in characters
        if isinstance(item.get("resolved_pack_row_id"), str)
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": as_of.isoformat(),
        "inputs": {
            "content_root": str(content_root),
            "my_roles_model": (
                {
                    "path": str(model_path.resolve()),
                    "role_count": len(model),
                    "sha256": file_sha256(model_path),
                    "purpose": "optional_legacy_coverage_audit_only",
                }
                if model_path is not None
                else None
            ),
            "overrides": {
                "path": str(overrides_path.resolve()),
                "sha256": file_sha256(overrides_path),
            },
            "data_tables": {
                "characters": _source_descriptor(content_root, character_path, len(character_rows)),
                "abilities": _source_descriptor(content_root, ability_path, len(ability_rows)),
                "pack_data": _source_descriptor(content_root, pack_path, len(pack_rows)),
            },
        },
        "counts": {
            "character_rows": len(character_rows),
            "logical_character_keys": len(
                {item["logical_character_key"] for item in characters}
            ),
            "legacy_model_roles": len(model),
            "matched_legacy_model_roles": len(matched_model_names),
            "normal_ability_profiles": len(normal_ability_ids),
            "minigame_ability_profiles": len(minigame_profiles),
            "pack_rows": len(pack_rows),
        },
        "characters": sorted(characters, key=lambda item: int(item["character_id"])),
        "minigame_ability_profiles": sorted(
            minigame_profiles, key=lambda item: item["profile_id"]
        ),
        "checks": {
            "unmatched_legacy_model_roles": sorted(model_names - matched_model_names),
            "normal_ability_ids_without_character_row": sorted(
                set(normal_ability_ids) - set(character_rows)
            ),
            "character_ids_without_normal_ability_profile": sorted(
                set(character_rows) - set(normal_ability_ids)
            ),
            "unclassified_special_ability_profiles": unclassified_special_profiles,
            "pack_row_case_mismatches": [
                {
                    "character_id": item["character_id"],
                    "reference": item["prop_modify_id"],
                    "matched_row": item["resolved_pack_row_id"],
                }
                for item in characters
                if item["pack_row_case_mismatch"]
            ],
            "unused_pack_rows": sorted(set(pack_rows) - used_pack_rows),
        },
    }


def render_markdown(catalog: dict[str, Any]) -> str:
    counts = catalog["counts"]
    checks = catalog["checks"]
    lines = [
        "# NTE 角色数据源清单",
        "",
        f"生成日期：`{catalog['generated_at']}`；发布状态判断日期：`{catalog['as_of_date']}`。",
        "",
        "## 数量核对",
        "",
        f"- `DT_Character`：{counts['character_rows']} 条物理角色记录。",
        f"- 逻辑角色：{counts['logical_character_keys']} 个。",
        f"- 普通技能配置：{counts['normal_ability_profiles']} 条。",
        f"- 999夜技能配置：{counts['minigame_ability_profiles']} 条，不计作角色。",
        f"- `DT_PlayerPackData`：{counts['pack_rows']} 条，其中未被角色直接引用：{', '.join(checks['unused_pack_rows']) or '无'}。",
        "",
        "## 角色记录",
        "",
        "| ID | 官方数据名称 | 旧模型名称 | 分类 | 逻辑角色 | 属性包 | 技能 | 显示时间 |",
        "|---:|---|---|---|---|---|---:|---|",
    ]
    if catalog["inputs"]["my_roles_model"] is not None:
        lines.insert(
            9,
            f"- 旧 `my_roles_model.json`：{counts['legacy_model_roles']} 个，仅作覆盖率审计，不参与分类或命名。",
        )
    for item in catalog["characters"]:
        profile = item.get("ability_profile") or {}
        skill_count = profile.get("proactive_count", 0) + profile.get("passive_count", 0)
        display = {
            **item,
            "legacy_model_name": item.get("legacy_model_name") or "—",
            "prop_modify_id": item.get("prop_modify_id") or "—",
            "skill_count": skill_count if item.get("ability_profile") else "—",
            "show_time": item.get("mainland_show_time") or "—",
        }
        lines.append(
            "| {character_id} | {source_name} | {legacy_model_name} | {classification} | "
            "{logical_character_key} | {prop_modify_id} | {skill_count} | {show_time} |".format(
                **display
            )
        )

    lines.extend(
        [
            "",
            "## 999夜技能配置",
            "",
            "这些记录是技能配置版本，不是角色。标准角色查询必须默认排除。",
            "",
            "| 配置ID | 角色 | 普攻/技能/终结/QTE | 被动 | 与普通配置完全相同 | 默认使用 |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    for item in catalog["minigame_ability_profiles"]:
        profile = item.get("profile") or {}
        lines.append(
            f"| {item['profile_id']} | {item['display_name']} | "
            f"{profile.get('proactive_count', 0)} | {profile.get('passive_count', 0)} | "
            f"{item.get('identical_to_normal_profile')} | {item['included_by_default']} |"
        )

    lines.extend(
        [
            "",
            "## 待处理检查",
            "",
            f"- 旧项目模型中未匹配角色：{', '.join(checks['unmatched_legacy_model_roles']) or '无'}。",
            f"- 有角色但无普通技能配置：{', '.join(checks['character_ids_without_normal_ability_profile']) or '无'}。",
            f"- 有普通技能配置但无角色：{', '.join(checks['normal_ability_ids_without_character_row']) or '无'}。",
        f"- 未分类的特殊技能配置：{', '.join(checks['unclassified_special_ability_profiles']) or '无'}。",
            "- 属性包大小写不一致："
            + (
                ", ".join(
                    f"{item['character_id']}:{item['reference']}→{item['matched_row']}"
                    for item in checks["pack_row_case_mismatches"]
                )
                or "无"
            )
            + "。",
            "",
            "## 分类说明",
            "",
            "- `available_character`：来自游戏角色表，且当前没有未来显示时间限制。",
            "- `available_avatar_variant`：同一逻辑主角的不同可选形象。",
            "- `combat_transformation`：技能产生的战斗形态，不是独立角色。",
            "- `scheduled_character`：游戏官方数据中存在，但显示时间晚于报告判断日期。",
            "- `minigame_variant`：999夜玩法专用技能配置，保留但默认排除。",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(catalog: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "character_catalog.json"
    markdown_path = output_dir / "character_catalog.md"
    json_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(catalog), encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="整理好的游戏数据根目录或其中的 Content 目录",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="可选的旧 my_roles_model.json，仅用于覆盖率审计",
    )
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        metavar="YYYY-MM-DD",
        help="判断待发布角色时使用的日期",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = build_catalog(
        args.source,
        args.model,
        args.overrides,
        as_of=args.as_of,
    )
    json_path, markdown_path = write_reports(catalog, args.output_dir)
    print(f"角色清单 JSON：{json_path.resolve()}")
    print(f"角色清单报告：{markdown_path.resolve()}")
    print(json.dumps(catalog["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
