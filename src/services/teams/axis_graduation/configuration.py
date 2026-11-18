# 从本账号当前证据与同身份主静态冻结目标编辑材料；读取不保存目标、不求解或写游戏。
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from src.domain.equipment.properties.equipment_normalizer import equipment_quality_coefficient
from src.domain.teams.graduation.axis_graduation_configuration import (
    ConfigurationMember,
    GraduationConfiguration,
    LayoutTemplate,
    layout_from_saved_items,
)
from src.domain.teams.graduation.axis_graduation_rules import compile_theory_equipment_rules
from src.domain.teams.graduation.axis_graduation_target import TargetFork, TargetMember
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.inputs.context_builder import read_allocation_scoring_rules
from src.services.equipment.loadouts.frozen_loadout import resolve_plan_inventory_items
from src.services.teams.axis_graduation.requests import actual_component_input
from src.storage.sqlite.user_data_dao import UserDataDao


def load_graduation_configuration(inputs, team, *, scoring_rules_path, cancelled=lambda: False):
    check = lambda: inputs.checkpoint(cancelled)
    check()
    current = inputs.freeze_current(team, cancelled=cancelled)
    actual = actual_component_input(current)
    scoring = read_allocation_scoring_rules(scoring_rules_path)
    with static_catalog_read_unit(inputs.static_database_path) as unit:
        release = unit.release
        if release.scope != "game" or (release.dataset_id, release.schema_version, release.sha256.lower()) != (
                current.dataset_id, current.static_schema, current.static_sha256):
            raise ValueError("目标编辑期间主静态已变化，请重新读取")
        rules = compile_theory_equipment_rules(scoring_rules=scoring,
            quality_coefficients={q: equipment_quality_coefficient(q) for q in ("Gold", "Purple", "Blue")},
            attributes=unit.dao.list_equipment_attributes(), equipment_items=unit.dao.list_equipment_items(),
            dataset_id=release.dataset_id, static_schema=release.schema_version, static_sha256=release.sha256.lower(), checkpoint=check)
        shapes = {s["shape_id"]: s for s in unit.dao.list_shapes()}
        members, templates = [], []
        if not Path(inputs.database_path).is_file():
            raise ValueError("目标编辑的原账号库已退出")
        with UserDataDao(inputs.database_path) as dao, dao.read_consistent_state():
            if dao.profile()["account_id"] != inputs.account_id or team not in dao.list_teams():
                raise ValueError("目标编辑的账号或队伍已变化")
            previous = dao.get_team_axis_target(team.team_id)
            slots = dao.list_visible_loadout_slots_with_plans()
            snapshots = {}
            for actor, source in zip(actual["build"]["characters"], current.members):
                check()
                cid, profile = actor["character_id"], actor["profile"]
                growth = tuple(sorted({(r["level"], r["breakthrough_stage"]) for r in unit.dao.list_character_panel_growth(cid)}))
                if not growth:
                    raise ValueError(f"角色 {cid} 缺少正式成长档位")  # ui-text: internal
                level, stage = max(growth)
                skill_rows = unit.dao.list_character_skill_level_requirements(cid)
                choices, defaults = [], []
                for skill in sorted(profile["skill_levels"]):
                    rows = tuple((r["level"], r["required_breakthrough_stage"], r["required_awaken_level"])
                                 for r in skill_rows if r["skill_id"] == skill)
                    if any(type(v) is not int or v < 0 for row in rows for v in row):
                        raise ValueError(f"角色 {cid} 的技能 {skill} 等级前置证据不完整")  # ui-text: internal
                    valid = [n for n, b, a in rows if b <= stage and a <= profile["awakening_level"]]
                    if not valid:
                        raise ValueError(f"角色 {cid} 的技能 {skill} 缺少可确认的合法目标等级")  # ui-text: internal
                    choices.append((skill, rows))
                    defaults.append((skill, max(valid)))
                fork = (TargetFork(profile["fork_id"], profile["fork_level"], profile["fork_breakthrough_stage"],
                                   profile["fork_refinement_level"]) if profile["fork_id"] is not None else None)
                member = TargetMember(cid, level, stage, profile["awakening_level"],
                    tuple(profile["selected_awaken_effect_ids"]), True, fork, tuple(defaults))
                name = (unit.dao.get_character(cid) or {}).get("name_zh") or str(cid)
                members.append(ConfigurationMember(name, member, growth, tuple(choices), source.slot_id))
                board = unit.dao.get_equipment_plan(cid)
                for slot in slots:
                    if slot["character_id"] != cid:
                        continue
                    plan, layout, reason = slot["current_plan"], None, ""
                    try:
                        if not plan or plan["status"] != "ready" or plan["source_snapshot_id"] is None:
                            raise ValueError("尚无完整已保存方案")
                        sid = plan["source_snapshot_id"]
                        if sid not in snapshots:
                            snapshots[sid] = dao.inventory_snapshot_summary(sid), dao.list_inventory_items(sid)
                        summary, inventory = snapshots[sid]
                        if not summary or not summary["complete"] or not board:
                            raise ValueError("方案来源或正式底盘缺失")
                        items = resolve_plan_inventory_items(dao, plan, snapshot_items=inventory)
                        layout = layout_from_saved_items(character_id=cid, items=items, assignments=plan["assignments"],
                            cells=board["cells"], shapes=shapes, rules=rules)
                    except (ValueError, KeyError, TypeError) as error:
                        reason = str(error) if isinstance(error, ValueError) else "图纸字段不完整或格式错误"
                    templates.append(LayoutTemplate(cid, slot["slot_id"], plan["plan_id"] if plan else None,
                        slot["slot_name"], layout, reason))
            # 默认优先当前来源同槽，否则明确展示首个可用模板，不混入真实装备UID。
            for n, member in enumerate(members):
                valid = [t for t in templates if t.character_id == member.default.character_id and t.layout is not None]
                if not any(t.slot_id == member.preferred_slot_id for t in valid):
                    members[n] = replace(member, preferred_slot_id=valid[0].slot_id if valid else None)
        check()
    check()
    return GraduationConfiguration(inputs.account_id, inputs.generation, team, rules,
        previous.target_id if previous else "axis-target-"+uuid4().hex, (previous.revision if previous else 0)+1,
        tuple(members), tuple(templates))
