# 将旧求解器所需的兼容结构完全投影自官方 SQLite。
"""Static SQLite adapter for the legacy allocation solver.

The solver still uses display names and puzzle matrices internally, but these
structures are derived only from official static data and account-scoped SQLite
 weight preferences.  No legacy JSON configuration is read here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.stat_catalog import StatCatalog
from src.models.equipment import DriveShape
from src.optimizer.scoring import ScoringEngine
from src.domain.official_role.advancement import fork_active_panel_stats
from src.services.allocation.inventory import legacy_shape_id
from src.services.characters.character_shape_bonus_service import get_effective_character_shape_bonus
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.integrations.static_catalog_release import static_catalog_read_unit
from src.services.characters.character_weight_service import resolve_character_base_weights
from src.storage.sqlite.user_data_dao import UserDataDao


_LEGACY_SHAPE_LABELS = {
    "H_2": "Type-2", "V_2": "Type-2",
    "H_3": "Type-3", "V_3": "Type-3",
    "L_3_BL": "Type-3", "L_3_TL": "Type-3", "L_3_TR": "Type-3", "L_3_BR": "Type-3",
    "H_4": "Type-4", "V_4": "Type-4", "Trap_4_H": "Type-4", "Trap_4_V": "Type-4",
}


@dataclass(frozen=True)
class LegacyAllocationStaticCatalog:
    roles_db: dict[str, dict[str, Any]]
    sets_db: dict[str, dict[str, Any]]
    shapes_db: dict[str, DriveShape]
    board_matrices: dict[str, list[list[int]]]
    fork_ids_by_name: dict[str, str]


def _shape_matrix(shape: dict[str, Any]) -> list[list[int]]:
    cells = list(shape.get("cells") or [])
    xs = [int(cell["x"]) for cell in cells]
    ys = [int(cell["y"]) for cell in cells]
    if not xs or not ys:
        raise ValueError(f"官方形状 {shape.get('shape_id')} 没有格位定义")
    matrix = [[0] * (max(ys) - min(ys) + 1) for _ in range(max(xs) - min(xs) + 1)]
    for cell in cells:
        matrix[int(cell["x"]) - min(xs)][int(cell["y"]) - min(ys)] = 1
    return matrix


def _custom_board_matrix(cells: list[dict[str, Any]]) -> list[list[int]]:
    board = [[-1] * 5 for _ in range(5)]
    enabled_count = 0
    for cell in cells:
        row = int(cell["row_number"]) - 1
        column = int(cell["column_number"]) - 1
        if not (0 <= row < 5 and 0 <= column < 5):
            continue
        if bool(cell["is_enabled"]):
            board[row][column] = 0
            enabled_count += 1
    if enabled_count != 20:
        raise ValueError(f"自建角色底盘应为 20 格，实际为 {enabled_count} 格")
    return board


def _custom_weight_labels(
    record: dict[str, Any], attributes: dict[str, str], field_name: str,
) -> dict[str, float]:
    return {
        attributes[property_id]: float(weight)
        for property_id, weight in (record.get(field_name) or {}).items()
        if attributes.get(str(property_id)) and float(weight) > 0
    }


def _likeability_crit_rate_bonus(
    static_dao: StaticGameDataDao,
    user_dao: UserDataDao | None,
    character_id: int,
    *, enabled_override: bool | None = None,
) -> float:
    """Return this account's enabled level-10 affinity CritBase bonus."""

    bonus = static_dao.get_character_likeability_bonus(character_id)
    if not bonus:
        return 0.0
    profile = user_dao.get_character_profile(character_id) if user_dao else None
    if enabled_override is not None:
        enabled = enabled_override
    elif profile is not None:
        enabled = bool(profile.get("likeability_level_10_enabled"))
    else:
        enabled = True
    if not enabled:
        return 0.0
    return sum(
        float(row.get("value") or 0.0)
        for row in bonus.get("properties") or ()
        if str(row.get("property_id") or "") == "CritBase"
    )


