# 毕业目标草稿复用冻结成长规则与已证实落位的图纸；理论身份不携带真实UID或库存可达含义。
from dataclasses import dataclass

from src.domain.equipment.geometry.native_layout import validate_native_layout
from src.domain.equipment.loadouts.virtual_equipment import is_virtual_equipment_assignment
from src.domain.teams.graduation.axis_graduation_rules import QUALITY_NAMES, TheoryEquipmentRules
from src.domain.teams.graduation.axis_graduation_target import (
    GraduationTarget,
    TargetLayout,
    TargetMember,
    TargetPiece,
    TargetPreset,
    TargetWorldBonus,
)
from src.domain.teams.models import Team


@dataclass(frozen=True, slots=True)
class LayoutTemplate:
    character_id: int
    slot_id: int
    plan_id: int | None
    label: str
    layout: TargetLayout | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ConfigurationMember:
    name: str
    default: TargetMember
    growth: tuple[tuple[int, int], ...]
    skill_levels: tuple[tuple[str, tuple[tuple[int, int, int], ...]], ...]
    preferred_slot_id: int | None


@dataclass(frozen=True, slots=True)
class GraduationConfiguration:
    account_id: str
    generation: int
    team: Team
    rules: TheoryEquipmentRules
    target_id: str
    revision: int
    members: tuple[ConfigurationMember, ...]
    templates: tuple[LayoutTemplate, ...]
    world: TargetWorldBonus = TargetWorldBonus(20, .04)


def layout_from_saved_items(*, character_id, items, assignments, cells, shapes, rules):
    """正式保存方案的target_row/column和静态x/y均为同一1-based锚点口径，不转置或重摆盘。"""
    if any(is_virtual_equipment_assignment(a.get("raw_assignment") or a) for a in assignments):
        raise ValueError("图纸含理论占位，请先保存完整真实方案")
    by_uid = {(i["uid_slot"], i["uid_serial"]): i for i in items}
    if len(by_uid) != len(items) or len(assignments) != len(items):
        raise ValueError("图纸装备身份或数量不一致")
    assigned = {(a["uid_slot"], a["uid_serial"]) for a in assignments}
    if assigned != set(by_uid) or len(assigned) != len(assignments):
        raise ValueError("图纸未完整引用其冻结装备")
    cores = [i for i in items if i["kind"] == "core"]
    if len(cores) != 1:
        raise ValueError("图纸必须有且仅有一张真实卡带")
    core = cores[0]
    specs = {i.item_id: i for i in rules.items}
    if any(i["item_id"] not in specs for i in items):
        raise ValueError("图纸包含理论词条规则尚未确认的物品")
    for item in items:
        spec = specs[item["item_id"]]
        geometry = item.get("geometry")
        if geometry and not geometry.startswith("EquipmentGeometry_"):
            geometry = "EquipmentGeometry_" + geometry
        if (item["kind"] != spec.kind or QUALITY_NAMES.get(str(item["quality"]).casefold()) != spec.quality
                or item.get("suit_id") != spec.suit_id or spec.kind == "module" and (
                    geometry != spec.geometry_id or item.get("grid_count") != spec.grid_count)):
            raise ValueError("图纸物品身份与正式形状/品质约束冲突")
    board_cells = {(c["row"], c["column"]) for c in cells}
    if (len(board_cells) != 20 or len(cells) != 20
            or any(type(v) is not int or not 1 <= v <= 5 for p in board_cells for v in p)):
        raise ValueError("图纸缺少完整正式底盘")
    board = [[None if (r+1, c+1) in board_cells else "XX" for c in range(5)] for r in range(5)]
    drives, placed, modules = [], [], []
    for assignment in assignments:
        uid = assignment["uid_slot"], assignment["uid_serial"]
        item = by_uid[uid]
        if assignment["kind"] != item["kind"]:
            raise ValueError("图纸装备类型冲突")
        if item["kind"] == "core":
            continue
        if assignment.get("rotation") != 0:
            raise ValueError("图纸旋转未形成正式形状身份")
        row, col = assignment.get("target_row"), assignment.get("target_column")
        if any(type(v) is not int or not 1 <= v <= 5 for v in (row, col)):
            raise ValueError("图纸缺少已确认的正式锚点")
        shape = specs[item["item_id"]].geometry_id
        offsets = (shapes.get(shape) or {}).get("cells") or []
        if not offsets:
            raise ValueError("图纸缺少正式形状坐标")
        for cell in offsets:
            pos = row + cell["x"], col + cell["y"]
            if pos not in board_cells:
                raise ValueError("图纸驱动超出正式底盘")
            board[pos[0]-1][pos[1]-1] = shape
        drives.append({"uid": uid, "shape_id": shape})
        placed.append({"uid": uid, "shape_id": shape, "target_row": row, "target_column": col})
        modules.append(TargetPiece(f"{character_id}-piece-{len(modules)+1}", item["item_id"], row, col))
    core_uid = core["uid_slot"], core["uid_serial"]
    state = {"blueprint_layout": board, "equipped_tape": {"uid": core_uid}, "equipped_drives": drives,
             "native_layout": {"version": 1, "tape_uid": core_uid, "drives": placed}}
    # 复用公共占用集合校验，重叠、遗漏格和形状面积均不被保存方案的ready标签豁免。
    validate_native_layout(state, by_uid, shapes, parse_uid=lambda uid, **_kwargs: uid)
    return TargetLayout(character_id, f"{character_id}-core", core["item_id"], tuple(modules), 0, ())


