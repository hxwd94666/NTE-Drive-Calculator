# 将本次冻结计划按真实保存入口的字段组装为专项行，不从当前配装补齐。
from src.services.allocation.direct.direct_postprocess import optimization_payload
from src.services.allocation.results.plan_save import legacy_plan_tape_main_values
from src.services.allocation.results.postprocess_facts import allocation_role_state_from_plan


def enhancement_rows(plans, roles_and_ids, snapshot):
    rows = []
    for name, character_id in roles_and_ids:
        plan = plans[name]
        items = [plan["assigned_tape"], *plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]
        rows.append(dict(role_name=name, character_id=character_id, snapshot_id=snapshot,
            name="冻结队伍增强", score=plan["score"], role_state=allocation_role_state_from_plan(plan), slot_id=None,
            axis_preview_source=plan.get("axis_preview_source"), baseline_preview_source=plan.get("baseline_preview_source"),
            team_enhancement_preview=plan.get("team_enhancement_preview"), payload={"source_role_name": name,
                "assignment_scores": {item.uid: item.role_scores[name] for item in items},
                "tape_main_values": legacy_plan_tape_main_values(plan), **optimization_payload(plan)}))
    return rows