def _current_role_calculation_projection(
    detail: dict[str, Any],
) -> dict[str, Any]:
    """Project current fork state without replacing workshop base weights."""

    profile = detail.get("profile") or {}
    fork_id = str(profile.get("fork_id") or "").strip()
    fork = next(
        (
            item for item in detail.get("forks") or ()
            if str(item.get("fork_id") or "") == fork_id
        ),
        None,
    )
    if fork is None:
        # A profile can name a fork absent from the frozen static catalogue.
        # Keep that source unknown instead of silently restoring the
        # graduation-template fork. An explicit empty fork is known zero.
        return {
            "default_weapon": "",
            "default_fork_id": fork_id,
            "likeability_level_10_enabled": bool(
                profile.get("likeability_level_10_enabled")
            ),
            "active_fork_crit_rate_bonus": (
                0.0 if "fork_id" in profile and not fork_id else None
            ),
        }
    fork_stats = fork_active_panel_stats(
        fork,
        int(profile.get("fork_level") or 1),
        breakthrough_stage=profile.get("fork_breakthrough_stage"),
        refinement_level=profile.get("fork_refinement_level"),
    )
    refinement = profile.get("fork_refinement_level")
    # A reviewed zero-permanent fork has no refinement row by design.  That is
    # known zero, not missing evidence for its level/breakthrough CritBase.
    permanent_known = fork.get("permanent_review_status") in (
        "confirmed_no_permanent", "conditional_only",
    ) or (
        refinement is not None and any(
            str(row.get("refinement_level")) == str(refinement)
            for row in fork.get("permanent_properties") or ()
        )
    )
    return {
        "default_weapon": str((fork or {}).get("name_zh") or ""),
        "default_fork_id": fork_id,
        "likeability_level_10_enabled": bool(profile.get("likeability_level_10_enabled")),
        "active_fork_crit_rate_bonus": (
            round(max(0.0, float(fork_stats.get("CritBase") or 0.0)) * 100.0, 4)
            if permanent_known else None
        ),
    }


