# 依据正式目标身份决定条件控制是否可以采用默认成功语义。
"""Conservative target-control defaults for fixed-axis battle replay."""

from __future__ import annotations

MOFEIKESI_CONTROL_REQUIREMENT = "con_fork_mofeikesi_1"
CONTROL_ELIGIBLE_DEFAULT = "eligible_default"
CONTROL_BLOCKED_BOSS = "blocked_boss"
CONTROL_CONFIRMED_ALL_BOSS = "confirmed_all_boss"
CONTROL_BLOCKED_FORMAL_IMMUNITY = "blocked_formal_immunity"

_BOSS_ENEMY_TYPES = frozenset({"boss", "weeklyboss"})


class BattleTargetControlPolicyService:
    """Resolve only the approved default; never invent observed control state."""

    @staticmethod
    def is_mofeikesi_control_requirement(requirement: str) -> bool:
        return MOFEIKESI_CONTROL_REQUIREMENT in str(requirement or "").casefold()

    @staticmethod
    def default_control_succeeds(
        policy: str,
    ) -> tuple[bool, str]:
        if policy in {CONTROL_BLOCKED_BOSS, CONTROL_CONFIRMED_ALL_BOSS}:
            return False, "正式怪物目录将已解析目标分类为 Boss，不默认控制成功。"
        if policy == CONTROL_BLOCKED_FORMAL_IMMUNITY:
            return False, "正式目标标签表明控制免疫，不默认控制成功。"
        return True, "目标身份未知或无正式免控证据，按用户确认规则默认受到控制。"

    @staticmethod
    def resolve_formal_policy(
        static_dao: object,
        resolved_monster_ids: tuple[str, ...],
        *,
        formal_control_immunity: bool = False,
        all_targets_resolved: bool = True,
    ) -> str:
        """Classify only resolved identities through the formal static catalog."""

        if formal_control_immunity:
            return CONTROL_BLOCKED_FORMAL_IMMUNITY
        identifiers = tuple(dict.fromkeys(
            value.strip() for value in resolved_monster_ids if value.strip()
        ))
        if not identifiers:
            return CONTROL_ELIGIBLE_DEFAULT
        enemy_types = tuple(
            getattr(static_dao, "get_monster_enemy_type_by_formal_id")(value)
            for value in identifiers
        )
        if enemy_types and all(
            str(value or "").casefold() in _BOSS_ENEMY_TYPES
            for value in enemy_types
        ):
            return (
                CONTROL_CONFIRMED_ALL_BOSS
                if all_targets_resolved
                else CONTROL_BLOCKED_BOSS
            )
        return CONTROL_ELIGIBLE_DEFAULT
