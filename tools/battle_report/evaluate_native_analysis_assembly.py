# 用公共战报分析用例验证原生全轴装配，逐字段保留未知和展示依据。
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.services.battle_report.build.offline_analysis import BattleCounterfactualAnalysisService  # noqa: E402
from src.services.battle_report.environment.conditions import resolve_battle_target_condition  # noqa: E402
from src.services.battle_report.characters.linko_inference import BattleLinkoCoattackInferenceService  # noqa: E402
from tools.battle_report.evaluate_native_application_parts import wire, numeric_wire  # noqa: E402
from tools.battle_report.evaluate_native_analysis import compare_results  # noqa: E402


def stable_role_ties(value):
    """Python set tie order is unspecified; only normalize equal-damage roles."""
    if isinstance(value, dict):
        return {key: (sorted(item, key=lambda r: (-r['damage'], r['character_id']))
                      if key == 'roles' and isinstance(item, list) else stable_role_ties(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [stable_role_ties(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = []
    python_mock_only = []
    original = BattleCounterfactualAnalysisService.analyze
    signature = inspect.signature(original)

    def analyze(**kwargs):
        expected = original(**kwargs)
        if isinstance(BattleLinkoCoattackInferenceService.infer, Mock):
            python_mock_only.append('injected_linko_result_tests_python_call_order_only')
            return expected
        bound = signature.bind(**kwargs)
        bound.apply_defaults()
        values = bound.arguments
        config = {key: wire(values.get(source)) for key, source in (
            ('zankou', 'zankou_form_config'), ('shinku', 'shinku_rage_config'),
            ('outer', 'outer_realm_buff_config'))}
        payload = {key: wire(values[key]) for key in (
            'evidence', 'build', 'animation_candidates', 'character_elements',
            'requested_start_us', 'requested_end_us', 'critical_events',
            'infer_buffs', 'target_control_policy')}
        payload.update(
            record={'battle_record_id': values['battle_record_id'],
                    'evidence_capability_level': values['capability_level']},
            rules=wire(values['buff_rules']), config=config,
            target_condition=wire(resolve_battle_target_condition(values['target_condition'])),
        )
        run = subprocess.run([str(args.executable)], input=json.dumps(
            {'operation': 'assemble', 'input': payload}, ensure_ascii=False).encode(),
            capture_output=True, timeout=60, check=False)
        if run.returncode:
            raise AssertionError(run.stderr.decode()[:300])
        actual = json.loads(run.stdout)
        comparison = compare_results(stable_role_ties(numeric_wire(wire(expected))),
                                     stable_role_ties(numeric_wire(actual)))
        cases.append(comparison)
        if comparison['difference_count']:
            (args.output.parent / f'assembly-failure-{len(cases)}.json').write_text(
                json.dumps({'input': payload, 'expected': wire(expected), 'actual': actual},
                           ensure_ascii=False, indent=2), encoding='utf-8')
            raise AssertionError(str(comparison['differences'][:5]))
        return expected

    args.output.parent.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'tests.battle_report.build.test_battle_counterfactual_analysis_service',
        'tests.test_battle_overkill_analysis_service',
        'tests.battle_report.targets.test_vital_analysis',
    ])
    with patch.object(BattleCounterfactualAnalysisService, 'analyze', side_effect=analyze):
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    args.output.write_text(json.dumps({'successful': result.wasSuccessful(),
                                     'tests': result.testsRun, 'cases': cases,
                                     'python_mock_only': python_mock_only}, indent=2), encoding='utf-8')
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