def build_legacy_allocation_static_catalog(
    *, config_dir: str | Path, user_database_path: str | Path | None = None,
    static_database_path: str | Path | None = None,
    expected_dataset: tuple[str, int, str] | None = None,
    stat_catalog: StatCatalog | None = None,
    static_read_unit=None,
) -> LegacyAllocationStaticCatalog:
    """Build all old-solver inputs from the static and account SQLite databases."""

    roles_db: dict[str, dict[str, Any]] = {}
    sets_db: dict[str, dict[str, Any]] = {}
    shapes_db: dict[str, DriveShape] = {}
    board_matrices: dict[str, list[list[int]]] = {}
    with static_catalog_read_unit(static_database_path, read_unit=static_read_unit) as read_unit:
        static_dao = read_unit.dao
        if expected_dataset is not None:
            metadata = static_dao.dataset_metadata()
            actual = (str(metadata["dataset_id"]), int(metadata["importer_version"]), str(metadata["built_at_utc"]))
            if actual != expected_dataset:
                raise ValueError("图纸读取的实际数据库与本批识别资料身份不一致。")
        # 此处只组装权重，不调用会另开静态DAO的ScoringEngine构造器。
        characters = static_dao.list_role_template_characters()
        for suit in static_dao.list_suits():
            name = str(suit.get("name_zh") or suit["suit_id"])
            sets_db[name] = {
                "suit_id": str(suit["suit_id"]),
                "shapes": [legacy_shape_id(shape_id) for shape_id in suit.get("required_shape_ids") or ()],
            }
        for shape in static_dao.list_shapes():
            legacy_id = legacy_shape_id(shape["shape_id"])
            shapes_db[legacy_id] = DriveShape(
                shape_id=legacy_id,
                label=_LEGACY_SHAPE_LABELS.get(legacy_id, f"Type-{int(shape['cell_count'])}"),
                matrix=_shape_matrix(shape),
                area=int(shape["cell_count"]),
                description=str(shape["shape_id"]),
            )
        attributes = {
            str(attribute["attribute_id"]): ScoringEngine._scoring_property_name(attribute)
            for attribute in static_dao.list_equipment_attributes()
        }
        fork_names = {
            str(fork.get("fork_id") or ""): str(fork.get("name_zh") or "")
            for fork in static_dao.list_fork_templates()
        }
        fork_ids_by_name: dict[str, str] = {}
        duplicate_fork_names: set[str] = set()
        for fork_id, name in fork_names.items():
            if not fork_id or not name:
                continue
            if name in fork_ids_by_name and fork_ids_by_name[name] != fork_id:
                duplicate_fork_names.add(name)
            else:
                fork_ids_by_name[name] = fork_id
        for name in duplicate_fork_names:
            fork_ids_by_name.pop(name, None)
        graduation_templates = {
            int(template["character_id"]): template
            for template in static_dao.list_character_graduation_templates()
        }
        user_dao = (
            UserDataDao(user_database_path)
            if user_database_path is not None and Path(user_database_path).is_file()
            else None
        )
        try:
            detail_cache: dict[object, Any] = {}
            for character in characters:
                character_id = int(character["character_id"])
                role_name = str(character.get("name_zh") or character_id)
                plan = static_dao.get_equipment_plan(character_id)
                default_suit = static_dao.get_character_default_suit(character_id)
                if plan is None or default_suit is None:
                    continue
                suit_name = str(default_suit["suit_name_zh"])
                if suit_name not in sets_db:
                    raise ValueError(f"角色 [{role_name}] 的官方默认套装不存在：{suit_name}")
                shape_bonus = get_effective_character_shape_bonus(
                    static_dao, character_id,
                ) or {}
                extra_shape_label = str(shape_bonus.get("shape_label") or "")
                extra_shape_buffs = {
                    attributes[str(row["property_id"])]: float(row["display_value"])
                    for row in shape_bonus.get("properties") or ()
                    if attributes.get(str(row["property_id"]))
                }
                weight_record = resolve_character_base_weights(user_dao, static_dao, character_id) or {}
                scoring_role = {
                    "weights": _custom_weight_labels(weight_record, attributes, "property_weights"),
                    "main_weights": _custom_weight_labels(weight_record, attributes, "main_property_weights"),
                }
                graduation_template = graduation_templates.get(character_id, {})
                default_weapon = fork_names.get(
                    str(graduation_template.get("fork_id") or ""),
                    "",
                )
                default_fork_id = str(graduation_template.get("fork_id") or "")
                calculation_projection = None
                if user_dao is not None:
                    try:
                        detail = load_official_role_detail(
                            user_database_path,
                            character_id,
                            include_inventory_contexts=False,
                            static_database_path=static_dao.database_path,
                            request_cache=detail_cache, static_read_unit=read_unit,
                        )
                        calculation_projection = (
                            _current_role_calculation_projection(detail) or None
                        )
                    except (OSError, RuntimeError, ValueError):
                        saved_profile = user_dao.get_character_profile(character_id)
                        observed_profile = user_dao.get_native_character_profile_observation(
                            character_id
                        )
                        fallback_profile = dict(saved_profile or {})
                        fork_observed = bool(
                            observed_profile
                            and observed_profile.get("fork_observed") is True
                            and "fork_id" in observed_profile
                        )
                        if fork_observed:
                            fallback_profile["fork_id"] = observed_profile["fork_id"]
                        if observed_profile and "likeability_level_10_enabled" in observed_profile:
                            fallback_profile["likeability_level_10_enabled"] = (
                                observed_profile["likeability_level_10_enabled"]
                            )
                        # Without a readable role page, an account-owned fork
                        # pointer still outranks the graduation template.
                        calculation_projection = (
                            _current_role_calculation_projection({
                                "profile": fallback_profile,
                                "forks": (),
                            })
                            if saved_profile is not None or fork_observed else None
                        )
                if calculation_projection is not None:
                    default_weapon = calculation_projection["default_weapon"]
                    default_fork_id = str(
                        calculation_projection.get("default_fork_id") or ""
                    )
                roles_db[role_name] = {
                    "character_id": character_id,
                    "default_set": suit_name,
                    "default_weapon": default_weapon,
                    "default_fork_id": default_fork_id,
                    "likeability_crit_rate_bonus": _likeability_crit_rate_bonus(
                        static_dao, user_dao, character_id,
                        enabled_override=(
                            calculation_projection.get("likeability_level_10_enabled")
                            if calculation_projection is not None else None
                        ),
                    ),
                    "active_fork_crit_rate_bonus": (
                        calculation_projection["active_fork_crit_rate_bonus"]
                        if calculation_projection is not None
                        else None
                    ),
                    "active_fork_crit_source_resolved": calculation_projection is not None,
                    "extra_shape_label": extra_shape_label,
                    "extra_shape_buffs": extra_shape_buffs,
                    "weights": dict(scoring_role.get("weights") or {}),
                    "main_weights": dict(scoring_role.get("main_weights") or {}),
                }
                board = [[-1] * 5 for _ in range(5)]
                for cell in plan.get("cells") or ():
                    board[int(cell["row"]) - 1][int(cell["column"]) - 1] = 0
                board_matrices[role_name] = board
            if user_dao is not None:
                suit_name_by_id = {
                    str(data["suit_id"]): name for name, data in sets_db.items()
                }
                for custom in user_dao.list_custom_characters():
                    character_id = int(custom["character_id"])
                    role_name = str(custom.get("name_zh") or character_id)
                    weights = user_dao.get_character_weight_preferences(character_id) or {}
                    shape_bonus = custom.get("shape_bonus") or {}
                    extra_shape_buffs = {
                        attributes[str(row["property_id"])]: float(row["display_value"])
                        for row in shape_bonus.get("properties") or ()
                        if attributes.get(str(row["property_id"]))
                    }
                    roles_db[role_name] = {
                        "character_id": character_id,
                        "default_set": suit_name_by_id.get(
                            str(custom.get("target_suit_id") or ""), ""
                        ),
                        "default_weapon": "",
                        "extra_shape_label": str(
                            shape_bonus.get("shape_label") or "Type-3"
                        ),
                        "extra_shape_buffs": extra_shape_buffs,
                        "weights": _custom_weight_labels(
                            weights, attributes, "property_weights"
                        ),
                        "main_weights": _custom_weight_labels(
                            weights, attributes, "main_property_weights"
                        ),
                        "is_custom": True,
                    }
                    board_matrices[role_name] = _custom_board_matrix(
                        list(custom.get("board_cells") or ())
                    )
        finally:
            if user_dao is not None:
                user_dao.close()
    return LegacyAllocationStaticCatalog(
        roles_db, sets_db, shapes_db, board_matrices, fork_ids_by_name,
    )
