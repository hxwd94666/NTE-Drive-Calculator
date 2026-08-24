# 保存战报缺少运行时事实时由产品所有者确认的弧盘默认策略。
"""Product-default fork assumptions that never override stronger evidence."""

from __future__ import annotations


FORK_DEFAULT_POLICY_VERSION = "battle-fork-default-policy-v1"

# Damage-side defaults explicitly confirmed for the fixed-axis analysis.
INFER_KNIGHT_CANDY_CRITICAL_FROM_HIT_REPLAY = True
BLACK_BOOK_DERIVED_HIT_CAN_CRIT = True
MAMEN_DEFAULT_STACKS = 10
JINGMO_DEFAULT_CRIT_STACKS = 4
DOOR_FALLBACK_INPUT_KINDS = frozenset({"E", "Q"})

# Existing conservative boundaries retained as defaults.
BOXING_CANDY_MISSING_HP_USES_BASE_TIER = True
UNKNOWN_BOSS_IS_FALSE = True
UNKNOWN_TARGET_STATE_IS_FALSE = True
DEFAULT_PLAYER_HP_ABOVE_HALF = True
DEFAULT_SHIELD_PRESENT = False
ALLOW_UNOBSERVED_DAMAGE_ACTIONS = False
ALLOW_ENERGY_TO_ADD_FIXED_AXIS_ACTIONS = False
