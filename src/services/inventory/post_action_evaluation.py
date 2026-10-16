# 评估全量扫描后的弃置与锁定目标。
"""Evaluate post-scan discard/lock targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.post_actions import (
    build_state_changes,
    merge_post_action_config,
    post_actions_enabled,
    summarize_post_action_filtering,
)
from src.integrations.bundled_resources import bundled_config_dir
from src.services.identification.equipment_service import EquipmentIdentificationService
from src.domain.equipment.matching import EquipmentMatchContext
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.utils.logger import logger


@dataclass
class PostActionEvaluation:
    config: dict[str, Any] | None = None
    enabled: bool = False
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    filter_summary: dict[str, int] = field(default_factory=dict)
    matching_identity: str = ""
    matching_errors: tuple = ()


class PostActionEvaluator:
    def __init__(
        self,
        *,
        post_actions_config: dict | None = None,
        selected_roles: list[str] | None = None,
        config_dir=None,
        user_database_path: str | Path | None = None,
        static_database_path: str | Path | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.cancel_check = cancel_check
        self.raw_config = post_actions_config
        # 外层仍会传递计算角色；状态管理只消费配置中各模块自己的正式角色身份。
        self.config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
        # 仓库管理和鉴定都必须按当前账号的自定义权重评分；未自定义
        # 的角色仍由 ScoringEngine 自动回退到公共 SQLite 推荐权重。
        self.user_database_path = (
            Path(user_database_path) if user_database_path is not None else None
        )
        self.static_database_path = Path(static_database_path) if static_database_path is not None else None

    def evaluate(self, parsed_items: list[tuple[int, object, str]], inventory) -> PostActionEvaluation:
        effective_config = merge_post_action_config(self.raw_config) if self.raw_config else None
        if not effective_config or not post_actions_enabled(effective_config):
            return PostActionEvaluation(config=effective_config, enabled=False)

        scoring = None
        score_context = EquipmentMatchContext()
        if any(effective_config[name]["enabled"] for name in ("discard", "lock")):
            try:
                matcher = EquipmentIdentificationService.from_paths(
                    config_dir=self.config_dir, user_database_path=self.user_database_path,
                    static_database_path=self.static_database_path, cancel_check=self.cancel_check)
                scoring, score_context = matcher.scoring, matcher.context
                scoring.evaluate_global_inventory(inventory)
            except (ValueError, RuntimeError, OSError):
                # 评分失败不扩大为全角色，不阻断独立的显式规则，也不生成评分动作。
                scoring = None
                score_context = EquipmentMatchContext(errors=(("", "", "评分资料准备失败"),))
        # 两模块的角色身份独立冻结；只有正在评估的指定范围才需要解析。
        selected_roles, incomplete_scopes = {}, set()
        for module_name in ("discard", "lock"):
            module = effective_config[module_name]
            selected_roles[module_name] = []
            if scoring is not None and module["enabled"] and module["role_scope"] == "selected":
                try:
                    selected_roles[module_name] = _selected_role_names(
                        scoring.roles_db, module["selected_character_ids"], self.static_database_path)
                except (ValueError, RuntimeError, OSError):
                    # 指定范围缺项只使该模块评估未完成，不能按剩余角色推断低分；独立规则仍执行。
                    incomplete_scopes.add(module_name)
        filter_summary = summarize_post_action_filtering(parsed_items, effective_config)
        state_changes = build_state_changes(
            parsed_items,
            effective_config,
            scoring,
            selected_roles,
            score_context,
            evaluation_summary=filter_summary,
            incomplete_scopes=frozenset(incomplete_scopes),
        )
        logger.info(
            f"[状态管理] 评分完成: 成功解析 {len(parsed_items)} 件，"
            f"参与计算 {filter_summary.get('post_action_candidate_count', 0)} 件，"
            f"目标变更 {len(state_changes)} 件"
        )
        logger.info(
            "[状态管理] 过滤统计: "
            f"品质范围 {filter_summary.get('post_action_quality_filtered_count', 0)} 件，"
            f"处理类别 {filter_summary.get('post_action_type_filtered_count', 0)} 件，"
            f"类型范围 {filter_summary.get('post_action_type_range_filtered_count', 0)} 件，"
            f"处理规则命中 {filter_summary.get('preserve_rule_matched_count', 0)} 件"
        )
        return PostActionEvaluation(
            config=effective_config,
            enabled=True,
            state_changes=state_changes,
            filter_summary=filter_summary,
            matching_identity=score_context.settings_identity,
            matching_errors=score_context.errors + tuple(
                (str(identity), "", "角色资料缺失") for identity in score_context.missing_character_ids) + tuple(
                (module, "", "指定角色评分资料缺失，请重新选择角色") for module in sorted(incomplete_scopes)),
        )


def _selected_role_names(
    roles_db: dict[str, Any],
    selected_character_ids: list[int] | tuple[int, ...],
    static_database_path: str | Path | None = None,
) -> list[str]:
    """Resolve the management dialog's role IDs to scoring role names.

    Avatar variants share one logical role.  Resolving through the static
    logical key keeps a saved female/male avatar selection valid when the
    account snapshot later exposes the other official variant ID.
    """

    selected_ids: set[int] = set()
    for value in selected_character_ids:
        try:
            character_id = int(value)
        except (TypeError, ValueError):
            continue
        if character_id > 0:
            selected_ids.add(character_id)
    if not selected_ids:
        return []
    with (StaticGameDataDao(static_database_path) if static_database_path is not None else StaticGameDataDao()) as static_dao:
        selected_keys = {
            static_dao.get_logical_character_key(character_id)
            or f"custom:{character_id}"
            for character_id in selected_ids
        }
        names: list[str] = []
        matched_keys: set[str] = set()
        for role_name, role in roles_db.items():
            try:
                character_id = int(role.get("character_id"))
            except (AttributeError, TypeError, ValueError):
                continue
            logical_key = (
                static_dao.get_logical_character_key(character_id)
                or f"custom:{character_id}"
            )
            if logical_key in selected_keys:
                names.append(str(role_name))
                matched_keys.add(logical_key)
        missing_count = len(selected_keys - matched_keys)
        if missing_count:
            raise ValueError(
                f"弃置/锁定管理中有 {missing_count} 名指定角色缺少可用评分配置，请重新选择角色"
            )
    return names
