# 导入角色战斗 Blueprint 的稳定关系、GameplayTag 与动画时间证据。
"""Build-time importer for the curated UnrealExporter combat overlay."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    canonical_json,
    file_sha256,
)


_PLAYER_CHARACTER_RE = re.compile(r"/Player_(?P<code>\d+)_", re.IGNORECASE)
_PLAYER_ABILITY_RE = re.compile(r"/Ability_(?P<code>\d+)_", re.IGNORECASE)
_PLAYER_ANIMATION_RE = re.compile(
    r"/Characters/Player/(?P<code>\d+)[^/]*/animation/Skill/",
    re.IGNORECASE,
)
_OBJECT_NAME_RE = re.compile(r"'(?P<name>[^']+)'$")
_SEMANTIC_PROPERTY_NAMES = frozenset(
    {
        "AbilityTags",
        "ApplicationRequirement",
        "ApplicationRequirements",
        "ApplicationTagRequirements",
        "CustomApplicationModifierInfos",
        "BuffEventEffectData",
        "DurationMagnitude",
        "DurationPolicy",
        "GameplayCueTags",
        "GrantedTags",
        "HoldTimeTrigger",
        "InheritableOwnedTagsContainer",
        "Modifiers",
        "OngoingTagRequirements",
        "NextSectionName",
        "PlayMontageName",
        "Period",
        "ReleaseSkillMode",
        "RemoveGameplayEffectsWithTags",
        "StackingType",
        "StackLimitCount",
        "TargetTagsGameplayEffectComponent",
    }
)


def _asset_path_from_file(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("").as_posix()
    return f"/Game/{relative}"


def _curated_blueprint_paths(root: Path) -> list[Path]:
    """Mirror the deliberately narrow UnrealExporter combat path contract."""

    patterns = (
        "Blueprints/Character/Player/Player_*.json",
        "Blueprints/Abilities/Player/Ability_*/**/*.json",
        "Characters/Player/*/animation/[Ss]kill/**/*.json",
        "Characters/Player/009_female/RetargetAsset/**/*.json",
        "Characters/Player/046_male/animation/UnarmedSkill/**/*.json",
        "Characters/Player/051_female/animation/UnarmedSkill/**/*.json",
        "Characters/Player/070_mitsuki/animation/Jump/**/*.json",
        "Characters/Player/071_chaos/WorldTeleport/**/*.json",
        "Characters/Player/072_radio/animation/level/**/*.json",
        "Characters/Monster/Boss_06/animation/**/*.json",
        "Characters/Monster/Boss_06/Sagiri/**/*.json",
        "Characters/Monster/mon_01/animation/**/*.json",
        "Characters/Monster/mon_01/ParryTest/**/*.json",
        "Characters/Monster/boss_18/boss_18_player01/animation/**/*.json",
        "Blueprints/Abilities/Buff/Common/**/*.json",
        "Blueprints/Abilities/Buff/element/**/*.json",
        "Blueprints/Abilities/Calculation/**/*.json",
        "Blueprints/Abilities/Condition/Player/**/*.json",
        "Blueprints/Abilities/Condition/Common/**/*.json",
        "Blueprints/Abilities/Player/Common/**/*.json",
        "Blueprints/Abilities/Player/DamageShare/**/*.json",
        "Blueprints/Abilities/CoolDown/**/*.json",
        "Blueprints/Abilities/JumpSectionType/**/*.json",
        "Blueprints/Abilities/Fork/Fork_*/**/*.json",
        "Blueprints/Abilities/Monster/mon_oneiroi/**/*.json",
        "Blueprints/Abilities/Monster/boss_06/**/*.json",
        "Blueprints/Abilities/Monster/mon_01/**/*.json",
        "Blueprints/Abilities/Monster/boss_01/GE_boss_01_act01_back_BP.json",
        "Blueprints/Abilities/Monster/GE_mon_hitback.json",
        "Blueprints/Abilities/Buff/Fork/Fork_*/**/*.json",
        "Blueprints/Abilities/Buff/Equipment/Equipment_*/**/*.json",
        "Blueprints/Abilities/Buff/Share/**/*.json",
        "Blueprints/Abilities/Buff/ControlState/**/*.json",
        (
            "Blueprints/Character/NPC/NPC_Mass/NPC_TirggerShoot/ShootSkill/"
            "Buff_npc_shoot_02_percentdamage.json"
        ),
    )
    return sorted({path for pattern in patterns for path in root.glob(pattern)})


def _normalize_object_path(value: str) -> str:
    """Collapse /Game/Package.Asset or /Game/Package.export-index to package path."""

    if not value.startswith("/Game/"):
        return value
    head, separator, _tail = value.rpartition(".")
    return head if separator else value


def _object_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ObjectName")
    if not isinstance(raw, str):
        return None
    match = _OBJECT_NAME_RE.search(raw)
    if match is None:
        return None
    return match.group("name").rsplit(":", 1)[-1].removesuffix("_C")


def _object_instance_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ObjectName")
    if not isinstance(raw, str):
        return None
    match = _OBJECT_NAME_RE.search(raw)
    return match.group("name").rsplit(":", 1)[-1] if match is not None else None


def _object_paths(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ObjectPath")
    if not isinstance(raw, str) or not raw.startswith("/Game/"):
        return None
    return _normalize_object_path(raw), raw


def _soft_asset_paths(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("AssetPathName")
    if not isinstance(raw, str) or not raw.startswith("/Game/"):
        return None
    return _normalize_object_path(raw), raw


def _iter_nodes(
    value: Any,
    path: str = "$",
) -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_nodes(child, f"{path}[{index}]")


def _first_tag(value: Any) -> str | None:
    for _path, node in _iter_nodes(value):
        if isinstance(node, dict):
            tag = node.get("TagName")
            if isinstance(tag, str) and tag not in ("", "None"):
                return tag
    return None


def _relation_kind(property_path: str) -> str:
    lowered = property_path.casefold()
    if "targetgameplayeffectclasses" in lowered:
        return "target_effect"
    if "passivebuffs" in lowered:
        return "passive_buff"
    if "abilityclass" in lowered or "grantedabilities" in lowered:
        return "ability"
    if "montage" in lowered or "animation" in lowered:
        return "animation"
    if "calculation" in lowered or "magnitude" in lowered:
        return "calculation"
    if "condition" in lowered or "requirement" in lowered:
        return "condition"
    if "buff" in lowered:
        return "buff"
    return "reference"


def _primary_export(exports: list[dict[str, Any]], asset_path: str) -> dict[str, Any]:
    for item in exports:
        if item.get("Package") == asset_path:
            return item
    return exports[0] if exports else {}


def _asset_kind(asset_path: str, asset_name: str, asset_type: str) -> str:
    lowered_path = asset_path.casefold()
    lowered_name = asset_name.casefold()
    if "/blueprints/character/player/" in lowered_path:
        return "character"
    if asset_type == "AnimMontage":
        return "montage"
    if asset_type == "AnimSequence" or "/characters/player/" in lowered_path:
        return "animation"
    if "/calculation/" in lowered_path:
        return "calculation"
    if "/condition/" in lowered_path:
        return "condition"
    if lowered_name.startswith("ga_"):
        return "ability"
    if lowered_name.startswith("ge_"):
        return "gameplay_effect"
    if lowered_name.startswith("buff_") or "/buff/" in lowered_path:
        return "buff"
    return "other"


def _character_code(asset_path: str) -> int | None:
    for pattern in (_PLAYER_CHARACTER_RE, _PLAYER_ABILITY_RE, _PLAYER_ANIMATION_RE):
        match = pattern.search(asset_path)
        if match is not None:
            return int(match.group("code"))
    return None


def _character_maps(connection: Any) -> tuple[dict[str, int], dict[int, int]]:
    by_asset: dict[str, int] = {}
    by_code: dict[int, int] = {}
    for character_id, actor_path in connection.execute(
        "SELECT character_id, actor_path FROM character WHERE actor_path IS NOT NULL"
    ):
        package = _normalize_object_path(str(actor_path))
        by_asset[package] = int(character_id)
        match = _PLAYER_CHARACTER_RE.search(package)
        if match is not None:
            by_code.setdefault(int(match.group("code")), int(character_id))
    return by_asset, by_code


def _binding_rows(
    exports: list[dict[str, Any]],
    character_id: int,
) -> Iterable[tuple[int, str, int, str | None, str, str]]:
    arrays = (
        ("GrantedAbilities", "active"),
        ("PassiveAbilities", "passive"),
        ("PassiveBuffs", "passive_buff"),
    )
    seen: set[tuple[str, int]] = set()
    for _path, node in _iter_nodes(exports):
        if not isinstance(node, dict):
            continue
        for property_name, binding_kind in arrays:
            values = node.get(property_name)
            if not isinstance(values, list):
                continue
            for ordinal, raw in enumerate(values):
                key = (binding_kind, ordinal)
                if key in seen:
                    continue
                if property_name == "PassiveBuffs":
                    reference = raw
                    input_id = None
                elif isinstance(raw, dict):
                    reference = raw.get("AbilityClass")
                    input_id = raw.get("InputID")
                else:
                    continue
                paths = _object_paths(reference)
                ability_id = _object_id(reference)
                if paths is None or ability_id is None:
                    continue
                seen.add(key)
                yield (
                    character_id,
                    binding_kind,
                    ordinal,
                    str(input_id) if isinstance(input_id, str) else None,
                    ability_id,
                    paths[0],
                )


def _montage_bindings(
    exports: list[dict[str, Any]],
) -> Iterable[tuple[int, str, str, str]]:
    ordinal = 0
    for _path, node in _iter_nodes(exports):
        if not isinstance(node, dict) or not isinstance(node.get("MontageToPlays"), list):
            continue
        for raw in node["MontageToPlays"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("Key"), str):
                continue
            paths = _soft_asset_paths(raw.get("Value"))
            if paths is None:
                continue
            yield ordinal, raw["Key"], paths[0], paths[1]
            ordinal += 1


def _effect_bindings(
    exports: list[dict[str, Any]],
) -> Iterable[tuple[str, int, str, str, str | None]]:
    ordinals: dict[str, int] = {}
    for _path, node in _iter_nodes(exports):
        if not isinstance(node, dict):
            continue
        key = node.get("Key")
        value = node.get("Value")
        event_tag = key.get("TagName") if isinstance(key, dict) else None
        if not isinstance(event_tag, str) or not isinstance(value, dict):
            continue
        effects = value.get("TargetGameplayEffectClasses")
        if not isinstance(effects, list):
            continue
        target_paths = _object_paths(value.get("TargetType"))
        target_path = target_paths[0] if target_paths is not None else None
        for effect in effects:
            paths = _object_paths(effect)
            effect_id = _object_id(effect)
            if paths is not None and effect_id is not None:
                ordinal = ordinals.get(event_tag, 0)
                ordinals[event_tag] = ordinal + 1
                yield event_tag, ordinal, paths[0], effect_id, target_path


def _montage_rows(
    exports: list[dict[str, Any]],
) -> tuple[
    tuple[float, float | None, float | None, int | None, int | None] | None,
    list[tuple[int, str, str | None, float, float, str | None]],
    list[tuple[int, str, str | None, float, float, str | None, int | None]],
]:
    montage = next((item for item in exports if item.get("Type") == "AnimMontage"), None)
    if montage is None or not isinstance(montage.get("Properties"), dict):
        return None, [], []
    properties = montage["Properties"]
    sections = properties.get("CompositeSections", [])
    notifies = properties.get("Notifies", [])
    section_starts = [
        float(item.get("LinkValue", 0.0))
        for item in sections
        if isinstance(item, dict)
    ]
    duration = 0.0
    for item in sections:
        if isinstance(item, dict):
            duration = max(
                duration,
                float(item.get("LinkValue", 0.0))
                + float(item.get("SegmentLength", 0.0)),
            )
    for item in notifies:
        if isinstance(item, dict):
            duration = max(
                duration,
                float(item.get("LinkValue", 0.0)) + float(item.get("duration", 0.0)),
            )
    blend_in = properties.get("BlendIn")
    blend_out = properties.get("BlendOut")
    frame_rate = properties.get("CommonTargetFrameRate")
    header = (
        duration,
        float(blend_in["BlendTime"])
        if isinstance(blend_in, dict) and isinstance(blend_in.get("BlendTime"), (int, float))
        else None,
        float(blend_out["BlendTime"])
        if isinstance(blend_out, dict) and isinstance(blend_out.get("BlendTime"), (int, float))
        else None,
        int(frame_rate["Numerator"])
        if isinstance(frame_rate, dict) and isinstance(frame_rate.get("Numerator"), int)
        else None,
        int(frame_rate["Denominator"])
        if isinstance(frame_rate, dict) and isinstance(frame_rate.get("Denominator"), int)
        else None,
    )
    section_rows = []
    for ordinal, item in enumerate(sections):
        if not isinstance(item, dict):
            continue
        start = float(item.get("LinkValue", 0.0))
        next_start = section_starts[ordinal + 1] if ordinal + 1 < len(section_starts) else duration
        linked = _object_paths(item.get("LinkedSequence"))
        section_rows.append(
            (
                ordinal,
                str(item.get("SectionName") or f"section_{ordinal}"),
                str(item["NextSectionName"])
                if item.get("NextSectionName") not in (None, "None")
                else None,
                start,
                max(start, next_start),
                linked[0] if linked is not None else None,
            )
        )
    exports_by_name = {
        str(item.get("Name")): item
        for item in exports
        if isinstance(item.get("Name"), str)
    }
    notify_rows = []
    for ordinal, item in enumerate(notifies):
        if not isinstance(item, dict):
            continue
        reference = item.get("NotifyStateClass") or item.get("Notify")
        paths = _object_paths(reference)
        instance = _object_instance_name(reference)
        detail = exports_by_name.get(instance or "")
        start = float(item.get("LinkValue", 0.0))
        end = start + float(item.get("duration", 0.0))
        notify_rows.append(
            (
                ordinal,
                str(item.get("NotifyName") or "unknown"),
                paths[1] if paths is not None else None,
                start,
                max(start, end),
                _first_tag(detail) or _first_tag(item),
                int(item["TrackIndex"]) if isinstance(item.get("TrackIndex"), int) else None,
            )
        )
    return header, section_rows, notify_rows


class BlueprintImportMixin:
    combat_blueprint_root: Path | None

    def _import_combat_blueprints(self) -> None:
        root = self.combat_blueprint_root
        if root is None:
            return
        if not root.is_dir():
            raise StaticDatabaseError(f"缺少战斗 Blueprint Content 目录：{root}")
        paths = _curated_blueprint_paths(root)
        if not paths:
            return
        # References may legitimately target one of the base export scopes (for
        # example DataTable assets) even though only the curated combat subset is
        # persisted as combat_blueprint_asset rows.  Index every exported JSON
        # package for availability without importing its payload globally.
        available_source_paths = {
            _asset_path_from_file(root, path).casefold() for path in root.rglob("*.json")
        }
        by_actor, by_code = _character_maps(self.connection)
        next_source_file_id = int(
            self.connection.execute("SELECT COALESCE(MAX(source_file_id), 0) FROM source_file").fetchone()[0]
        )
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StaticDatabaseError(f"无法读取战斗 Blueprint JSON：{path}") from exc
            if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
                raise StaticDatabaseError(f"战斗 Blueprint JSON 顶层必须是对象数组：{path}")
            exports: list[dict[str, Any]] = raw
            asset_path = _asset_path_from_file(root, path)
            primary = _primary_export(exports, asset_path)
            asset_name = path.stem
            asset_type = str(primary.get("Type") or "Unknown")
            asset_kind = _asset_kind(asset_path, asset_name, asset_type)
            character_id = by_actor.get(asset_path)
            if character_id is None and (code := _character_code(asset_path)) is not None:
                character_id = by_code.get(code)
            next_source_file_id += 1
            relative_path = f"combat_blueprint/{path.relative_to(root).as_posix()}"
            self.connection.execute(
                "INSERT INTO source_file VALUES (?,?,?,?)",
                (next_source_file_id, relative_path, file_sha256(path), len(exports)),
            )
            self.connection.execute(
                "INSERT INTO combat_blueprint_asset VALUES (?,?,?,?,?,?)",
                (
                    asset_path,
                    asset_name,
                    asset_type,
                    asset_kind,
                    character_id,
                    next_source_file_id,
                ),
            )
            if character_id is not None and asset_path in by_actor:
                self.connection.executemany(
                    "INSERT INTO character_combat_ability_binding VALUES (?,?,?,?,?,?)",
                    _binding_rows(exports, character_id),
                )
            reference_rows = []
            tag_rows = []
            property_rows = []
            # Montage/AnimSequence dependency graphs are dominated by skeleton,
            # curve and visual-object references. Their action evidence is
            # represented by the dedicated montage/section/notify tables below.
            if asset_kind not in {"montage", "animation"}:
                for property_path, node in _iter_nodes(exports):
                    if not isinstance(node, dict):
                        continue
                    object_paths = _object_paths(node)
                    if object_paths is not None:
                        reference_rows.append(
                            (
                                asset_path,
                                property_path,
                                0,
                                _relation_kind(property_path),
                                object_paths[0],
                                object_paths[1],
                                str(node["ObjectName"])
                                if isinstance(node.get("ObjectName"), str)
                                else None,
                                int(object_paths[0].casefold() in available_source_paths),
                            )
                        )
                    tag = node.get("TagName")
                    if isinstance(tag, str) and tag not in ("", "None"):
                        tag_rows.append((asset_path, property_path, 0, tag))
                    for property_name in _SEMANTIC_PROPERTY_NAMES.intersection(node):
                        value_path = f"{property_path}.{property_name}"
                        property_rows.append(
                            (
                                asset_path,
                                value_path,
                                0,
                                property_name,
                                canonical_json(node[property_name]),
                            )
                        )
            self.connection.executemany(
                "INSERT INTO combat_blueprint_reference VALUES (?,?,?,?,?,?,?,?)",
                reference_rows,
            )
            self.connection.executemany(
                "INSERT INTO combat_blueprint_tag VALUES (?,?,?,?)",
                tag_rows,
            )
            self.connection.executemany(
                "INSERT INTO combat_blueprint_semantic_property VALUES (?,?,?,?,?)",
                property_rows,
            )
            if asset_kind == "ability":
                self.connection.executemany(
                    "INSERT INTO combat_ability_montage_binding VALUES (?,?,?,?,?)",
                    (
                        (asset_path, ordinal, key, target, object_path)
                        for ordinal, key, target, object_path in _montage_bindings(exports)
                    ),
                )
                self.connection.executemany(
                    "INSERT INTO combat_ability_effect_binding VALUES (?,?,?,?,?,?)",
                    (
                        (asset_path, event_tag, ordinal, effect_path, effect_id, target_path)
                        for event_tag, ordinal, effect_path, effect_id, target_path
                        in _effect_bindings(exports)
                    ),
                )
            montage, sections, notifies = _montage_rows(exports)
            if montage is not None:
                self.connection.execute(
                    "INSERT INTO combat_montage VALUES (?,?,?,?,?,?)",
                    (asset_path, *montage),
                )
                self.connection.executemany(
                    "INSERT INTO combat_montage_section VALUES (?,?,?,?,?,?,?)",
                    ((asset_path, *row) for row in sections),
                )
                self.connection.executemany(
                    "INSERT INTO combat_montage_notify VALUES (?,?,?,?,?,?,?,?)",
                    ((asset_path, *row) for row in notifies),
                )
