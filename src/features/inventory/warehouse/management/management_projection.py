# 将独立管理结果一次性投影到仓库草稿，原始状态、人工目标和生成目标分开持有。
from src.observability.stage_timings import timed_call
from dataclasses import dataclass
from src.ui.common.paint_timings import begin_gui_timing

from src.features.inventory.warehouse.display.item_state import warehouse_item_with_state
from src.services.inventory.recommendation.warehouse_recommendation_preview import preview_targets


@dataclass(frozen=True)
class WarehouseDraftInput:
    snapshot_id: int
    manual_targets: tuple
    revision: int = 0


def capture_management_input(host):
    snapshot = host._warehouse_snapshot_id
    if not isinstance(snapshot, int) or host._warehouse_source != "nte_core":
        raise ValueError("仓库快照不可用，请先刷新背包")
    return WarehouseDraftInput(snapshot, tuple(sorted(host.warehouse_read_controller.manual_targets.items())),
                               getattr(host.warehouse_read_controller, "draft_revision", 0))


def refresh_conflict_summary(host):
    owner = host.warehouse_read_controller
    button = getattr(owner, "conflict_button", None)
    if button is not None:
        count = len(owner.conflicts)
        button.setText(host.text_catalog.render("warehouse.management.conflict.details"))
        button.setVisible(bool(count))
        summary = getattr(owner, "conflict_summary_label", None)
        if summary is not None:
            summary.setText(host.text_catalog.render("warehouse.management.conflict.count", count=count))
        container = getattr(owner, "conflict_summary_container", None)
        if container is not None:
            container.setVisible(bool(count))


def _state_projection(host, pending):
    known = host._warehouse_base_states
    present = {str(item["uid"]) for item in host._warehouse_all_items}
    if set(pending) - set(known) or set(pending) - present:
        raise ValueError("待保存目标缺少已确认的装备状态，请重新生成")
    projected = []
    for item in host._warehouse_all_items:
        uid = str(item["uid"])
        # 未知项仍可浏览与查看原因，但不伪造正常状态，也不通过卡片变更入口投影。
        if uid not in known or not item.get("state_known", True):
            if uid in pending:
                raise ValueError("待保存目标的装备状态未知，请重新生成")
            projected.append(item)
        else:
            projected.append(warehouse_item_with_state(
                item, pending.get(uid, known[uid]),
                text_catalog=getattr(host, 'text_catalog', None)))
    return projected


def _project_states(host, pending, projected, timings=None):
    host._warehouse_pending_state_changes, host._warehouse_all_items = pending, projected
    host._apply_warehouse_filters(**({"timings": timings} if timings is not None else {}))
    host._update_warehouse_save_state()
    refresh_conflict_summary(host)


def invalidate_management_preview(host):
    owner = host.warehouse_read_controller
    guard = owner.recommendation_guard or getattr(owner, "conflict_guard", None)
    if guard is None:
        return
    try:
        guard.require_current()
        return
    except ValueError:
        pass
    pending = {uid: state for uid, state in host._warehouse_pending_state_changes.items()
               if owner.manual_targets.get(uid) == state}
    projected = _state_projection(host, pending)
    owner.recommendation_guard, owner.recommendation_targets, owner.conflicts = None, {}, []
    owner.conflict_guard = None
    owner.management_issues = []
    _project_states(host, pending, projected)


def project_management_preview(host, expected, result, guard):
    if capture_management_input(host) != expected or guard.snapshot_id != expected.snapshot_id:
        raise ValueError("仓库人工草稿或快照已变化，请重新生成")
    timings = getattr(guard, "timings", None)
    probe = None
    if timings is not None:
        context = host.app_context
        identity = context.account.active_account_id, context.generation
        probe = begin_gui_timing(host.warehouse_view.viewport(), timings,
            is_current=lambda: identity == (context.account.active_account_id, context.generation))
    timed_call(timings, "gui.guard", guard.require_current, **({"timings": timings} if timings is not None else {}))
    targets = preview_targets(result)
    owner = host.warehouse_read_controller
    manual = dict(expected.manual_targets)
    for uid, state in targets.items():
        if uid in manual and manual[uid] != state:
            raise ValueError("生成结果改变了已冻结的人工目标")
    generated = {uid: state for uid, state in targets.items() if uid not in manual}
    # 只采用首阶段已批准的人工目标。受阻/未知目标仍定案，但不进入待保存集合。
    pending = dict(targets)
    guard.require_targets(pending)
    projected = timed_call(timings, "gui.state_projection", _state_projection, host, pending)
    # 全部门禁通过后再一次替换，失败不提交半份状态或冲突证据。
    owner.cancel_quality()
    owner.recommendation_guard, owner.recommendation_targets = guard, generated
    owner.conflicts = [row for row in result.get("conflicts", ()) if row["uid"] not in manual]
    owner.conflict_guard = guard
    owner.management_issues = [
        dict(row) for row in result.get("rows", ()) if row.get("decision_status") in {"unknown", "blocked"}
    ]
    _project_states(host, pending, projected, timings=timings)
    hint = getattr(host, "warehouse_hint", None)
    limitations = list(result.get("limitations", ()))
    if any(row["uid"] in manual and "state_protected" in row.get("reasons", ())
           for row in owner.management_issues):
        limitations.append(host.text_catalog.render("warehouse.management.discard_protected"))
    if hint is not None and limitations:
        hint.setText("\n".join(limitations))
        hint.show()
    if probe is not None:
        probe.submitted()
    score = result.get("warehouse_score")
    if score is not None and owner.score_label is not None:
        owner.score_label.setText(
            host.text_catalog.render("warehouse.management.projected_score", score=f"{score:.2f}")
        )
