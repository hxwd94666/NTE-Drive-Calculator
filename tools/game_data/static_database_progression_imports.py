"""Import v30 release annotations and conservative progression-drop facts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from src.domain.static_character_release_annotations import (
    CHARACTER_RELEASE_SEEDS,
    RELEASE_EVIDENCE,
)
from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    asset_path,
    canonical_json,
    enum_tail,
    optional_text,
    resolved_text_parts,
)


_COST_TOKEN_ALIASES = {
    ("gold", "progression_cost"): "Fons",
}

_LIMITED_LOTTERY_SOURCES = (
    "lottery_nanali",
    "lottery_xun",
    "lottery_anhunqu",
    "lottery_kaesi",
    "lottery_zhenhong",
    "lottery_yiluoyi",
    "lottery_canhong",
    "lottery_lingke",
)

_DAMAGE_RESISTANCE_TERMS = {
    "normal": ("DamageResistNormalBase", None),
    "chaos": ("DamageResistChaos", "DamageResistChaos"),
    "cosmos": ("DamageResistCosmos", "DamageResistCosmos"),
    "incantation": ("DamageResistIncantation", "DamageResistIncantation"),
    "lakshana": ("DamageResistLakshana", "DamageResistLakshana"),
    "nature": ("DamageResistNature", "DamageResistNature"),
    "psyche": ("DamageResistPsyche", "DamageResistPsyche"),
    "psychically": ("DamageResistPsychically", "DamageResistPsychically"),
}

_OUTER_REALM_STAGE_TERMS = {
    "EAbyssFightStage::FirstHalf": "上半场",
    "EAbyssFightStage::SecondHalf": "下半场",
}


@dataclass(frozen=True, slots=True)
class _ResolvedSequence:
    item_id: str | None
    quantity: int | None
    reason_code: str | None


def _source_kind(evidence_keys: tuple[str, ...]) -> str:
    return (
        "official"
        if evidence_keys and all(
            RELEASE_EVIDENCE[key][0] == "official" for key in evidence_keys
        )
        else "reviewed_fallback"
    )


def _parse_cost_string(value: Any) -> list[tuple[str, int]]:
    text = str(value or "").strip()
    if not text or text == "0":
        return []
    parsed: list[tuple[str, int]] = []
    for token in text.split(","):
        item_id, separator, raw_quantity = token.partition(":")
        item_id = item_id.strip()
        if not separator or not item_id:
            raise StaticDatabaseError(f"养成消耗格式无效：{token!r}")
        try:
            quantity = int(raw_quantity)
        except ValueError as exc:
            raise StaticDatabaseError(f"养成消耗数量无效：{token!r}") from exc
        if quantity <= 0:
            raise StaticDatabaseError(f"养成消耗数量必须为正整数：{token!r}")
        parsed.append((item_id, quantity))
    return parsed


def _numbered_family(rows: dict[str, Any], base_id: str) -> list[tuple[str, Any]]:
    prefix = f"{base_id}_"
    members = []
    for row_key, row in rows.items():
        suffix = str(row_key).removeprefix(prefix)
        if str(row_key).startswith(prefix) and suffix.isdigit():
            members.append((str(row_key), row))
    return sorted(members, key=lambda item: int(item[0].rsplit("_", 1)[1]))


class _ProgressionImportContext(Protocol):
    connection: sqlite3.Connection
    rows: dict[str, dict[str, Any]]

    def source_row_id(self, table: str, row_key: str) -> int: ...


class ProgressionImportMixin(_ProgressionImportContext):
    def _import_progression_catalog(self) -> None:
        self._import_character_release_annotations()
        drop_results = self._resolve_clone_drop_groups()
        referenced_items = self._collect_progression_item_ids(drop_results)
        missing_names = self._import_progression_items(referenced_items)
        self._import_progression_aliases()
        self._import_item_quality_terms()
        self._import_character_acquisition_terms()
        self._import_fork_lottery_campaigns()
        self._import_damage_resistance_terms()
        self._import_outer_realm_stage_terms()
        self._import_clone_drop_projections(drop_results, missing_names)

    def _import_character_release_annotations(self) -> None:
        for evidence_key, (source_kind, locator) in RELEASE_EVIDENCE.items():
            self.connection.execute(
                "INSERT INTO character_release_evidence VALUES (?,?,?)",
                (evidence_key, source_kind, locator),
            )
        memberships = self._import_character_acquisition_memberships()
        official_rows = {
            int(row[0]): (row[1], int(row[2]))
            for row in self.connection.execute(
                "SELECT character_id, mainland_show_time, source_row_id FROM character"
            )
        }
        for character_id, seed in sorted(CHARACTER_RELEASE_SEEDS.items()):
            official = official_rows.get(character_id)
            if official is None:
                continue
            official_show_time, official_source_row_id = official
            official_date = str(official_show_time or "").strip()[:10] or None
            release_date = official_date or seed.release_date
            release_kind = (
                "official" if official_date else _source_kind(seed.release_evidence_keys)
            )
            self.connection.execute(
                """
                INSERT INTO character_release_annotation VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    character_id,
                    seed.quality,
                    _source_kind(seed.quality_evidence_keys),
                    seed.acquisition_type,
                    (
                        "official"
                        if (character_id, seed.acquisition_type) in memberships
                        and seed.acquisition_type != "free"
                        else "reviewed_fallback"
                    ),
                    release_date,
                    release_kind,
                    official_source_row_id if official_date else None,
                ),
            )
            field_evidence = {
                "quality": seed.quality_evidence_keys,
                "acquisition_type": seed.acquisition_evidence_keys,
                "mainland_release_date": (
                    () if official_date else seed.release_evidence_keys
                ),
            }
            for field_name, evidence_keys in field_evidence.items():
                for ordinal, evidence_key in enumerate(evidence_keys):
                    self.connection.execute(
                        "INSERT INTO character_release_evidence_link VALUES (?,?,?,?)",
                        (character_id, field_name, ordinal, evidence_key),
                    )

    def _import_character_acquisition_memberships(self) -> set[tuple[int, str]]:
        official_rows = {
            int(row[0]) for row in self.connection.execute("SELECT character_id FROM character")
        }
        memberships: set[tuple[int, str]] = set()
        permanent = self.rows["lottery_permanent"].get("Properties")
        if not isinstance(permanent, dict):
            raise StaticDatabaseError("常驻角色 Lottery DataAsset 缺少 Properties")
        for raw_character_id in permanent.get("CharacterIDs") or ():
            character_id = int(raw_character_id)
            if character_id not in official_rows:
                raise StaticDatabaseError(f"常驻卡池引用未知角色：{character_id}")
            self.connection.execute(
                "INSERT INTO character_acquisition_membership VALUES (?,?,?,?,?,?)",
                (
                    character_id,
                    "permanent",
                    "formal_game_data",
                    self.source_row_id("lottery_permanent", "Properties"),
                    None,
                    None,
                ),
            )
            memberships.add((character_id, "permanent"))

        for source_name in _LIMITED_LOTTERY_SOURCES:
            properties = self.rows[source_name].get("Properties")
            if not isinstance(properties, dict):
                raise StaticDatabaseError(f"限定角色 Lottery DataAsset 缺少 Properties：{source_name}")
            character_ids = properties.get("CharacterIDs") or ()
            drops = properties.get("ModuleDropDatas") or ()
            if (
                properties.get("bActivityPool") is not True
                or not str(properties.get("PoolTypeID") or "").endswith("CardPool_Character")
                or not character_ids
                or not drops
            ):
                raise StaticDatabaseError(f"限定角色卡池结构无效：{source_name}")
            featured_id = int(character_ids[0])
            if featured_id not in official_rows:
                raise StaticDatabaseError(f"限定卡池引用未知角色：{featured_id}")
            first_drop = drops[0]
            drop_id = optional_text(first_drop.get("DropID")) if isinstance(first_drop, dict) else None
            group_key = f"{drop_id}_0" if drop_id else ""
            group = self.rows["drop_groups"].get(group_key)
            if (
                not isinstance(first_drop, dict)
                or not str(first_drop.get("CellClass") or "").endswith("EMBCT_Character")
                or not isinstance(group, dict)
                or group.get("SequenceId") != f"droplist_{featured_id}"
            ):
                raise StaticDatabaseError(f"限定卡池主推角色掉落关系不一致：{source_name}")
            self.connection.execute(
                "INSERT INTO character_acquisition_membership VALUES (?,?,?,?,?,?)",
                (
                    featured_id,
                    "limited",
                    "formal_game_data",
                    self.source_row_id(source_name, "Properties"),
                    self.source_row_id("drop_groups", group_key),
                    None,
                ),
            )
            memberships.add((featured_id, "limited"))

        for character_id, seed in sorted(CHARACTER_RELEASE_SEEDS.items()):
            if seed.acquisition_type != "free" or character_id not in official_rows:
                continue
            evidence_key = seed.acquisition_evidence_keys[0]
            self.connection.execute(
                "INSERT INTO character_acquisition_membership VALUES (?,?,?,?,?,?)",
                (
                    character_id,
                    "free",
                    "reviewed_annotation",
                    None,
                    None,
                    evidence_key,
                ),
            )
            memberships.add((character_id, "free"))
        return memberships

    @staticmethod
    def _canonical_item_id(token: str, context: str) -> str:
        return _COST_TOKEN_ALIASES.get((token, context), token)

    def _resolve_sequence(self, sequence_id: str, multiplier: int) -> _ResolvedSequence:
        members = _numbered_family(self.rows["drop_sequences"], sequence_id)
        if not members:
            token = sequence_id.removeprefix("droplist_")
            if not token or token == sequence_id:
                return _ResolvedSequence(None, None, "sequence_missing")
            return _ResolvedSequence(
                self._canonical_item_id(token, "progression_cost"),
                multiplier,
                None,
            )
        branches: list[tuple[str, int]] = []
        for _row_key, row in members:
            if not isinstance(row, dict):
                return _ResolvedSequence(None, None, "sequence_shape_invalid")
            number = row.get("Num")
            fixed = str(row.get("SequenceNumType") or "").endswith("_FIXED")
            if (
                not fixed
                or isinstance(number, bool)
                or not isinstance(number, int)
                or number <= 0
                or row.get("SequenceProbability") not in (None, [])
                or row.get("MinNum") not in (None, 0)
                or row.get("MaxNum") not in (None, 0)
                or str(row.get("Formula") or "").strip()
                or row.get("Weight") not in (1, 1.0)
                or str(row.get("LimitLevel") or "").strip()
            ):
                return _ResolvedSequence(None, None, "sequence_not_deterministic")
            item_id = optional_text(row.get("ItemID"))
            if item_id is None:
                return _ResolvedSequence(None, None, "sequence_item_missing")
            branches.append((
                self._canonical_item_id(item_id, "progression_cost"),
                number * multiplier,
            ))
        if len(set(branches)) != 1:
            return _ResolvedSequence(None, None, "sequence_branch_divergent")
        return _ResolvedSequence(*branches[0], None)

    def _resolve_clone_drop_groups(
        self,
    ) -> dict[str, tuple[Counter[str], list[tuple[str | None, str, int | None]]]]:
        drop_ids = {
            str(row[0])
            for row in self.connection.execute(
                """SELECT DISTINCT drop_id FROM clone_activity_difficulty
                   WHERE drop_id IS NOT NULL AND trim(drop_id) <> ''"""
            )
        }
        results: dict[
            str,
            tuple[Counter[str], list[tuple[str | None, str, int | None]]],
        ] = {}
        for drop_id in sorted(drop_ids):
            outputs: Counter[str] = Counter()
            gaps: list[tuple[str | None, str, int | None]] = []
            group_rows = _numbered_family(self.rows["drop_groups"], drop_id)
            if not group_rows:
                results[drop_id] = (outputs, [(None, "drop_group_missing", None)])
                continue
            for row_key, row in group_rows:
                source_row_id = self.source_row_id("drop_groups", row_key)
                if not isinstance(row, dict):
                    gaps.append((None, "drop_group_shape_invalid", source_row_id))
                    continue
                sequence_id = optional_text(row.get("SequenceId"))
                multiplier = row.get("ModifyNum")
                if (
                    sequence_id is None
                    or isinstance(multiplier, bool)
                    or not isinstance(multiplier, int)
                    or multiplier <= 0
                    or row.get("SequenceWeight") not in (1, 1.0)
                    or row.get("DropConditions") not in (None, [])
                ):
                    gaps.append((sequence_id, "drop_group_not_deterministic", source_row_id))
                    continue
                resolved = self._resolve_sequence(sequence_id, multiplier)
                if resolved.reason_code:
                    gaps.append((sequence_id, resolved.reason_code, source_row_id))
                else:
                    assert resolved.item_id is not None and resolved.quantity is not None
                    outputs[resolved.item_id] += resolved.quantity
            results[drop_id] = (outputs, gaps)
        return results

    def _collect_progression_item_ids(
        self,
        drop_results: dict[str, tuple[Counter[str], list[tuple[str | None, str, int | None]]]],
    ) -> set[str]:
        item_ids = {"Fons", "Gold"}
        item_ids.update(
            item_id
            for outputs, _gaps in drop_results.values()
            for item_id in outputs
        )
        for character in self.rows["character_abilities"].values():
            for ability in (character.get("CharacterAbilityList") or ()):
                value = ability.get("Value") if isinstance(ability, dict) else None
                for level in (value.get("LevelsCostItems") or ()) if isinstance(value, dict) else ():
                    for cost in (level.get("CostItems") or ()) if isinstance(level, dict) else ():
                        token = optional_text(cost.get("ID")) if isinstance(cost, dict) else None
                        if token:
                            item_ids.add(self._canonical_item_id(token, "progression_cost"))
        cost_tables = (
            ("character_breakthroughs", ("NeedItems", "NeedGolds")),
            ("fork_breakthroughs", ("NeedItems", "NeedGolds")),
            ("fork_stars", ("NeedGolds",)),
        )
        for table, fields in cost_tables:
            for row in self.rows[table].values():
                if not isinstance(row, dict):
                    continue
                for field in fields:
                    for token, _quantity in _parse_cost_string(row.get(field)):
                        item_ids.add(
                            self._canonical_item_id(token, "progression_cost")
                        )
        return item_ids

    def _import_progression_items(self, item_ids: set[str]) -> set[str]:
        catalogs = {
            **{item_id: ("item_catalog", row) for item_id, row in self.rows["item_catalog"].items()},
            **{
                item_id: ("capital_item_catalog", row)
                for item_id, row in self.rows["capital_item_catalog"].items()
            },
        }
        missing_names: set[str] = set()
        for item_id in sorted(item_ids):
            catalog = catalogs.get(item_id)
            if catalog is None:
                missing_names.add(item_id)
                self.connection.execute(
                    "INSERT INTO progression_item VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        "{}",
                        "name_missing",
                        None,
                        None,
                        None,
                        None,
                        None,
                        "referenced_missing",
                        None,
                    ),
                )
                self._insert_localized_term(
                    "item",
                    item_id,
                    source_kind="name_missing",
                    display_name=None,
                )
                continue
            table, row = catalog
            name_zh, name_table, name_key = resolved_text_parts(
                self.rows, row.get("ItemName")
            )
            if not name_zh:
                missing_names.add(item_id)
            self.connection.execute(
                "INSERT INTO progression_item VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    canonical_json({"zh-CN": name_zh} if name_zh else {}),
                    "complete" if name_zh else "name_missing",
                    name_zh,
                    name_table,
                    name_key,
                    enum_tail(row.get("ItemQuality"), "ITEM_QUALITY_"),
                    asset_path(row.get("ItemIcon")),
                    "official_item_catalog",
                    self.source_row_id(table, item_id),
                ),
            )
            self._insert_localized_term(
                "item",
                item_id,
                source_kind=(
                    "formal_localization" if name_zh else "name_missing"
                ),
                display_name=name_zh,
                text_table=name_table if name_zh else None,
                text_key=name_key if name_zh else None,
                source_row_id=self.source_row_id(table, item_id) if name_zh else None,
            )
        return missing_names

    def _import_progression_aliases(self) -> None:
        for (token, context), item_id in sorted(_COST_TOKEN_ALIASES.items()):
            self.connection.execute(
                "INSERT INTO progression_item_alias VALUES (?,?,?,?)",
                (token, context, item_id, "product_contract"),
            )

    def _import_item_quality_terms(self) -> None:
        for row_key, row in sorted(self.rows["item_qualities"].items()):
            quality_id = enum_tail(row_key, "ITEM_QUALITY_")
            grade_zh, grade_table, grade_key = resolved_text_parts(
                self.rows, row.get("QualityText")
            )
            color_zh, color_table, color_key = resolved_text_parts(
                self.rows, row.get("QualityDesc")
            )
            if not quality_id or not grade_zh or not color_zh:
                raise StaticDatabaseError(f"物品品质术语不完整：{row_key}")
            self.connection.execute(
                "INSERT INTO item_quality_term VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    quality_id,
                    canonical_json({"zh-CN": grade_zh}),
                    grade_zh,
                    grade_table,
                    grade_key,
                    canonical_json({"zh-CN": color_zh}),
                    color_zh,
                    color_table,
                    color_key,
                    self.source_row_id("item_qualities", row_key),
                ),
            )
            self._insert_localized_term(
                "item_quality",
                quality_id,
                source_kind="formal_localization",
                display_name=grade_zh,
                text_table=grade_table,
                text_key=grade_key,
                source_row_id=self.source_row_id("item_qualities", row_key),
            )
            self._insert_localized_term(
                "item_quality_color",
                quality_id,
                source_kind="formal_localization",
                display_name=color_zh,
                text_table=color_table,
                text_key=color_key,
                source_row_id=self.source_row_id("item_qualities", row_key),
            )

    def _insert_localized_term(
        self,
        entity_kind: str,
        canonical_id: str,
        *,
        source_kind: str,
        display_name: str | None,
        text_table: str | None = None,
        text_key: str | None = None,
        source_row_id: int | None = None,
        locale: str = "zh-CN",
    ) -> None:
        self.connection.execute(
            "INSERT INTO localized_term VALUES (?,?,?,?,?,?)",
            (
                entity_kind,
                canonical_id,
                source_kind,
                text_table,
                text_key,
                source_row_id,
            ),
        )
        if display_name:
            self.connection.execute(
                "INSERT INTO localized_term_name VALUES (?,?,?,?)",
                (entity_kind, canonical_id, locale, display_name),
            )

    def _import_character_acquisition_terms(self) -> None:
        formal_terms = (
            ("permanent", "string_ui_j", "MainActivity_02", "/Game/Text/ST_UI_J.ST_UI_J"),
            ("limited", "string_ui", "LimitEditionTag", "/Game/Text/ST_Ui.ST_Ui"),
        )
        for canonical_id, source_name, text_key, text_table in formal_terms:
            display_name = optional_text(self.rows[source_name].get(text_key))
            if display_name is None:
                raise StaticDatabaseError(f"角色获取类型缺少正式名称：{canonical_id}")
            self._insert_localized_term(
                "character_acquisition_type",
                canonical_id,
                source_kind="formal_localization",
                display_name=display_name,
                text_table=text_table,
                text_key=text_key,
                source_row_id=self.source_row_id(source_name, text_key),
            )
        self._insert_localized_term(
            "character_acquisition_type",
            "free",
            source_kind="reviewed_annotation",
            display_name="免费获取",
        )

    def _import_fork_lottery_campaigns(self) -> None:
        configured_pool_ids = tuple(
            str(entry.get("Value"))
            for entry in self.rows["fork_lottery_data"]["1"].get("PoolIDMap", ())
            if isinstance(entry, dict) and optional_text(entry.get("Value"))
        )
        if len(configured_pool_ids) != 8 or len(set(configured_pool_ids)) != 8:
            raise StaticDatabaseError("弧盘限定卡池配置必须恰好包含 8 个 pool")
        for release_ordinal, pool_id in enumerate(configured_pool_ids):
            row = self.rows["fork_lottery_pools"].get(pool_id)
            if not isinstance(row, dict):
                raise StaticDatabaseError(f"弧盘限定卡池缺少定义：{pool_id}")
            up_list = row.get("UpList")
            if (
                not isinstance(up_list, list)
                or len(up_list) != 1
                or not optional_text(up_list[0])
            ):
                raise StaticDatabaseError(f"弧盘限定卡池主推关系无效：{pool_id}")
            featured_fork_id = str(up_list[0])
            if self.connection.execute(
                "SELECT 1 FROM fork_item WHERE fork_id = ?", (featured_fork_id,)
            ).fetchone() is None:
                raise StaticDatabaseError(f"弧盘限定卡池引用未知弧盘：{featured_fork_id}")
            title, text_table, text_key = resolved_text_parts(
                self.rows, row.get("ShowText1")
            )
            if not title or not text_table or not text_key:
                raise StaticDatabaseError(f"弧盘限定卡池标题不完整：{pool_id}")
            source_row_id = self.source_row_id("fork_lottery_pools", pool_id)
            self.connection.execute(
                "INSERT INTO fork_lottery_campaign VALUES (?,?,?,?,?,?)",
                (
                    pool_id,
                    featured_fork_id,
                    release_ordinal,
                    text_table,
                    text_key,
                    source_row_id,
                ),
            )
            self._insert_localized_term(
                "fork_campaign",
                pool_id,
                source_kind="formal_localization",
                display_name=title,
                text_table=text_table,
                text_key=text_key,
                source_row_id=source_row_id,
            )

    def _import_damage_resistance_terms(self) -> None:
        for resistance_id, (attribute_id, text_key) in _DAMAGE_RESISTANCE_TERMS.items():
            if text_key is None:
                self.connection.execute(
                    "INSERT INTO damage_resistance_term VALUES (?,?,NULL)",
                    (resistance_id, attribute_id),
                )
                self._insert_localized_term(
                    "damage_resistance",
                    resistance_id,
                    source_kind="name_missing",
                    display_name=None,
                )
                continue
            display_name = optional_text(self.rows["string_common"].get(text_key))
            if display_name is None:
                raise StaticDatabaseError(f"伤害抗性缺少正式名称：{resistance_id}")
            source_row_id = self.source_row_id("string_common", text_key)
            self.connection.execute(
                "INSERT INTO damage_resistance_term VALUES (?,?,?)",
                (resistance_id, attribute_id, source_row_id),
            )
            self._insert_localized_term(
                "damage_resistance",
                resistance_id,
                source_kind="formal_localization",
                display_name=display_name,
                text_table="/Game/Text/ST_Common.ST_Common",
                text_key=text_key,
                source_row_id=source_row_id,
            )

    def _import_outer_realm_stage_terms(self) -> None:
        official_ids = {
            str(entry.get("Key"))
            for row in self.rows["abyss_clone_levels"].values()
            if isinstance(row, dict)
            for level in row.get("LevelConfigArray", ())
            if isinstance(level, dict)
            for entry in level.get("SpawnMonsterConfigMap", ())
            if isinstance(entry, dict) and optional_text(entry.get("Key"))
        }
        missing = set(_OUTER_REALM_STAGE_TERMS) - official_ids
        if missing:
            raise StaticDatabaseError(f"轨外上下半场正式枚举缺失：{sorted(missing)}")
        for canonical_id, display_name in _OUTER_REALM_STAGE_TERMS.items():
            self._insert_localized_term(
                "outer_realm_fight_stage",
                canonical_id,
                source_kind="ui_state",
                display_name=display_name,
            )

    def _import_clone_drop_projections(
        self,
        results: dict[str, tuple[Counter[str], list[tuple[str | None, str, int | None]]]],
        missing_names: set[str],
    ) -> None:
        for drop_id, (outputs, initial_gaps) in sorted(results.items()):
            gaps = list(initial_gaps)
            for item_id in sorted(outputs):
                if item_id in missing_names:
                    gaps.append((None, "name_missing", None))
            status = "complete" if outputs and not gaps else "partial" if outputs else "unavailable"
            reason = None if status == "complete" else gaps[0][1]
            self.connection.execute(
                "INSERT INTO clone_drop_projection VALUES (?,?,?,?)",
                (drop_id, status, "official_drop_closure", reason),
            )
            for item_id, quantity in sorted(outputs.items()):
                self.connection.execute(
                    "INSERT INTO clone_drop_projection_item VALUES (?,?,?)",
                    (drop_id, item_id, quantity),
                )
            for ordinal, (sequence_id, reason_code, source_row_id) in enumerate(gaps):
                self.connection.execute(
                    "INSERT INTO clone_drop_projection_gap VALUES (?,?,?,?,?)",
                    (drop_id, ordinal, sequence_id, reason_code, source_row_id),
                )
