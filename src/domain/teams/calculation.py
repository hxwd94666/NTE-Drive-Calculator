# 冻结单队计算准备身份并按正式角色 ID 解析候选，不使用名称猜测或修改角色偏好。
from dataclasses import dataclass
from collections.abc import Mapping

from src.domain.teams.models import Team


class TeamCalculationError(ValueError):
    def __init__(self, code, character_ids=()):
        super().__init__(code)
        self.code, self.character_ids = code, tuple(character_ids)


@dataclass(frozen=True)
class TeamCalculationRequest:
    account_id: str
    generation: int
    database_path: str
    team: Team
    draft_revision: int


def resolve_team_roles(character_ids, catalog):
    """同名不是身份；缺失、自建或一对多映射都不能作为正式增强四人队。"""
    ids = tuple(character_ids)
    if len(ids) != 4 or len(set(ids)) != 4 or any(type(key) is not int or key <= 0 for key in ids):
        raise TeamCalculationError('incomplete')
    by_id = {}
    for name, role in catalog.items():
        if isinstance(role, Mapping) and not role.get('is_custom') and type(role.get('character_id')) is int:
            by_id.setdefault(role['character_id'], []).append(name)
    missing = tuple(key for key in ids if len(by_id.get(key, ())) != 1)
    if missing:
        raise TeamCalculationError('catalog_missing', missing)
    return tuple(by_id[key][0] for key in ids)
