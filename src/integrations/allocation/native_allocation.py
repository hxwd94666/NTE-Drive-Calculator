# 接入经清单校验的空幕分配核心，并恢复现有渲染需要的装备对象。
from __future__ import annotations

from collections import Counter
from concurrent.futures import CancelledError
from math import isfinite
from loguru import logger

from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.integrations.allocation.native_allocation_wire import freeze
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError
from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import execute_with_soft_main_fallback


def create_allocation_executor(*, static_database_path, cancel_check=None, analysis_client=None):
    """Bind one verified component to this calculation; never fall back silently."""
    if cancel_check is not None and cancel_check():
        raise CancelledError("分配计算已取消")
    client = analysis_client if analysis_client is not None else create_bundled_analysis_client(
        static_database_path=static_database_path, cancelled=cancel_check,
    )
    if client is None or not client.supports_allocation:
        raise NativeAnalysisError("缺少支持空幕分配的分析组件，请更新分析组件")
    try:
        identity = client.version()
    except NativeAnalysisCancelled:
        raise CancelledError("分配计算已取消") from None
    if "allocation_v2" not in identity.get("capabilities", []):
        raise NativeAnalysisError("分析组件实际能力与空幕分配清单不匹配")

    def execute(request, scorer):
        def checkpoint():
            if request.cancel_check is not None and request.cancel_check():
                raise CancelledError("分配计算已取消")

        checkpoint()
        def allocate_once(frozen_request):
            checkpoint()
            if (any(role.get("suit_permanent_properties") for role in frozen_request.roles_db.values())
                    and not getattr(client, "supports_allocation_suit_crit", False)):
                raise NativeAnalysisError("分析组件实际能力与空幕分配清单不匹配")
            plans = client.allocate(freeze(frozen_request, scorer), checkpoint=checkpoint)
            result = restore(plans, frozen_request)
            checkpoint()
            return result

        try:
            # 全部轮次仍走同一组件与冻结库存；共用内存内核只编排，不在Python重复评分。
            result = execute_with_soft_main_fallback(request, allocate_once)
            for group in request.priority_groups:
                selected = [result[role] for role in group if role in result]
                if len(selected) < 2:
                    continue
                progress = selected[0].get("group_search") or {}
                logger.info(
                    "同级组分配诊断: 成员={} 初轮完成={} 留一尝试={} 最终完成={} "
                    "恢复候选={} 预算截断={} 候选截断={}",
                    progress.get("members", len(selected)),
                    progress.get("initial_completed", 0),
                    progress.get("leave_one_out_attempts", 0),
                    progress.get("completed", sum(bool(p.get("valid")) for p in selected)),
                    sum(int((p.get("group_search") or {}).get("recovery_candidates", 0)) for p in selected),
                    any(bool(p.get("budget_exhausted")) for p in selected),
                    any(bool(p.get("candidate_truncated")) for p in selected),
                )
        except NativeAnalysisCancelled:
            raise CancelledError("分配计算已取消") from None
        checkpoint()
        return result

    return execute


def restore(plans, request, *, frozen_models=False, originals=None):
    """Validate identities and decode wire data before rendering or saving."""
    try:
        if set(plans) != set(request.role_order):
            raise ValueError("role mismatch")
        if originals is None:
            originals = {item.uid: item for item in request.inventory}
        used = set()
        immutable = ("item_type", "item_id", "area", "shape_id", "quality", "set_name",
                     "sub_stats", "main_stats", "main_value", "suit_id", "discarded",
                     "is_duplicate_drive", "duplicate_group_id", "duplicate_index", "duplicate_count")
        def decode(raw, model):
            if not frozen_models:
                return model(**raw)
            original = originals.get(raw["uid"])
            if not isinstance(original, model):
                raise ValueError("equipment kind mismatch")
            # Weighted inputs were already validated by the context builder.
            # Preserve that projection, including incomplete historic main-stat data.
            if any(raw.get(key) != getattr(original, key, None) for key in immutable):
                raise ValueError("equipment identity mismatch")
            scores = raw["role_scores"]
            if not isinstance(scores, dict) or any(
                type(score) not in (int, float) or not isfinite(score)
                for score in scores.values()
            ):
                raise ValueError("invalid equipment scores")
            return original.model_copy(update={"role_scores": scores})

        for role, plan in plans.items():
            if not isinstance(plan, dict) or type(plan.get("valid")) is not bool:
                raise ValueError("invalid plan")
            if isinstance(plan.get("assigned_tape"), dict):
                plan["assigned_tape"] = decode(plan["assigned_tape"], Tape)
            elif plan.get("assigned_tape") is not None:
                raise ValueError("invalid tape")
            for field in ("assigned_set_drives", "assigned_extra_drives"):
                if field in plan:
                    plan[field] = [decode(item, Drive) for item in plan[field]]
            if "stat_priority_key" in plan:
                plan["stat_priority_key"] = tuple(plan["stat_priority_key"])
            items = [*plan.get("assigned_set_drives", []), *plan.get("assigned_extra_drives", [])]
            if plan.get("assigned_tape") is not None:
                items.append(plan["assigned_tape"])
            for item in items:
                original = originals.get(item.uid)
                if original is None or any(getattr(item, key, None) != getattr(original, key, None)
                                           for key in immutable):
                    raise ValueError("equipment identity mismatch")
            if not plan["valid"]:
                continue
            blueprint = plan.get("blueprint")
            if blueprint not in request.blueprints_db.get(role, ()):
                raise ValueError("blueprint mismatch")
            for field, shapes in (("assigned_set_drives", "set_pieces"),
                                  ("assigned_extra_drives", "extra_pieces")):
                if Counter(item.shape_id for item in plan.get(field, ())) != Counter(blueprint[shapes]):
                    raise ValueError("shape mismatch")
            for item in items:
                if item.uid in used:
                    raise ValueError("duplicate equipment")
                used.add(item.uid)
            if (type(plan.get("score")) not in (int, float)
                    or not isfinite(plan["score"])
                    or abs(sum(item.role_scores.get(role, 0.0) for item in items) - plan["score"]) > 1e-8):
                raise ValueError("score mismatch")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise NativeAnalysisError("空幕分配响应内容或冻结装备身份无效") from None
    return plans
