# 区分历史事实、当前输入与基准上下文；有效性只投影状态，不重算伤害或改写历史。
from dataclasses import dataclass



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
