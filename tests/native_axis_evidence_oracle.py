# 从公开行为回归生成原生证据层差分夹具，所有输出仅写验证目录。
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def serialized(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): serialized(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialized(v) for v in value]
    return value


def generate(output: Path):
    from src.services.battle_report.targets.vital_analysis import BattleTargetVitalAnalysisService
    from src.services.battle_report.targets.hp_pool import BattleTargetHpPoolReconciliationService
    from src.services.battle_report.build.offline_analysis import BattleCounterfactualAnalysisService
    from src.services.battle_report.timeline.stop_projection import BattleTimeStopProjectionService
    from src.services.skill_name_rendering_service import SkillNameRenderingService

    fixtures = []
    project_inputs = []
    analyze_oracle = BattleCounterfactualAnalysisService.analyze
    catalogs = {}
    initialize = SkillNameRenderingService.__init__
    render = SkillNameRenderingService.render_axis_identity

    def capture_initialize(instance, *args, **kwargs):
        values = inspect.signature(initialize).bind(instance, *args, **kwargs)
        values.apply_defaults()
        catalog = {key: serialized(tuple(value)) for key, value in values.arguments.items() if key != "self"}
        catalogs[id(instance)] = catalog
        return initialize(instance, *args, **kwargs)

    def capture_render(instance, *args, **kwargs):
        result = render(instance, *args, **kwargs)
        values = inspect.signature(render).bind(instance, *args, **kwargs)
        values.apply_defaults()
        values.arguments.pop("self")
        fixtures.append({"kind": "identity", "input": {
            "catalog": catalogs[id(instance)], "row": dict(values.arguments),
        }, "expected": serialized(result)})
        return result

    def capture(original, kind):
        signature = inspect.signature(original)

        def call(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            inputs = deepcopy(serialized(dict(arguments.arguments)))
            result = original(*args, **kwargs)
            expected = serialized(result)
            if kind == "project":
                project_inputs.append(deepcopy(dict(arguments.arguments)))
                return result
            fixtures.append({"kind": kind, "input": inputs, "expected": expected})
            return result
        return call

    targets = (
        (BattleTargetVitalAnalysisService, "derive", "derive"),
        (BattleTargetVitalAnalysisService, "estimate_from_descriptions", "estimate"),
        (BattleTargetHpPoolReconciliationService, "reconcile", "pool"),
        (BattleTimeStopProjectionService, "observed_typed_intervals", "observed"),
        (BattleTimeStopProjectionService, "resolve", "clock"),
        (BattleCounterfactualAnalysisService, "analyze", "project"),
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(SkillNameRenderingService, "__init__", capture_initialize))
        stack.enter_context(patch.object(SkillNameRenderingService, "render_axis_identity", capture_render))
        for owner, method, kind in targets:
            stack.enter_context(patch.object(owner, method, capture(getattr(owner, method), kind)))
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            "tests.battle_report.targets.test_vital_analysis",
            "tests.battle_report.targets.test_vital_identity",
            "tests.battle_report.targets.test_vital_v4_fadia",
            "tests.battle_report.targets.test_hp_pool_reconciliation",
            "tests.battle_report.build.test_battle_counterfactual_analysis_service",
            "tests.battle_report.timeline.test_stop_projection",
            "tests.test_skill_name_rendering_service",
        ])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Public oracle regressions failed")
    from tests.battle_report.characters.linko_fixtures import _qte, _lte_aoe
    qte, _action = _qte()
    linko_rows = [{
        "sequence_order": hit.sequence, "relative_time_us": hit.relative_time_us,
        "character_id": hit.character_id, "character_name": hit.character_name,
        "direction": hit.direction, "damage": hit.damage, "follow_up_damage": 0,
        "ability_name": hit.ability_id, "gameplay_effect_name": hit.gameplay_effect_id,
        "damage_name": hit.damage_name, "damage_component": hit.damage_component,
        "attack_type": hit.attack_type, "damage_attribute": hit.damage_attribute,
        "target_id": hit.target_id,
    } for hit in (qte, _lte_aoe())]
    project_inputs.append(dict(
        battle_record_id=1, evidence={"hits": linko_rows, "axis_complete": True},
        build=None, capability_level="hit_axis", infer_buffs=False,
        character_elements={1036: "CHARACTER_ELEMENT_TYPE_INCANTATION"},
    ))
    # Some public tests deliberately mock child inference; rerun their frozen inputs
    # after all mocks have ended so expected output is the actual Python algorithm.
    for arguments in project_inputs:
        result = serialized(analyze_oracle(**arguments))
        expected = {
            "hits": result["timeline_hits"],
            "max_hp_events": result["timeline_max_hp_events"],
            "estimated_max_hp_events": result["timeline_estimated_max_hp_events"],
            "actions": result["inferred_actions"],
            "linko_inferences": result["linko_coattack_inferences"],
            "inferred_inputs": result["inferred_inputs"],
            "timeline_groups": result["timeline_damage_groups"],
            "range_summary": {key: result[key] for key in (
                "battle_start_us", "battle_end_us", "range_start_us", "range_end_us",
                "duration_seconds", "total_damage", "total_dps", "raw_total_damage",
                "damage_correction_total", "damage_overlap_correction_total",
                "max_hp_reduction_damage", "effective_damage", "hits", "max_hp_events",
                "estimated_max_hp_events", "roles", "skills", "targets",
            )},
        }
        fixtures.append({"kind": "project", "input": serialized(arguments), "expected": expected})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixtures, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"fixture_count={len(fixtures)}")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
