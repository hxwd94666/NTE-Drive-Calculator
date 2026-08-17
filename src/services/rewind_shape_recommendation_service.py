# 编排倒带形状推荐的数据读取与策略计算。
"""Read a pinned inventory snapshot and build rewind-shape advice."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.domain.rewind_shape_recommendation import (
    RewindPlan,
    RewindPricingRule,
    RewindShape,
    RewindShapeRecommendation,
    recommend_score_shortfall_shapes,
    target_grade_score,
    target_percentage_score,
)
from src.domain.loadout_plan_scores import assignment_score_key
from src.optimizer.scoring import ScoringEngine
from src.services.equipment_scoring_service import score_drive_stats
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class RewindShapeAnalysis:
    """A read-only recommendation tied to one account inventory snapshot."""

    snapshot_id: int | None
    snapshot_source: str
    shape_count: int
    selection_limit: int
    recommendations: tuple[RewindShapeRecommendation, ...]
    plans: tuple[RewindPlan, ...] = ()
    pricing_rule: RewindPricingRule = RewindPricingRule()
    notice: str = ""
    required_count: int = 0
    strategy: str = "balanced"
    owned_shape_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RewindTargetRole:
    character_id: int
    name: str
    default_suit_id: str | None
    is_custom: bool = False


class RewindShapeRecommendationService:
    """Application boundary for the generic custom-rewind recommendation."""

    def __init__(
        self,
        *,
        user_database_path: str | Path,
        static_database_path: str | Path,
        user_dao_factory: Callable[..., Any] = UserDataDao,
        static_dao_factory: Callable[..., Any] = StaticGameDataDao,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._static_database_path = Path(static_database_path)
        self._user_dao_factory = user_dao_factory
        self._static_dao_factory = static_dao_factory

    def load_preferences(self) -> dict[str, object]:
        if not self._user_database_path.is_file():
            return {}
        with self._user_dao_factory(self._user_database_path) as user_dao:
            copies = getattr(user_dao, "list_application_setting_copies", lambda: {})()
        value = copies.get("rewind_recommendation") if isinstance(copies, dict) else None
        return dict(value) if isinstance(value, dict) else {}

    def save_preferences(self, value: dict[str, object]) -> None:
        with self._user_dao_factory(self._user_database_path) as user_dao:
            saver = getattr(user_dao, "replace_application_setting_copy", None)
            if callable(saver):
                saver("rewind_recommendation", value)

    def analyze(self, *, selection_limit: int = 8) -> RewindShapeAnalysis:
        return self.analyze_for_targets(selection_limit=selection_limit)

    def list_target_roles(self) -> tuple[RewindTargetRole, ...]:
        with self._static_dao_factory(self._static_database_path) as static_dao:
            # The static catalog contains combat transformations and two avatar
            # variants under the same display name.  Neither is a separate
            # cultivation target in this picker.
            roles_by_name: dict[str, dict[str, Any]] = {}
            for character in static_dao.list_characters():
                if str(character.get("classification") or "") == "combat_transformation":
                    continue
                name = str(character.get("name_zh") or character["character_id"])
                existing = roles_by_name.get(name)
                if existing is None or _role_picker_order(character) < _role_picker_order(existing):
                    roles_by_name[name] = character
            roles = [
                RewindTargetRole(
                    character_id=int(character["character_id"]),
                    name=str(character.get("name_zh") or character["character_id"]),
                    default_suit_id=(
                        str(template.get("core_suit_id"))
                        if (template := static_dao.get_character_graduation_template(
                            int(character["character_id"])
                        )) and template.get("core_suit_id")
                        else None
                    ),
                )
                for character in sorted(
                    roles_by_name.values(),
                    key=lambda row: str(row.get("name_zh") or row["character_id"]),
                )
            ]
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                custom_roles = getattr(user_dao, "list_custom_characters", lambda: [])()
            known_ids = {role.character_id for role in roles}
            roles.extend(
                RewindTargetRole(
                    character_id=character_id,
                    name=str(role.get("name_zh") or character_id),
                    default_suit_id=(
                        str(role["target_suit_id"])
                        if role.get("target_suit_id")
                        else None
                    ),
                    is_custom=True,
                )
                for role in custom_roles
                if (character_id := int(role["character_id"])) not in known_ids
            )
        return tuple(sorted(roles, key=lambda role: (role.name, role.character_id)))

    def load_owned_shape_counts(self) -> tuple[tuple[str, int], ...]:
        """Count every official drive shape in the current pinned inventory."""

        with self._static_dao_factory(self._static_database_path) as static_dao:
            shape_ids = tuple(str(row["shape_id"]) for row in static_dao.list_shapes())
        known_shape_ids = set(shape_ids)
        counts: Counter[str] = Counter()
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                snapshot_id = user_dao.current_inventory_snapshot_id()
                if snapshot_id is not None:
                    counts.update(
                        shape_id
                        for row in user_dao.list_inventory_items(snapshot_id, kind="module")
                        if (
                            shape_id := _official_shape_id(
                                str(row.get("geometry") or ""),
                                known_shape_ids,
                            )
                        )
                    )
        return tuple((shape_id, int(counts[shape_id])) for shape_id in shape_ids)

    def analyze_for_targets(
        self,
        *,
        target_character_ids: tuple[int, ...] = (),
        strategy: str = "balanced",
        primary_character_ids: tuple[int, ...] = (),
        primary_character_id: int | None = None,
        selection_limit: int = 8,
        target_grade: str = "S",
        target_custom_percent: float | None = None,
    ) -> RewindShapeAnalysis:
        if strategy not in {"balanced", "focused"}:
            raise ValueError(f"unknown rewind strategy: {strategy}")
        if not target_character_ids:
            raise ValueError("请先选择培养角色，再生成推荐。")
        target_label = _target_label(target_grade, target_custom_percent)
        role_names: dict[int, str] = {}
        with self._static_dao_factory(self._static_database_path) as static_dao:
            role_names = {
                int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
                for row in getattr(static_dao, "list_characters", lambda: [])()
            }
            shapes = tuple(
                RewindShape(
                    shape_id=str(row["shape_id"]),
                    cell_count=int(row["cell_count"]),
                )
                for row in static_dao.list_shapes()
            )
            known_shape_ids = {shape.shape_id for shape in shapes}
            attribute_names = {
                str(row.get("attribute_id") or ""): ScoringEngine._scoring_property_name(row)
                for row in getattr(static_dao, "list_equipment_attributes", lambda: [])()
            }

        snapshot_id: int | None = None
        snapshot_source = ""
        owned_shape_counts: Counter[str] = Counter()
        module_rows: list[dict[str, Any]] = []
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                snapshot_id = user_dao.current_inventory_snapshot_id()
                if snapshot_id is not None:
                    summary = user_dao.inventory_snapshot_summary(snapshot_id) or {}
                    snapshot_source = str(summary.get("source") or "")
                    module_rows = list(user_dao.list_inventory_items(
                        snapshot_id,
                        kind="module",
                    ))
                    owned_shape_counts.update(
                        _official_shape_id(
                            str(row.get("geometry") or ""),
                            known_shape_ids,
                        )
                        for row in module_rows
                        if _official_shape_id(
                            str(row.get("geometry") or ""),
                            known_shape_ids,
                        )
                    )
        shortfalls: Counter[str] = Counter()
        score_gaps: Counter[str] = Counter()
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                for custom in getattr(user_dao, "list_custom_characters", lambda: [])():
                    character_id = int(custom["character_id"])
                    role_names[character_id] = str(custom.get("name_zh") or character_id)
                # This is the sole modern source of recommendation inputs: each
                # visible current slot is a saved calculation/loadout plan. A role
                # may own multiple slots and every slot must contribute. Blueprint
                # candidates are deliberately outside this DAO projection.
                slot_rows = getattr(user_dao, "list_current_loadout_slot_plans", lambda: [])()
                active_plans: dict[int, list[dict[str, Any]]] = {}
                for row in slot_rows:
                    slot = row.get("slot") or {}
                    plan = row.get("plan") or {}
                    character_id = plan.get("character_id") or slot.get("character_id")
                    if character_id is None:
                        continue
                    active_plans.setdefault(int(character_id), []).append(plan)
                if not active_plans:
                    legacy_plans = getattr(
                        user_dao,
                        "list_active_loadout_plans_by_role",
                        lambda: {},
                    )()
                    for role_name, plan in legacy_plans.items():
                        character_id = plan.get("character_id")
                        if character_id is None:
                            character_id = next(
                                (key for key, value in role_names.items() if value == role_name),
                                None,
                            )
                        if character_id is not None:
                            active_plans.setdefault(int(character_id), []).append(plan)
                selected_ids = set(target_character_ids)
                if strategy == "focused":
                    selected_ids = {int(value) for value in primary_character_ids}
                    if primary_character_id is not None:
                        selected_ids.add(int(primary_character_id))
                if strategy == "focused" and not selected_ids:
                    raise ValueError("请先选择冲分角色，再生成少角冲分推荐。")

                # A saved plan is immutable with respect to its source snapshot.
                # The current inventory can have changed after the plan was saved,
                # so resolving its assignments against the latest snapshot silently
                # dropped every historical UID and produced a false zero shortfall.
                rows_by_snapshot: dict[int, dict[tuple[int, int], dict[str, Any]]] = {}
                if snapshot_id is not None:
                    rows_by_snapshot[int(snapshot_id)] = {
                        _item_uid(row): row for row in module_rows
                    }

                def plan_items(plan: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
                    source_snapshot_id = plan.get("source_snapshot_id")
                    resolved_snapshot_id = (
                        int(source_snapshot_id)
                        if source_snapshot_id is not None
                        else snapshot_id
                    )
                    if resolved_snapshot_id is None:
                        return {}
                    if resolved_snapshot_id not in rows_by_snapshot:
                        rows_by_snapshot[resolved_snapshot_id] = {
                            _item_uid(row): row
                            for row in user_dao.list_inventory_items(
                                resolved_snapshot_id,
                                kind="module",
                            )
                        }
                    return rows_by_snapshot[resolved_snapshot_id]

                missing_plans: list[str] = []
                missing_items: list[str] = []
                compatibility_engine: ScoringEngine | None = None
                for character_id in selected_ids:
                    role_name = role_names.get(character_id, str(character_id))
                    plans_for_role = active_plans.get(character_id, [])
                    # Both calculated plans and game-loadout imports promoted into a
                    # saved calculation plan are active plan inputs. Blueprints are
                    # never returned by this DAO boundary.
                    if not plans_for_role:
                        missing_plans.append(role_name)
                        continue
                    for plan in plans_for_role:
                        scores = (plan.get("payload") or {}).get("assignment_scores") or {}
                        items_by_uid = plan_items(plan)
                        for assignment in plan.get("assignments") or ():
                            if assignment.get("kind") != "module":
                                continue
                            item = items_by_uid.get(_item_uid(assignment))
                            if item is None:
                                missing_items.append(role_name)
                                continue
                            shape_id = _official_shape_id(
                                str(item.get("geometry") or ""),
                                known_shape_ids,
                            )
                            if not shape_id:
                                continue
                            score_value = scores.get(assignment_score_key(assignment))
                            if score_value is None:
                                raw_assignment = assignment.get("raw_assignment")
                                score_value = (
                                    raw_assignment.get("score")
                                    if isinstance(raw_assignment, dict)
                                    else None
                                )
                            if score_value is None:
                                # Plans saved before per-drive scores were persisted
                                # still contain complete drives and their fixed
                                # snapshot. Rebuild only this legacy missing field
                                # with the application's normal scoring rule; newly
                                # saved plans take the direct persisted-score path.
                                compatibility_engine = compatibility_engine or ScoringEngine(
                                    user_database_path=self._user_database_path,
                                )
                                score_value = _legacy_drive_score(
                                    item,
                                    role_name=role_name,
                                    score_area=int(item.get("grid_count") or 0),
                                    attribute_names=attribute_names,
                                    engine=compatibility_engine,
                                )
                            shape_cells = next(
                                shape.cell_count for shape in shapes if shape.shape_id == shape_id
                            )
                            # ``grid_count`` is the scoring area used by the same
                            # grade thresholds shown in a saved loadout. The score
                            # itself is the persisted per-drive score; it is never
                            # recalculated here.
                            score_area = int(item.get("grid_count") or shape_cells)
                            threshold = (
                                target_percentage_score(target_custom_percent, score_area)
                                if target_custom_percent is not None
                                else target_grade_score(target_grade, score_area)
                            )
                            gap = max(0.0, threshold - float(score_value))
                            if gap > 0:
                                shortfalls[shape_id] += 1
                                score_gaps[shape_id] += gap
            if missing_plans:
                raise ValueError(f"{ '、'.join(missing_plans) } 尚未生成计算方案，请先生成方案。")
            if missing_items:
                raise ValueError(
                    f"{ '、'.join(sorted(set(missing_items))) } 的计算方案来源快照"
                    "缺少已装配驱动，无法读取其保存评分。"
                )
        pricing_rule = RewindPricingRule()
        notice = ""
        recommendations = recommend_score_shortfall_shapes(
            shapes=shapes,
            owned_shape_counts=owned_shape_counts,
            shortfalls=shortfalls,
            score_gaps=score_gaps,
            selection_limit=selection_limit,
            proportional=strategy == "focused",
        )
        positive_shortfall_shape_count = sum(
            1 for score_gap in score_gaps.values() if score_gap > 0
        )
        if positive_shortfall_shape_count > selection_limit:
            notice = (
                f"所需驱动超过 {selection_limit} 个，"
                "建议降低评分等级或使用随机倒带抽取。"
            )
        elif not recommendations:
            notice = (
                "已读取所选角色的保存方案；"
                f"没有低于{target_label}的已装配驱动。"
            )
        benefit = sum(row.priority_score * row.quantity for row in recommendations)
        cost = sum(pricing_rule.cost_for_quantity(row.quantity) for row in recommendations)
        labels = {"balanced": "全面均衡", "focused": "少角冲分"}
        plans = (RewindPlan(strategy, labels[strategy], recommendations, benefit, cost),) if recommendations else ()
        return RewindShapeAnalysis(
            snapshot_id=snapshot_id,
            snapshot_source=snapshot_source,
            shape_count=len(shapes),
            selection_limit=selection_limit,
            recommendations=recommendations,
            plans=plans,
            pricing_rule=pricing_rule,
            notice=notice,
            required_count=sum(shortfalls.values()),
            strategy=strategy,
            owned_shape_counts=tuple(
                (shape.shape_id, int(owned_shape_counts[shape.shape_id]))
                for shape in shapes
            ),
        )


def _target_label(target_grade: str, target_custom_percent: float | None) -> str:
    """Format and validate the threshold name shown in rewind analysis notices."""

    if target_custom_percent is None:
        return f" {target_grade.upper()} 评分等级"
    target_percentage_score(target_custom_percent, 1)
    return f"自选 {float(target_custom_percent):g}% 目标"


def _official_shape_id(value: str, known_shape_ids: set[str]) -> str:
    """Normalize snapshot geometry (``Hen2``) to its static official ID."""

    if value in known_shape_ids:
        return value
    candidate = f"EquipmentGeometry_{value.removeprefix('EquipmentGeometry_')}"
    return candidate if candidate in known_shape_ids else ""


def _item_uid(row: dict[str, Any]) -> tuple[int, int]:
    """Return the stable UID tuple shared by snapshot rows and assignments."""

    return (int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0))


def _role_picker_order(character: dict[str, Any]) -> tuple[int, int, int]:
    """Choose one canonical picker record for a duplicated display name."""

    classification = str(character.get("classification") or "")
    actor_path = str(character.get("actor_path") or "").casefold()
    avatar_variant = classification == "available_avatar_variant"
    return (
        1 if avatar_variant else 0,
        0 if avatar_variant and "female" in actor_path else 1,
        int(character["character_id"]),
    )


def _legacy_drive_score(
    item: dict[str, Any],
    *,
    role_name: str,
    score_area: int,
    attribute_names: dict[str, str],
    engine: ScoringEngine,
) -> float:
    """Fill a missing historical single-drive score using the normal scorer."""

    role = engine.roles_db.get(role_name) or {}
    weights = role.get("weights") if isinstance(role, dict) else None
    sub_stat_names = [
        attribute_names.get(str(stat.get("property_id") or ""), "")
        for stat in item.get("sub_stats") or ()
        if isinstance(stat, dict)
    ]
    quality = {
        "orange": "Gold",
        "gold": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(str(item.get("quality") or "Gold").casefold(), str(item.get("quality") or "Gold"))
    return score_drive_stats(
        engine,
        sub_stat_names=(name for name in sub_stat_names if name),
        area=max(1, score_area),
        weights=weights if isinstance(weights, dict) else {},
        quality=quality,
    )
