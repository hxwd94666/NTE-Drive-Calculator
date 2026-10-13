# 从公开目标行为用例生成原生环境与实例映射差分夹具。
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def serialized(value):
    if is_dataclass(value):
        return serialized(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, SimpleNamespace):
        return serialized(vars(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): serialized(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [serialized(v) for v in value]
    return value


def generate(output: Path):
    from src.services.battle_report.targets.encounter.candidate_selection import BattleEncounterCandidateSelectionService as Selection
    from src.services.battle_report.targets.inferred_condition import BattleInferredTargetConditionService as Inference
    from src.services.battle_report.targets.instance_mapping import BattleTargetInstanceMappingService as Mapping
    import src.services.battle_report.targets.resolution as support
    import src.services.battle_report.environment.outer_realm_period as period
    import src.services.battle_report.environment.outer_realm_confirmation as confirmation
    from src.services.battle_report.targets.incoming_identity import BattleIncomingMonsterIdentityService
    from src.services.battle_report.targets.inferred_snapshot import BattleInferredTargetSnapshotService as Snapshot
    from src.services.battle_report.targets.encounter.fit_projection import BattleEncounterFitProjectionService as Fit
    from tools.native_analysis.export_target_catalog import export_catalog

    fixtures = []
    catalog = export_catalog(ROOT / "data/game_static.sqlite3")

    def capture(original, kind, mutate=False):
        signature = inspect.signature(original)

        def call(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            inputs = deepcopy(serialized(dict(bound.arguments)))
            result = original(*args, **kwargs)
            if kind == "infer":
                inputs["has_catalog"] = inputs.pop("static_database_path") is not None
            else:
                inputs.pop("static_database_path", None)
            expected = serialized(bound.arguments["evidence"] if mutate else result)
            if kind == "period" and result is not None:
                expected.update(display_label=result.display_label, inference_basis=result.inference_basis)
            fixtures.append({"kind": kind, "input": inputs, "expected": expected})
            return result
        return call

    targets = (
        (Selection, "observe", "observe", False),
        (Selection, "strict_matches", "strict", False),
        (Inference, "infer", "infer", False),
        (Inference, "condition_for_candidate", "condition", False),
        (Inference, "select_residual_candidate", "select_residual", False),
        (Inference, "apply_residual_resolution_metadata", "residual_metadata", False),
        (Mapping, "resolve", "resolve", False),
        (support, "resolve_available_target_instances", "available", False),
        (support, "inferred_mapping_condition", "mapping_condition", False),
        (support, "project_inferred_target_evidence", "project_inferred", True),
        (support, "project_resolved_target_evidence", "project_resolved", True),
        (period, "resolve_outer_realm_period", "period", False),
        (Snapshot, "restore", "restore", False),
        (confirmation, "complete_outer_realm_confirmation", "confirmation", False),
    )
    original_fit = Fit.select

    def capture_fit(inferred, *, project_candidate, group_analysis=None, backend=None, checkpoint=None):
        analyses = {}

        def project(candidate):
            analysis = project_candidate(candidate)
            analyses[candidate.environment_ref] = serialized(analysis)
            return analysis

        result = original_fit(inferred, project_candidate=project, group_analysis=group_analysis,
                              backend=backend, checkpoint=checkpoint)
        fixtures.append({"kind": "fit", "input": {"inferred": serialized(inferred),
            "group_analysis": serialized(group_analysis), "analyses": analyses}, "expected": serialized(result)})
        return result

    with ExitStack() as stack:
        for owner, name, kind, mutate in targets:
            stack.enter_context(patch.object(owner, name, new=capture(getattr(owner, name), kind, mutate)))
        stack.enter_context(patch.object(Fit, "select", new=capture_fit))
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
            "tests.battle_report.targets.encounter.test_candidate_selection",
            "tests.battle_report.targets.test_inferred_condition",
            "tests.battle_report.targets.test_inferred_resolution",
            "tests.battle_report.targets.test_instance_mapping",
            "tests.battle_report.environment.test_outer_realm_period",
            "tests.battle_report.targets.encounter.test_fit_projection",
        ))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            raise SystemExit("public target oracle failed")
        import tests.battle_report.environment.test_outer_realm_confirmation as confirmation_tests
        for name in dir(confirmation_tests):
            if name.startswith("test_"):
                getattr(confirmation_tests, name)()

    # Explicitly cover wire-handle alias precedence and incoming GE disagreement.
    from src.domain.battle_encounter import BattleObservedTarget
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    observed = (BattleObservedTarget("", "synthetic-target", "", 100.0, 0),)
    with StaticGameDataDao(ROOT / "data/game_static.sqlite3") as dao:
        for effect in (row for row in catalog["gameplay_effects"] if "/monster/" in str(row["class_path"]).casefold()):
            for name in (effect["gameplay_effect_id"], "wrong-ge-id", ""):
                evidence = {"hits": [{"direction": "incoming", "gameplay_effect_index": effect["gameplay_effect_index"], "gameplay_effect_name": name}]}
                expected = BattleIncomingMonsterIdentityService.supplement(observed, evidence, dao)
                fixtures.append({"kind": "supplement", "input": {"observed": serialized(observed), "evidence": evidence}, "expected": serialized(expected)})
            break
    for occurred in ("2026-09-04T08:00:00+00:00", "20260904T080000Z", "2026-09-04",
                     "2026-W36-5T08:00:00Z", "2026W365T08:00:00Z", "2026-W36",
                     "2026-09-04界08:00:00+00:00", "2026-09-04T16:00:00+08:00",
                     "2026-09-04T00:00:00-08:00", "2026-09-04T00::00",
                     "2026-09-04T08:00:00.", "2026-02-30T00:00:00"):
        result_period = period.resolve_outer_realm_period(catalog["outer_realm_configs"], occurred)
        expected = serialized(result_period)
        if result_period is not None:
            expected.update(display_label=result_period.display_label, inference_basis=result_period.inference_basis)
        fixtures.append({"kind": "period", "input": {"configs": catalog["outer_realm_configs"], "battle_occurred_at_utc": occurred}, "expected": expected})
    from src.services.battle_report.targets.encounter.fit_projection import _fit_group_id
    hit = SimpleNamespace(event_id="synthetic-hit", character_id=None, gameplay_effect_id="GE_覆纹",
                          damage_attribute="incantation", scope_half="upper", target_id="synthetic-target")
    for value in (1.0, -0.0, 1e-9, 1e-4, 1e16, 2.675):
        replay = SimpleNamespace(factors=(SimpleNamespace(factor_id="state_coefficient", value=value),))
        fixtures.append({"kind": "fit_group", "input": {"hit": serialized(hit), "replay": serialized(replay),
            "applied": ["甲", "β"]}, "expected": _fit_group_id(hit, replay, ("甲", "β"))})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"catalog": catalog, "fixtures": fixtures}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"fixtures": len(fixtures), "public_tests": result.testsRun}))


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
