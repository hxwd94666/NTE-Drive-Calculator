# 接入经清单校验的空幕分配核心，并恢复现有渲染需要的装备对象。
from __future__ import annotations

from collections import Counter
from concurrent.futures import CancelledError

from src.integrations.analysis_core_release import create_bundled_analysis_client
from src.integrations.native_allocation_wire import freeze
from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError
from src.models.equipment import Drive, Tape


def create_allocation_executor(*, static_database_path, cancel_check=None):
    """Bind one verified component to this calculation; never fall back silently."""
    if cancel_check is not None and cancel_check():
        raise CancelledError("分配计算已取消")
    client = create_bundled_analysis_client(
        static_database_path=static_database_path, cancelled=cancel_check,
    )
    if client is None or not client.supports_allocation:
        raise NativeAnalysisError("缺少支持空幕分配的分析组件，请更新分析组件")
    try:
        identity = client.version()
    except NativeAnalysisCancelled:
        raise CancelledError("分配计算已取消") from None
    if "allocation_v1" not in identity.get("capabilities", []):
        raise NativeAnalysisError("分析组件实际能力与空幕分配清单不匹配")

    def execute(request, scorer):
        def checkpoint():
            if request.cancel_check is not None and request.cancel_check():
                raise CancelledError("分配计算已取消")

        checkpoint()
        payload = freeze(request, scorer)
        try:
            plans = client.allocate(payload, checkpoint=checkpoint)
            result = restore(plans, request)
        except NativeAnalysisCancelled:
            raise CancelledError("分配计算已取消") from None
        checkpoint()
        return result

    return execute


def restore(plans, request):
    """Validate identities and decode wire data before rendering or saving."""
    try:
        if set(plans) != set(request.role_order):
            raise ValueError("role mismatch")
        originals = {item.uid: item for item in request.inventory}
        used = set()
        immutable = ("item_type", "item_id", "area", "shape_id", "quality", "set_name",
                     "sub_stats", "main_stats", "main_value", "suit_id", "discarded",
                     "is_duplicate_drive", "duplicate_group_id", "duplicate_index", "duplicate_count")
        for role, plan in plans.items():
            if not isinstance(plan, dict) or type(plan.get("valid")) is not bool:
                raise ValueError("invalid plan")
            if isinstance(plan.get("assigned_tape"), dict):
                plan["assigned_tape"] = Tape(**plan["assigned_tape"])
            elif plan.get("assigned_tape") is not None:
                raise ValueError("invalid tape")
            for field in ("assigned_set_drives", "assigned_extra_drives"):
                if field in plan:
                    plan[field] = [Drive(**item) for item in plan[field]]
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
                    or abs(sum(item.role_scores.get(role, 0.0) for item in items) - plan["score"]) > 1e-8):
                raise ValueError("score mismatch")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise NativeAnalysisError("空幕分配响应内容或冻结装备身份无效") from None
    return plans
