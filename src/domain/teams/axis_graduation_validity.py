# 区分历史事实、当前输入与基准上下文；有效性只投影状态，不重算伤害或改写历史。
from dataclasses import dataclass, replace

from src.domain.teams.axis_graduation_presentation import AxisGraduationDisplay


@dataclass(frozen=True, slots=True)
class AxisResultValidity:
    benchmark: str
    current: str
    component: str
    reasons: tuple[str, ...] = ()

    @property
    def state(self):
        if self.benchmark == "stale":
            return "benchmark_stale"
        if self.component != "ready":
            return "component_pending"
        if self.current == "stale":
            return "current_stale"
        if self.benchmark != "matching" or self.current != "matching":
            return "validity_unverified"
        return "context_matching"


@dataclass(frozen=True, slots=True)
class AxisPlanChangeScope:
    mode: str
    members: tuple[tuple[int, int | None], ...]

    @classmethod
    def from_request(cls, request):
        provenance = request["current_provenance"]
        return cls(provenance["source_mode"], tuple(
            (row["character_id"], row["slot_id"]) for row in provenance["members"]))

    def affected(self, event):
        # 计算方案保存不代表游戏装配；备用槽的保存也不影响已冻结的所选槽。
        if self.mode == "game":
            return False
        return any(key in event.character_ids and (not event.slot_ids or slot is None or slot in event.slot_ids)
                   for key, slot in self.members)


def validity_display(display, validity):
    if display is None:
        return None
    label = {"benchmark_stale": "历史结果 · 基准需重建", "current_stale": "历史结果 · 当前评价过期",
             "component_pending": "历史结果 · 组件待就绪", "validity_unverified": "历史结果 · 有效性待确认",
             "context_matching": "上下文已核对（未重算）"}[validity.state]
    return replace(display, headline=label + "\n" + display.headline,
                   evidence=display.evidence + "\n有效性核对：" + ("；".join(validity.reasons) or label))


def empty_axis_display():
    return AxisGraduationDisplay("轴伤毕业：待建立或选用当前草稿的基准", "", "", "", "", "")
