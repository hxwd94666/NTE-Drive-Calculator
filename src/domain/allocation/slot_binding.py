# 冻结每个保存角色的槽位、方案、来源与偏好身份，不拥有数据库或任务权限。
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WeightedSlotBinding:
    character_id: int
    slot_id: int
    plan_id: int
    source_snapshot_id: int
    profile_id: int
    profile_version: int


@dataclass(frozen=True, slots=True)
class WeightedSaveReceipt:
    """仅记录真实提交后校验过的ID与账号代次，不赋予写入权限。"""

    account_id: str
    generation: int
    user_database_path: str
    plan_ids: tuple[int, ...]
    slot_bindings: tuple[WeightedSlotBinding, ...]
    explicit_slots: bool
