# 保存仓库本次计算的不可变业务输入，不携带账号连接或可回写的兼容数据库。
from dataclasses import dataclass, field
import json

from src.domain.allocation.inputs import AllocationContext


@dataclass(frozen=True, slots=True)
class WarehouseCalculationInput:
    snapshot_id: int
    account_identity: str
    static_dataset_id: str
    static_sha256: str
    payload: bytes = field(repr=False)
    profile_contexts: tuple[AllocationContext, ...] = field(default=(), repr=False)
    profile_issues: tuple[str, ...] = ()
    profiles_frozen: bool = False

    def values(self):
        """每个消费者只修改自己的投影；嵌套字典也不与冻结原件共享。"""
        return json.loads(self.payload)
