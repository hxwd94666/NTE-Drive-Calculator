# 评估全量扫描后的弃置与锁定目标。
"""Evaluate post-scan discard/lock targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.inventory.post_actions import (
    build_state_changes,
    merge_post_action_config,
    post_actions_enabled,
    summarize_post_action_filtering,
)
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.utils.logger import logger
from src.services.identification.scoring_scope import prepare_management_scoring
from src.domain.equipment.eligibility.management_settings import MatchingScope


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
        matching_scope=MatchingScope.SCAN,
    ):
        self.cancel_check = cancel_check
        self.matching_scope = MatchingScope(matching_scope)
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

        scoring, score_context, selected_roles, incomplete_scopes = prepare_management_scoring(
            effective_config, inventory, config_dir=self.config_dir, database=self.user_database_path,
            static_database=self.static_database_path, scope=self.matching_scope, cancel_check=self.cancel_check)
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