def make_configuration_target(configuration, *, members, slot_ids, world):
    if not isinstance(configuration, GraduationConfiguration) or not isinstance(world, TargetWorldBonus):
        raise ValueError("目标草稿缺少本次冻结配置")
    slot_ids = tuple(slot_ids)
    members, selected = tuple(members), dict(slot_ids)
    if (len(members) != 4 or len(selected) != 4 or len(tuple(slot_ids)) != 4
            or tuple(m.character_id for m in members) != configuration.team.character_ids):
        raise ValueError("目标草稿需要同序四人和四份明确图纸")
    layouts = []
    for member, source in zip(members, configuration.members):
        default = source.default
        if ((member.character_level, member.breakthrough_stage) not in source.growth
                or member.fork != default.fork or member.awakening_level != default.awakening_level
                or member.selected_awaken_effect_ids != default.selected_awaken_effect_ids):
            raise ValueError("目标成长无效；本次编辑冻结当前觉醒/弧盘，不静默扩大养成")
        choices = dict(source.skill_levels)
        if set(dict(member.skill_levels)) != set(choices):
            raise ValueError("目标技能必须使用正式SkillID集合")
        for skill, level in member.skill_levels:
            if not any(n == level and stage <= member.breakthrough_stage and awake <= member.awakening_level
                       for n, stage, awake in choices[skill]):
                raise ValueError("目标技能等级或突破/觉醒前置未满足")
        template = next((v for v in configuration.templates if v.character_id == member.character_id
                         and v.slot_id == selected[member.character_id]), None)
        if template is None or template.layout is None:
            raise ValueError("请选择已确认完整落位的计算图纸")
        layouts.append(template.layout)
    rules = configuration.rules
    return GraduationTarget(configuration.target_id, configuration.team.team_id, configuration.revision,
        configuration.team.character_ids, rules.dataset_id, rules.static_schema, rules.static_sha256,
        rules.scoring_rule_sha256, world, (TargetPreset("configured", members, tuple(layouts)),),
        ("fixed_selected_saved_layout_v1",))


def configuration_seeds(target, rules):
    """只生成合法满值起点，不读库存、不按收益猜必选词条，不将起点声称为最优。"""
    target.verify_rules(rules)
    specs = {v.item_id: v for v in rules.items}
    result = []
    for preset in target.presets:
        actors = []
        for layout in preset.layouts:
            pieces = []
            for pid, iid in ((layout.core_piece_id, layout.core_item_id), *((p.piece_id, p.item_id) for p in layout.pieces)):
                spec = specs.get(iid)
                if spec is None:
                    raise ValueError("目标物品不属于本次完整词条规则")
                pool = rules.core_sub_stats if spec.kind == "core" else rules.drive_sub_stats
                if len(pool) < spec.sub_stat_count:
                    raise ValueError("合法不同副词条池不足，不复制属性补预算")
                pieces.append({"piece_id": pid, "item_id": iid,
                    "main_property_id": rules.core_main_stats[0].property_id if spec.kind == "core" else None,
                    "sub_property_ids": [v.property_id for v in pool[:spec.sub_stat_count]]})
            actors.append({"character_id": layout.character_id, "pieces": pieces})
        result.append({"preset_id": preset.preset_id, "seeds": [actors]})
    return result
