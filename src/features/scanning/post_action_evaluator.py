# 评估全量扫描后的弃置与锁定目标。
"""Evaluate post-scan discard/lock targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.features.scanning.post_actions import (
    PostActionScoreContext,
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
        score_context = PostActionScoreContext.from_config_dir(str(self.config_dir or "config"))
        if score_context.strict:
            logger.info(
                "[状态管理] 已启用实际可用角色评分: "
                f"驱动形状映射 {len(score_context.drive_roles_by_shape)} 个，"
                f"卡带套装映射 {len(score_context.tape_roles_by_set)} 个"
            )
        else:
            logger.warning("[状态管理] 未能建立图纸可用角色映射，已退回全角色评分")
        filter_summary = summarize_post_action_filtering(parsed_items, effective_config)
        state_changes = build_state_changes(
            parsed_items,
            effective_config,
            scoring,
            self.selected_roles,
            score_context,
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
            f"类型范围 {filter_summary.get('post_action_type_range_filtered_count', 0)} 件"
        )
        for change in state_changes:
            decision = change.get("decision", {}) or {}
            lock_detail = decision.get("lock", {}) or {}
            discard_detail = decision.get("discard", {}) or {}
            chosen = lock_detail if change.get("target_state") == "locked" else discard_detail
            if change.get("target_state") == "normal":
                chosen = lock_detail if change.get("current_state") == "locked" else discard_detail
            logger.info(
                f"[状态管理] 目标 raw_drive_{int(change.get('index', 0)):04d} "
                f"{change.get('current_state')} -> {change.get('target_state')} "
                f"type={change.get('item_type')} quality={change.get('quality')} "
                f"shape={change.get('shape_id')} set={change.get('set_name')} "
                f"best_role={chosen.get('role', '')} score={float(chosen.get('score', 0.0) or 0.0):.2f} "
                f"grade={chosen.get('grade', '')} threshold={chosen.get('threshold', '')} "
                f"eligible_roles={chosen.get('eligible_roles', 0)} mode={chosen.get('match_mode', '')} "
                f"reason={chosen.get('reason', '')} sub_stats={change.get('sub_stats')}"
            )
        return PostActionEvaluation(
            config=effective_config,
            enabled=True,
            state_changes=state_changes,
            filter_summary=filter_summary,
        )
