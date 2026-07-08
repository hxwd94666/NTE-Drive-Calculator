# 评估全量扫描后的弃置与锁定目标。
"""Evaluate post-scan discard/lock targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.features.scanning.post_actions import (
    build_state_changes,
    merge_post_action_config,
    post_actions_enabled,
    summarize_post_action_filtering,
)
from src.optimizer.scoring import ScoringEngine
from src.utils.logger import logger


@dataclass
class PostActionEvaluation:
    config: dict[str, Any] | None = None
    enabled: bool = False
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    filter_summary: dict[str, int] = field(default_factory=dict)


class PostActionEvaluator:
    def __init__(
        self,
        *,
        post_actions_config: dict | None = None,
        selected_roles: list[str] | None = None,
        config_dir=None,
    ):
        self.raw_config = post_actions_config
        self.selected_roles = selected_roles
        self.config_dir = config_dir

    def evaluate(self, parsed_items: list[tuple[int, object, str]], inventory) -> PostActionEvaluation:
        effective_config = merge_post_action_config(self.raw_config) if self.raw_config else None
        if not effective_config or not post_actions_enabled(effective_config):
            return PostActionEvaluation(config=effective_config, enabled=False)

        scoring = ScoringEngine(str(self.config_dir or "config"))
        if not scoring.roles_db:
            return PostActionEvaluation(config=effective_config, enabled=True)

        scoring.evaluate_global_inventory(inventory)
        filter_summary = summarize_post_action_filtering(parsed_items, effective_config)
        state_changes = build_state_changes(
            parsed_items,
            effective_config,
            scoring,
            self.selected_roles,
        )
        logger.info(
            f"扫描后管理评估完成: 成功解析 {len(parsed_items)} 件，"
            f"参与计算 {filter_summary.get('post_action_candidate_count', 0)} 件，"
            f"目标变更 {len(state_changes)} 件。"
        )
        logger.info(
            "扫描后管理过滤统计: "
            f"品质范围 {filter_summary.get('post_action_quality_filtered_count', 0)} 件，"
            f"处理类别 {filter_summary.get('post_action_type_filtered_count', 0)} 件，"
            f"类型范围 {filter_summary.get('post_action_type_range_filtered_count', 0)} 件。"
        )
        for change in state_changes:
            logger.info(
                f"扫描后管理目标: raw_drive_{int(change.get('index', 0)):04d} "
                f"{change.get('current_state')} -> {change.get('target_state')} "
                f"quality={change.get('quality')} type={change.get('item_type')}"
            )
        return PostActionEvaluation(
            config=effective_config,
            enabled=True,
            state_changes=state_changes,
            filter_summary=filter_summary,
        )
