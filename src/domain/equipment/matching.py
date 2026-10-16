# 冻结角色套装的合法图纸匹配结果，供鉴定与评分管理共用。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.suit_identity import normalized_suit_name
from src.models.equipment import Tape


@dataclass(frozen=True)
class RoleSuitMatch:
    role: str
    character_id: int
    suit_id: str
    suit_name: str
    is_default: bool
    set_shapes: frozenset[str]
    extra_shapes: frozenset[str]

    def matches(self, item) -> bool:
        if isinstance(item, Tape):
            # 原生对象优先正式 ID；视觉对象缺 ID 时才使用规范名称。
            identity = str(getattr(item, "suit_id", None) or "")
            return (identity == self.suit_id if identity else
                    normalized_suit_name(item.set_name) == normalized_suit_name(self.suit_name))
        return item.shape_id in self.set_shapes or item.shape_id in self.extra_shapes


@dataclass(frozen=True)
class EquipmentMatchContext:
    matches: tuple[RoleSuitMatch, ...] = ()
    # (角色名, 套装 ID, 状态原因)；无图纸是已确认不适配，资料缺口另记 errors。
    unavailable: tuple[tuple[str, str, str], ...] = ()
    errors: tuple[tuple[str, str, str], ...] = ()
    settings_identity: str = ""
    missing_character_ids: tuple[int, ...] = ()

    def for_item(self, item) -> tuple[RoleSuitMatch, ...]:
        return tuple(match for match in self.matches if match.matches(item))

    def errors_for_roles(self, roles) -> tuple[tuple[str, str, str], ...]:
        names = set(roles)
        return tuple(row for row in self.errors if row[0] in names or not row[0])
