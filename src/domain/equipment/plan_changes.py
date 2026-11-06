# 已提交方案变更的会话内通知，只携带冻结账号、成员和槽位身份，不作为持久事实源。
from dataclasses import dataclass


@dataclass(frozen=True)
class SavedPlansChanged:
    generation: int
    account_id: str
    database_path: str
    character_ids: tuple[int, ...]
    team_ids: tuple[str, ...]
    slot_ids: tuple[int, ...] = ()
