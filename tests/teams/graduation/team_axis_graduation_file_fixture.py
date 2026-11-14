# 仅在新建build隔离目录生成合成账号战报与显式毕业目标，供独立Rust的真实Context专项读取。
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from src.domain.equipment.properties.equipment_normalizer import equipment_quality_coefficient
from src.domain.teams.graduation.axis_graduation_target import (
    GraduationTarget,
    TargetLayout,
    TargetMember,
    TargetPiece,
    TargetPreset,
    TargetWorldBonus,
    encode_graduation_target,
)
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.inputs.context_builder import read_allocation_scoring_rules
from src.services.teams.axis_graduation.rules import freeze_theory_equipment_rules
from tests.allocation.axis_team_fixture import attach_fixed_builds, attach_native_team
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case


ROOT = Path(__file__).resolve().parents[3]
IDS = (1003, 1072, 1004, 1008)
KIND = "synthetic_team_axis_graduation_files_v1"


def _hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def create_fixture(output):
    output = Path(output).resolve()
    build = (ROOT / "build").resolve()
    if build not in output.parents or output.exists():
        raise ValueError("专项只接受build内全新目录")
    output.mkdir(parents=True)
    case = create_axis_catalog_case(output, core_suit_id="Suit1", main_character_id=1003)
    attach_native_team(case, ids=IDS)
    attach_fixed_builds(case, ids=IDS)
    deps = case["dependencies"]
    scoring = read_allocation_scoring_rules(ROOT / "config/stats.json")
    rules = freeze_theory_equipment_rules(
        scoring_rules=scoring,
        quality_coefficients={key: equipment_quality_coefficient(key) for key in ("Gold", "Purple", "Blue")},
        static_database_path=deps.static_database_path,
    )
    specs = {item.item_id: item for item in rules.items}
    core = next(item for item in rules.items if item.kind == "core" and item.suit_id == "Suit1" and item.quality == "Gold")
    members, layouts, seed = [], [], []
    with static_catalog_read_unit(deps.static_database_path) as unit:
        for cid in IDS:
            growth = max(unit.dao.list_character_panel_growth(cid), key=lambda r: (r["level"], r["breakthrough_stage"]))
            skills = tuple((row["skill_id"], 1) for row in unit.dao.list_character_skills(cid))
            members.append(TargetMember(cid, growth["level"], growth["breakthrough_stage"], 0, (), False, None, skills))
            plan = unit.dao.get_equipment_plan(cid)
            pieces = tuple(TargetPiece(f"piece-{cid}-{index}", row["anchor_item_id"], row["row"], row["column"])
                           for index, row in enumerate(row for row in plan["cells"] if row["anchor_item_id"]))
            layout = TargetLayout(cid, f"core-{cid}", core.item_id, pieces, 0, ())
            layouts.append(layout)
            choices = []
            for piece_id, item_id in ((layout.core_piece_id, core.item_id), *((p.piece_id, p.item_id) for p in layout.pieces)):
                spec = specs[item_id]
                pool = rules.core_sub_stats if spec.kind == "core" else rules.drive_sub_stats
                choices.append({"piece_id": piece_id, "item_id": item_id,
                    "main_property_id": next((p.property_id for p in rules.core_main_stats if p.property_id == "AtkUp"), rules.core_main_stats[0].property_id)
                        if spec.kind == "core" else None,
                    "sub_property_ids": [p.property_id for p in pool[:spec.sub_stat_count]]})
            seed.append({"character_id": cid, "pieces": choices})
    presets = tuple(TargetPreset(key, tuple(members), tuple(layouts)) for key in ("actual-file-a", "actual-file-b"))
    target = GraduationTarget("file-target", "file-team", 1, IDS, rules.dataset_id,
        rules.static_schema, rules.static_sha256, rules.scoring_rule_sha256, TargetWorldBonus(0, 0), presets)
    target.verify_rules(rules)
    selector = {"account_id": deps.account_id, "generation": deps.generation,
        "battle_record_id": case["battle_record_id"], "user_database_path": str(deps.user_database_path),
        "static_database_path": str(deps.static_database_path), "semantics_path": str(ROOT / "config/gameplay_effect_semantics.json"),
        "detail_level": "hit", "_dataset_version": rules.dataset_id}
    # 数据库活库只用SQLite一致性备份。冻结变更用例独占副本，不修改基线合成库或正式data。
    copied = output / "freeze-user.sqlite3"
    with sqlite3.connect(deps.user_database_path) as original, sqlite3.connect(copied) as replica:
        original.backup(replica)
    reference_world = output / "reference-world-user.sqlite3"
    with sqlite3.connect(deps.user_database_path) as original, sqlite3.connect(reference_world) as replica:
        original.backup(replica)
    value = {"fixture_kind": KIND, "selector": selector,
        "freeze_selector": {**selector, "user_database_path": str(copied)},
        "reference_world_selector": {**selector, "user_database_path": str(reference_world)},
        "target": json.loads(encode_graduation_target(target)), "target_sha256": target.sha256,
        "rules": asdict(rules), "seeds": [{"preset_id": p.preset_id, "seeds": [seed]} for p in presets],
        "source_sha256": {key: _hash(selector[key]) for key in ("user_database_path", "static_database_path", "semantics_path")}}
    manifest = output / "fixture.json"
    manifest.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")
    # 只输出可核对的摘要和数量；不打印战报、装备、UID或完整规则正文。
    print(json.dumps({"fixture_kind": KIND, "manifest": str(manifest), "target_sha256": target.sha256,
        "roster_count": len(IDS), "preset_count": len(presets), "seed_pieces": [len(c["pieces"]) for c in seed]}))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    create_fixture(parser.parse_args().output)
