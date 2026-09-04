# 统一时间轴的车道、堆叠轨道与配色布局。
"""Layout primitives shared by the unified battle timeline widget."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from typing import Literal

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)


LABEL_WIDTH = 176
RIGHT_MARGIN = 12
TOP = 42
LANE_PADDING = 5
INPUT_LANE_HEIGHT = 31
ACTION_TRACK_HEIGHT = 27
DAMAGE_TRACK_HEIGHT = 23
ROLE_LANE_HEIGHT = 61
MIN_ACTION_WIDTH = 48.0
MIN_DAMAGE_BAR_WIDTH = 20.0

ROLE_COLORS = (
    QColor("#2f81f7"),
    QColor("#d29922"),
    QColor("#db61a2"),
    QColor("#3fb950"),
    QColor("#a371f7"),
    QColor("#f0883e"),
    QColor("#39c5bb"),
    QColor("#ff7b72"),
)
CHANNEL_COLORS = {
    "direct": QColor("#58a6ff"),
    "direct_follow_up": QColor("#a371f7"),
    "reaction_hexed": QColor("#f0883e"),
    "reaction_creation": QColor("#56d364"),
    "reaction_remora": QColor("#79c0ff"),
    "reaction_nova": QColor("#d2a8ff"),
    "reaction_scorch": QColor("#ff7b72"),
    "reaction_stain": QColor("#39c5bb"),
    "reaction_charge": QColor("#e3b341"),
    "reaction_discord": QColor("#db61a2"),
    "reaction_unknown": QColor("#3fb950"),
    "special_nightmare": QColor("#ff75b5"),
    "special_zankou_erosion": QColor("#ff9b73"),
    "special_zankou_venom": QColor("#c77dff"),
    "max_hp_reduction": QColor("#ffd166"),
    "max_hp_reduction_estimated": QColor("#c9a227"),
    "incoming": QColor("#f85149"),
    "other_topple": QColor("#e3b341"),
    "other_reflected_projectile": QColor("#8b949e"),
    "other": QColor("#8b949e"),
}
CHANNEL_ORDER = {
    "special_nightmare": 0,
    "special_zankou_erosion": 1,
    "special_zankou_venom": 2,
    "special": 3,
    "max_hp_reduction": 4,
    "max_hp_reduction_estimated": 5,
    "reaction_scorch": 10,
    "reaction_hexed": 11,
    "reaction_creation": 12,
    "reaction_nova": 13,
    "reaction_remora": 14,
    "reaction_stain": 15,
    "reaction_charge": 16,
    "reaction_discord": 17,
    "reaction_unknown": 18,
    "incoming": 30,
    "other_topple": 31,
    "other_reflected_projectile": 32,
    "other": 33,
}

ROLE_DIRECT_CHANNELS = frozenset({"direct", "direct_follow_up"})


def format_damage(value: float) -> str:
    return f"{value:,.0f}"


def format_time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def format_analysis_evidence(analysis: BattleAnalysisSnapshot) -> str:
    capability = {
        "hit_axis": "正式逐击证据",
        "summary_only": "聚合摘要",
    }.get(analysis.capability_level, analysis.capability_level)
    axis = "完整轴" if analysis.axis_complete else "不完整轴"
    return f"{capability} · {axis} · {format_time_stop_evidence(analysis)}"


def format_time_stop_evidence(analysis: BattleAnalysisSnapshot) -> str:
    return {
        "nte_core": f"nte-core 记录时停 {len(analysis.time_stop_intervals)} 段",
        "nte_core_plus_inferred_linko_e": (
            f"nte-core 时停 + 灵可 E 推算 {len(analysis.time_stop_intervals)} 段"
            "（含低置信）"
        ),
        "inferred_q_action": (
            f"Q 动作推算时停 {len(analysis.time_stop_intervals)} 段（低置信）"
        ),
        "inferred_linko_e": (
            f"灵可 E 推算时停 {len(analysis.time_stop_intervals)} 段（低置信）"
        ),
        "inferred_q_and_linko_e": (
            f"Q / 灵可 E 推算时停 {len(analysis.time_stop_intervals)} 段（低置信）"
        ),
    }.get(getattr(analysis, "time_stop_source_kind", "none"), "未取得时停")


@dataclass(frozen=True, slots=True)
class TimelineLane:
    key: str
    label: str
    kind: Literal["input", "action", "damage"]
    top: int
    height: int
    character_id: int | None = None
    character_name: str = ""
    role_index: int = 0


@dataclass(frozen=True, slots=True)
class TimelineLayout:
    lanes: tuple[TimelineLane, ...]
    input_rows: tuple[tuple[TimelineLane, BattleInferredInput, QRectF], ...]
    action_rows: tuple[tuple[TimelineLane, BattleInferredAction, QRectF], ...]
    group_rows: tuple[tuple[TimelineLane, BattleTimelineDamageGroup, QRectF], ...]


@dataclass(frozen=True, slots=True)
class TimelinePaintedBar:
    kind: Literal["input", "action", "damage_group"]
    item_id: str
    action_id: str | None
    lane_key: str
    rect: QRectF
    start_us: int
    end_us: int
    payload: object


@dataclass(frozen=True, slots=True)
class TimelinePaintedHit:
    lane_key: str
    rect: QRectF
    hit: BattleAnalysisHit


@dataclass(frozen=True, slots=True)
class TimelineSelection:
    kind: Literal["hit", "damage_group", "action", "input"]
    item_id: str
    payload: object

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.item_id}"


def nice_tick_step(span_us: int, plot_width: float) -> int:
    desired = max(2.0, plot_width / 130.0)
    raw = max(1.0, span_us / desired)
    power = 10 ** math.floor(math.log10(raw))
    for multiplier in (1, 2, 5, 10):
        candidate = round(power * multiplier)
        if candidate >= raw:
            return max(1, candidate)
    return max(1, round(power * 10))


def timeline_role_index(
    lanes: Sequence[TimelineLane],
    character_id: int | None,
    character_name: str,
) -> int:
    lane = next(
        (
            row
            for row in lanes
            if row.character_id == character_id and row.character_name == character_name
        ),
        None,
    )
    return lane.role_index if lane is not None else 0


def damage_group_color(
    lanes: Sequence[TimelineLane],
    group: BattleTimelineDamageGroup,
) -> QColor:
    if group.direction == "incoming":
        return QColor(CHANNEL_COLORS["incoming"])
    if group.character_id is not None:
        role_index = timeline_role_index(
            lanes,
            group.character_id,
            group.character_name,
        )
        return QColor(ROLE_COLORS[role_index % len(ROLE_COLORS)])
    return QColor(CHANNEL_COLORS.get(group.channel_key, CHANNEL_COLORS["other"]))


def _role_order(analysis: BattleAnalysisSnapshot) -> list[tuple[int | None, str]]:
    roles: list[tuple[int | None, str]] = []
    for action in analysis.inferred_actions:
        role = (action.character_id, action.character_name)
        if role not in roles:
            roles.append(role)
    for hit in analysis.timeline_hits:
        role = (hit.character_id, hit.character_name)
        if role not in roles:
            roles.append(role)
    return roles


def build_timeline_layout(
    analysis: BattleAnalysisSnapshot,
    *,
    x_for_time: Callable[[int], float],
) -> TimelineLayout:
    roles = _role_order(analysis)
    role_indexes = {role: index for index, role in enumerate(roles)}
    cursor = TOP
    lanes: list[TimelineLane] = []
    input_rows: list[tuple[TimelineLane, BattleInferredInput, QRectF]] = []
    action_rows: list[tuple[TimelineLane, BattleInferredAction, QRectF]] = []
    group_rows: list[tuple[TimelineLane, BattleTimelineDamageGroup, QRectF]] = []

    for device, label in (("mouse", "推算鼠标"), ("keyboard", "推算键盘")):
        lane = TimelineLane(
            key=f"input:{device}",
            label=label,
            kind="input",
            top=cursor,
            height=INPUT_LANE_HEIGHT,
        )
        lanes.append(lane)
        for item in analysis.inferred_inputs:
            if item.device_kind != device:
                continue
            left = x_for_time(item.start_us)
            square_size = float(lane.height - 10)
            right = left + square_size
            if item.end_us > item.start_us + 1:
                right = max(right, x_for_time(item.end_us))
            input_rows.append(
                (lane, item, QRectF(left, lane.top + 5, right - left, lane.height - 10))
            )
        cursor += lane.height

    actions_by_role: dict[tuple[int | None, str], list[BattleInferredAction]] = (
        defaultdict(list)
    )
    for action in analysis.inferred_actions:
        actions_by_role[(action.character_id, action.character_name)].append(action)
    direct_groups_by_role: dict[
        tuple[int | None, str],
        list[BattleTimelineDamageGroup],
    ] = defaultdict(list)
    public_groups: dict[tuple[str, str], list[BattleTimelineDamageGroup]] = (
        defaultdict(list)
    )
    # Off-field DOT/reaction owners still need a visible role lane, but not a
    # fabricated action bar or a separately split public damage lane.
    public_damage_roles: set[tuple[int | None, str]] = set()
    for group in analysis.timeline_damage_groups:
        role = (group.character_id, group.character_name)
        if group.direction == "outgoing" and group.channel_key in ROLE_DIRECT_CHANNELS:
            direct_groups_by_role[role].append(group)
        else:
            public_groups[(group.channel_key, group.channel_label)].append(group)
            if group.direction == "outgoing" and group.character_id is not None:
                public_damage_roles.add(role)

    for role in roles:
        actions = sorted(
            actions_by_role.get(role, []),
            key=lambda item: (item.start_us, item.end_us, item.action_id),
        )
        direct_groups = direct_groups_by_role.get(role, [])
        if not actions and not direct_groups and role not in public_damage_roles:
            continue
        role_index = role_indexes[role]
        lane = TimelineLane(
            key=f"action:{role[0]}:{role[1]}",
            label=f"{role[1]}\n上：直伤 · 下：动作",
            kind="action",
            top=cursor,
            height=ROLE_LANE_HEIGHT,
            character_id=role[0],
            character_name=role[1],
            role_index=role_index,
        )
        lanes.append(lane)
        for index, action in enumerate(actions):
            left = x_for_time(action.start_us)
            right = max(left + MIN_ACTION_WIDTH, x_for_time(action.end_us))
            if index + 1 < len(actions):
                next_left = x_for_time(actions[index + 1].start_us)
                right = min(right, max(left + 1.0, next_left - 2.0))
            action_rows.append(
                (
                    lane,
                    action,
                    QRectF(
                        left,
                        lane.top + lane.height - ACTION_TRACK_HEIGHT + 3,
                        max(1.0, right - left),
                        ACTION_TRACK_HEIGHT - 7,
                    ),
                )
            )
        for group in direct_groups:
            left = x_for_time(group.start_us)
            right = max(left + MIN_DAMAGE_BAR_WIDTH, x_for_time(group.end_us))
            group_rows.append(
                (
                    lane,
                    group,
                    QRectF(left, lane.top + 12, max(1.0, right - left), 10),
                )
            )
        cursor += lane.height

    ordered_group_keys = sorted(
        public_groups,
        key=lambda key: (
            CHANNEL_ORDER.get(key[0], 999),
            key[1],
        ),
    )
    for channel_key, channel_label in ordered_group_keys:
        groups = public_groups[(channel_key, channel_label)]
        lane = TimelineLane(
            key=f"damage:{channel_key}",
            label=channel_label,
            kind="damage",
            top=cursor,
            height=DAMAGE_TRACK_HEIGHT + LANE_PADDING * 2,
        )
        lanes.append(lane)
        for group in groups:
            left = x_for_time(group.start_us)
            right = max(left + MIN_DAMAGE_BAR_WIDTH, x_for_time(group.end_us))
            center_y = lane.top + lane.height / 2
            group_rows.append(
                (
                    lane,
                    group,
                    QRectF(left, center_y - 5, max(1.0, right - left), 10),
                )
            )
        cursor += lane.height

    return TimelineLayout(
        lanes=tuple(lanes),
        input_rows=tuple(input_rows),
        action_rows=tuple(action_rows),
        group_rows=tuple(group_rows),
    )
