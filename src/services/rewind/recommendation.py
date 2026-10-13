# 编排倒带形状推荐的数据读取与策略计算。
"""Read a pinned inventory snapshot and build rewind-shape advice."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from src.domain.role_name_order import role_name_sort_key
from src.domain.rewind_loadout import (
    RewindSlotReference, RewindSlotSummary, inspect_slot, positive_id, shape_key,
)
from src.domain.equipment.virtual_equipment import (
    normalized_equipment_assignment,
    is_virtual_equipment_assignment,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def snapshot_complete(summary):
    if not summary:
        return False
    complete = summary.get("complete")
    if complete is not True and not (type(complete) is int and complete == 1):
        return False
    declared, stored = summary.get("declared_item_count"), summary.get("stored_item_count")
    return (type(declared) is int and type(stored) is int
            and declared >= 0 and declared == stored)


class RewindLoadoutReader:
    def __init__(self, dao, shapes):
        self.dao = dao
        self.shape_map = {shape_key(row["shape_id"]): row for row in shapes}
        # 缓存只活在本次一致读取内；来源快照与所选 UID 集共同构成身份。
        self.items = {}
        self.summaries = {}

    def inspect(self, slot):
        try:
            return self._inspect(slot)
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            plan = slot.get("current_plan") or {}
            plan_id = positive_id(plan.get("plan_id")) if isinstance(plan, Mapping) else None
            base = inspect_slot(slot, {}, {}, self.shape_map)
            return replace(base, reference=replace(base.reference, plan_id=plan_id),
                           state="layout_invalid", reason="配装资料不足，请重新计算并保存。")

    def _inspect(self, slot):
        plan = slot.get("current_plan") or {}
        source = positive_id(plan.get("source_snapshot_id"))
        inventory = {}
        if source is not None:
            if source not in self.summaries:
                self.summaries[source] = self.dao.inventory_snapshot_summary(source)
            if snapshot_complete(self.summaries[source]):
                uids = set()
                for raw in plan.get("assignments") or ():
                    row = normalized_equipment_assignment(raw)
                    if row.get("kind") == "module" and not is_virtual_equipment_assignment(row):
                        uids.add((int(row["uid_serial"]), int(row["uid_slot"])))
                key = (source, frozenset(uids))
                if key not in self.items:
                    self.items[key] = {
                        (int(row["uid_slot"]), int(row["uid_serial"])): row
                        for row in self.dao.list_inventory_items(source, kind="module", uids=uids)
                    }
                inventory = self.items[key]
        return inspect_slot(slot, plan, inventory, self.shape_map)


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
    selected_slots: tuple[RewindSlotReference, ...] = ()
    static_identity: tuple[str, int, int] | None = None
    selected_source_snapshots: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RewindTargetRole:
    character_id: int
    name: str
    default_suit_id: str | None
    is_custom: bool = False
    slots: tuple[RewindSlotSummary, ...] = ()


class RewindShapeRecommendationService:
    """Application boundary for the generic custom-rewind recommendation."""

    def __init__(
        self,
        *,
        user_database_path: str | Path,
        static_database_path: str | Path,
        asset_root: str | Path | None = None,
        user_dao_factory: Callable[..., Any] = UserDataDao,
        static_dao_factory: Callable[..., Any] = StaticGameDataDao,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._static_database_path = Path(static_database_path)
        self.asset_root = Path(asset_root) if asset_root is not None else None
        self._user_dao_factory = user_dao_factory
        self._static_dao_factory = static_dao_factory

    def load_preferences(self) -> dict[str, object]:
        if not self._user_database_path.is_file():
            return {}
        with self._user_dao_factory(self._user_database_path) as user_dao:
            copies = getattr(user_dao, "list_application_setting_copies", lambda: {})()
        value = copies.get("rewind_recommendation") if isinstance(copies, dict) else None
        return dict(value) if isinstance(value, dict) else {}

    def save_preferences(self, value: dict[str, object], *,
                         expected_slots: tuple[RewindSlotReference, ...] = (), static_identity=None) -> None:
        if expected_slots and static_identity != self.static_identity():
            raise ValueError("计算使用的静态资料已变化，请关闭并重新打开倒带推荐后生成。")
        with self._user_dao_factory(self._user_database_path) as user_dao:
            if "slot_selection_version" in value:
                version = value["slot_selection_version"]
                if type(version) is not int or version not in {1, 2}:
                    raise ValueError("槽位偏好版本或格式无效")
                if version == 1:
                    maps = {"legacy": value.get("selected_slots")}
                else:
                    maps = value.get("selected_slots_by_strategy")
                    if not isinstance(maps, dict) or set(maps) != {"balanced", "focused"}:
                        raise ValueError("槽位偏好版本或格式无效")
                for selected in maps.values():
                    if not isinstance(selected, dict):
                        raise ValueError("槽位偏好版本或格式无效")
                    for key, slot_id in selected.items():
                        identifier = positive_id(key)
                        if identifier is None or (slot_id is not None and (type(slot_id) is not int or slot_id <= 0)):
                            raise ValueError("槽位偏好身份无效")
                        slot = user_dao.get_loadout_slot(slot_id) if slot_id is not None else None
                        if slot is not None and slot["character_id"] != identifier:
                            raise ValueError("槽位不属于所选角色")
            if expected_slots:
                user_dao.replace_application_setting_copy("rewind_recommendation", value,
                    expected_loadout_plans=tuple({"character_id": ref.character_id, "slot_id": ref.slot_id,
                                                 "plan_id": ref.plan_id} for ref in expected_slots))
            else:
                user_dao.replace_application_setting_copy("rewind_recommendation", value)

    def list_target_roles(self, *, checkpoint=lambda: None) -> tuple[RewindTargetRole, ...]:
        checkpoint()
        identity = self.static_identity()
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
                    key=lambda row: role_name_sort_key(str(row.get("name_zh") or row["character_id"])),
                )
            ]
        slots_by_character = {}
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao, self._static_dao_factory(self._static_database_path) as static_dao:
                with user_dao.read_consistent_state():
                    custom_roles = user_dao.list_custom_characters()
                    reader = RewindLoadoutReader(user_dao, static_dao.list_shapes())
                    visible_slots = user_dao.list_visible_loadout_slots_with_plans()
                    for slot in visible_slots:
                        checkpoint()
                        identifier = int(slot["character_id"])
                        slots_by_character.setdefault(identifier, []).append(reader.inspect(slot))
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
        roles = [
            replace(
                role,
                slots=tuple(slots_by_character.get(role.character_id, ())),
            )
            for role in roles
        ]
        checkpoint()
        if identity != self.static_identity():
            raise ValueError("静态资料已变化，请关闭并重新打开倒带推荐。")
        return tuple(sorted(
            roles, key=lambda role: (role_name_sort_key(role.name), role.character_id),
        ))

    def load_owned_shape_counts(self) -> tuple[tuple[str, int], ...]:
        """Count every official drive shape in the current pinned inventory."""

        with self._static_dao_factory(self._static_database_path) as static_dao:
            shape_ids = tuple(str(row["shape_id"]) for row in static_dao.list_shapes())
        counts: Counter[str] = Counter()
        if self._user_database_path.is_file():
            with self._user_dao_factory(self._user_database_path) as user_dao:
                with user_dao.read_consistent_state():
                    snapshot_id = user_dao.current_inventory_snapshot_id()
                    summary = user_dao.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None
                    if snapshot_complete(summary):
                        counts = _owned_shape_counts(
                            user_dao.list_inventory_items(snapshot_id, kind="module"), set(shape_ids)
                        )
        return tuple((shape_id, int(counts[shape_id])) for shape_id in shape_ids)

    def analyze_for_targets(
        self, *, selected_slots: tuple[RewindSlotReference, ...],
        target_character_ids: tuple[int, ...] = (), strategy: str = "balanced",
        primary_character_ids: tuple[int, ...] = (), primary_character_id: int | None = None,
        selection_limit: int = 8, target_grade: str = "S", target_custom_percent: float | None = None,
        checkpoint: Callable[[], None] = lambda: None,
    ) -> RewindShapeAnalysis:
        if strategy not in {"balanced", "focused"}:
            raise ValueError(f"unknown rewind strategy: {strategy}")
        selected_ids = set(primary_character_ids if strategy == "focused" else target_character_ids)
        if strategy == "focused" and primary_character_id is not None:
            selected_ids.add(primary_character_id)
        if not selected_ids:
            raise ValueError("请先选择参与本次策略的角色，再生成推荐。")
        if any(type(value) is not int or value <= 0 for value in selected_ids):
            raise ValueError("角色选择资料无效，请重新选择。")
        target_label = _target_label(target_grade, target_custom_percent)
        if not self._user_database_path.is_file():
            raise ValueError("尚无保存方案，请先计算并保存。")
        checkpoint()
        identity = self.static_identity()
        with self._static_dao_factory(self._static_database_path) as static_dao, self._user_dao_factory(self._user_database_path) as dao:
            shape_rows = static_dao.list_shapes()
            shapes = tuple(RewindShape(str(row["shape_id"]), int(row["cell_count"])) for row in shape_rows)
            known_shape_ids = {shape.shape_id for shape in shapes}
            role_names = {int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
                          for row in static_dao.list_characters()}
            with dao.read_consistent_state():
                role_names.update({int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
                                   for row in dao.list_custom_characters()})
                snapshot_id = dao.current_inventory_snapshot_id()
                summary = dao.inventory_snapshot_summary(snapshot_id) if snapshot_id is not None else None
                if not snapshot_complete(summary):
                    raise ValueError("当前库存资料不完整，请先同步背包再生成推荐。")
                snapshot_source = str(summary.get("source") or "")
                # 当前库存只提供拥有数量；所选方案另读自身 source_snapshot_id，不能互补。
                owned_shape_counts = _owned_shape_counts(
                    dao.list_inventory_items(snapshot_id, kind="module"), known_shape_ids
                )
                references = {}
                for ref in selected_slots:
                    if not isinstance(ref, RewindSlotReference) or positive_id(ref.character_id) is None:
                        raise ValueError("槽位选择资料无效，请重新选择。")
                    if ref.character_id in references:
                        raise ValueError("同一角色只可选择一个配装槽位。")
                    references[ref.character_id] = ref
                reader, validated, problems = RewindLoadoutReader(dao, shape_rows), [], []
                for identifier in sorted(selected_ids):
                    checkpoint()
                    name, ref = role_names.get(identifier, str(identifier)), references.get(identifier)
                    if ref is None:
                        problems.append(f"{name} 尚未选择配装槽位，请先选择。")
                        continue
                    slot = dao.get_loadout_slot(ref.slot_id) if positive_id(ref.slot_id) else None
                    plan = (slot or {}).get("current_plan") or {}
                    if (slot is None or slot.get("is_archived") or int(slot["character_id"]) != identifier
                            or plan.get("plan_id") != ref.plan_id):
                        problems.append(f"{name} 的所选配装已变化，请关闭并重新打开倒带推荐后选择。")
                        continue
                    row = reader.inspect(slot)
                    if row.state != "ready":
                        problems.append(f"{name} 的“{row.slot_name}”槽位{row.reason}")
                    else:
                        validated.append(row)
                if problems:
                    raise ValueError("\n".join(problems))
        shortfalls, score_gaps = Counter(), Counter()
        for slot in validated:
            checkpoint()
            for drive in slot.drives:
                threshold = (target_percentage_score(target_custom_percent, drive.area)
                             if target_custom_percent is not None else target_grade_score(target_grade, drive.area))
                gap = max(0.0, threshold - drive.score)
                if gap > 0:
                    shortfalls[drive.shape_id] += 1
                    score_gaps[drive.shape_id] += gap
        frozen_references = tuple(row.reference for row in validated)
        checkpoint()
        self.validate_selection(frozen_references, identity)
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
            selected_slots=frozen_references, static_identity=identity,
            selected_source_snapshots=tuple((row.reference.slot_id, row.source_snapshot_id) for row in validated),
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

    def static_identity(self):
        stat = self._static_database_path.stat()
        return str(self._static_database_path.resolve()), stat.st_size, stat.st_mtime_ns

    def validate_selection(self, references, static_identity):
        if static_identity != self.static_identity():
            raise ValueError("计算使用的静态资料已变化，请关闭并重新打开倒带推荐后生成。")
        with self._user_dao_factory(self._user_database_path) as dao, dao.read_consistent_state():
            for ref in references:
                slot = dao.get_loadout_slot(ref.slot_id)
                if (slot is None or slot.get("is_archived") or slot["character_id"] != ref.character_id
                        or ((slot.get("current_plan") or {}).get("plan_id")) != ref.plan_id):
                    raise ValueError("所选配装已变化，请关闭并重新打开倒带推荐后生成。")


def _target_label(target_grade: str, target_custom_percent: float | None) -> str:
    """Format and validate the threshold name shown in rewind analysis notices."""

    if target_custom_percent is None:
        return f" {target_grade.upper()} 评分等级"
    target_percentage_score(target_custom_percent, 1)
    return f"自选 {float(target_custom_percent):g}% 目标"


def _owned_shape_counts(rows, known_shape_ids: set[str]) -> Counter[str]:
    """只计调用方已经冻结的库存 rows，不重新读取当前指针。"""
    return Counter(
        shape_id for row in rows
        if (shape_id := _official_shape_id(str(row.get("geometry") or ""), known_shape_ids))
    )


def _official_shape_id(value: str, known_shape_ids: set[str]) -> str:
    """Normalize snapshot geometry (``Hen2``) to its static official ID."""

    if value in known_shape_ids:
        return value
    candidate = f"EquipmentGeometry_{value.removeprefix('EquipmentGeometry_')}"
    return candidate if candidate in known_shape_ids else ""



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
