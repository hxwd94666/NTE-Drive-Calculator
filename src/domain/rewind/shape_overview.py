# 汇总倒带所选方案逐件驱动相对目标的缺分与多分，供手选驱动弹窗与兼顾模式“共同缺口”只读展示。
"""Pure per-shape margin summary for the rewind picker and dual-target overlap view.

输入是本次“生成推荐”冻结的逐件保存分与目标分；这里只做校验、聚合与排序，
不读取当前库存或权重，也不改变八槽推荐结果。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import isfinite

GROUP_KEYS = ('improve', 'push')


def _valid_score(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value) and value >= 0


def _positive_id(value) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True)
class DriveMargin:
    """一件已保存驱动相对本次冻结目标的余量。

    character_id/slot_id 为正式身份，只用于追溯；saved_score 是方案冻结的逐件评分，
    target_score 是本次目标换算后的分数。group 仅兼顾模式使用（improve/push），其余模式为 None。
    """

    character_id: int
    slot_id: int
    shape_id: str
    area: int
    saved_score: float
    target_score: float
    group: str | None = None

    def __post_init__(self):
        if (not _positive_id(self.character_id) or not _positive_id(self.slot_id)
                or not isinstance(self.shape_id, str) or not self.shape_id
                or type(self.area) is not int or self.area <= 0 or not _valid_score(self.saved_score)
                or not _valid_score(self.target_score) or self.group not in (None, *GROUP_KEYS)):
            raise ValueError('驱动余量资料无效。')

    @property
    def margin(self) -> float:
        """正数为多分，负数为缺分；等于目标视为已达标。"""
        return float(self.saved_score) - float(self.target_score)

    @property
    def normalized_margin(self) -> float:
        # 单件满分基数为面积 × 10，与兼顾模式的归一化缺口同一口径。
        return self.margin / (self.area * 10.)


@dataclass(frozen=True, slots=True)
class MarginTotals:
    """缺分只累计低于目标的部分、多分只累计高于目标的部分，二者互不抵消。"""

    shortfall_count: int = 0
    shortfall: float = 0.
    normalized_shortfall: float = 0.
    surplus_count: int = 0
    surplus: float = 0.
    normalized_surplus: float = 0.


def _totals(drives: Iterable[DriveMargin]) -> MarginTotals:
    short, over = [], []
    for drive in drives:
        (short if drive.margin < 0 else over).append(drive)
    return MarginTotals(len(short), sum(-row.margin for row in short), sum(-row.normalized_margin for row in short),
                        len(over), sum(row.margin for row in over), sum(row.normalized_margin for row in over))


@dataclass(frozen=True, slots=True)
class ShapeMarginRow:
    shape_id: str
    area: int
    totals: MarginTotals
    # 兼顾模式按 GROUP_KEYS 顺序给出两组各自的统计；其他模式为空。
    groups: tuple[MarginTotals, ...]

    @property
    def demand(self) -> str:
        """兼顾模式返回 shared/improve/push/none；其他模式返回 short/none。"""
        if not self.groups:
            return 'short' if self.totals.shortfall_count else 'none'
        improve, push = (row.shortfall_count > 0 for row in self.groups)
        return 'shared' if improve and push else 'improve' if improve else 'push' if push else 'none'


def slot_drive_margins(slot, threshold: Callable[[int], float], group: str | None = None) -> tuple[DriveMargin, ...]:
    """把一份已校验的保存槽位逐件转成余量；threshold 按驱动面积给出本次目标分。"""
    ref = slot.reference
    return tuple(DriveMargin(ref.character_id, ref.slot_id, drive.shape_id, drive.area, drive.score,
                             threshold(drive.area), group) for drive in slot.drives)


def summarize_shape_margins(margins: Iterable[DriveMargin], *, normalized: bool = False) -> tuple[ShapeMarginRow, ...]:
    """按形状聚合缺分与多分，默认缺分从多到少、缺分相同时多分从少到多。

    normalized=True 时用归一化值排序（兼顾模式），避免大面积形状仅靠原始分差排在前面。
    同一批余量必须全部带分组或全部不带；同一形状面积不一致视为资料损坏，不猜测取哪一个。
    """
    margins = tuple(margins)
    if any(not isinstance(row, DriveMargin) for row in margins):
        raise ValueError('驱动余量资料无效。')
    grouped = {row.group is not None for row in margins}
    if len(grouped) > 1:
        raise ValueError('驱动余量的分组资料不一致。')
    dual = grouped == {True}
    by_shape: dict[str, list[DriveMargin]] = defaultdict(list)
    for row in margins:
        by_shape[row.shape_id].append(row)
    rows = []
    for shape_id, drives in by_shape.items():
        if len({row.area for row in drives}) != 1:
            raise ValueError('同一驱动形状的面积资料不一致。')
        groups = tuple(_totals(row for row in drives if row.group == key) for key in GROUP_KEYS) if dual else ()
        rows.append(ShapeMarginRow(shape_id, drives[0].area, _totals(drives), groups))

    def order(row: ShapeMarginRow):
        totals = row.totals
        if normalized:
            return -totals.normalized_shortfall, totals.normalized_surplus, row.shape_id
        return -totals.shortfall, totals.surplus, row.shape_id

    return tuple(sorted(rows, key=order))
