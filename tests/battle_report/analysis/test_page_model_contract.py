# 验证原生页面的唯一领域类型、必填未知值、浅冻结和首次解码导入边界。
from __future__ import annotations

from dataclasses import FrozenInstanceError, MISSING, fields
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from typing import get_args, get_type_hints

from src.domain import battle_page_models as models
from src.domain.battle_counterfactual import BattleMarginalResult
from src.domain.battle_encounter import BattleEncounterCandidateMatch, BattleEncounterTargetPreset
from src.integrations.native_battle_page_wire import decode, decode_page
from src.integrations.nte_analysis_core import NativeAnalysisError


def page_input():
    return {'analysis': None, 'target_catalog': None, 'target_catalog_error': None,
            'marginal_benefits': None, 'marginal_panel': None, 'candidate_display_analysis': None}


def derived_input():
    inferred = {
        'environment_kind': 'fixture', 'environment_ref': 'fixture-ref',
        'environment_name': '合成环境', 'source_kind': 'fixture', 'confidence': '未知',
        'inference_basis': '合成证据', 'scope_half': '', 'outer_realm_floor': None,
        'difficulty_id': None, 'feast_options': [], 'targets': [],
        'identities': [{'scope_half': '', 'captured_target_id': 'fixture-target',
                        'target_name': '合成目标', 'inferred_monster_id': 'fixture-monster',
                        'initial_max_hp': None}],
        'target_condition': None, 'algorithm_version': 'distinct-native-producer-revision',
        'fixture_extension': {'unknown': None},
    }
    return {'battle_record_id': 7, 'payload_schema_version': 1, 'static_schema_version': 31,
            'static_dataset_id': 'fixture-dataset', 'inference_status': 'resolved',
            'inferred_payload': inferred,
            **{key: inferred[key] for key in ('algorithm_version', 'environment_kind',
                'environment_ref', 'environment_name', 'source_kind', 'confidence')}}


class PageModelContractTests(unittest.TestCase):
    def test_nullable_required_identity_field_is_distinct_from_missing(self):
        raw = derived_input()['inferred_payload']['identities'][0]
        result = decode(raw, models.BattleInferredTargetIdentity)
        self.assertIsNone(result.initial_max_hp)
        self.assertIs(fields(models.BattleInferredTargetIdentity)[-1].default, MISSING)
        del raw['initial_max_hp']
        with self.assertRaises(NativeAnalysisError):
            decode(raw, models.BattleInferredTargetIdentity)

    def test_canonical_types_have_resolved_nested_annotations_and_original_defaults(self):
        panel = get_type_hints(models.BattleMarginalPanelResult)
        encounter = get_type_hints(models.BattleInferredEncounter)
        self.assertEqual(panel['results'], tuple[BattleMarginalResult, ...])
        self.assertEqual(encounter['targets'], tuple[BattleEncounterTargetPreset, ...])
        self.assertEqual(encounter['identities'], tuple[models.BattleInferredTargetIdentity, ...])
        self.assertEqual(encounter['formula_matches'], tuple[BattleEncounterCandidateMatch, ...])
        for cls in (models.BattleMarginalPanelResult, models.BattleReportAnalysisLoadResult,
                    models.BattleInferredEncounter, models.BattleInferredTargetIdentity,
                    models.BattleMarginalCandidate, models.BattleCoreMainStatCounterfactual):
            with self.subTest(model=cls.__name__):
                self.assertEqual(cls.__module__, 'src.domain.battle_page_models')
                self.assertTrue(cls.__dataclass_params__.frozen)
                self.assertEqual(tuple(field.name for field in fields(cls)), cls.__slots__)
        inferred = decode(derived_input()['inferred_payload'], models.BattleInferredEncounter)
        self.assertFalse(inferred.ambiguous)
        self.assertEqual(inferred.formula_matches, ())
        self.assertEqual(inferred.selection_mode, 'unique_hard')
        without_revision = derived_input()['inferred_payload']
        del without_revision['algorithm_version']
        self.assertEqual(decode(without_revision, models.BattleInferredEncounter).algorithm_version,
                         models.INFERRED_ENCOUNTER_ALGORITHM_VERSION)

    def test_result_preserves_required_positionals_and_shallow_frozen_semantics(self):
        with self.assertRaises(TypeError):
            models.BattleReportAnalysisLoadResult()
        catalog = {'unknown': None}
        result = models.BattleReportAnalysisLoadResult(None, catalog)
        self.assertIsNone(result.target_catalog_error)
        self.assertFalse(hasattr(result, '__dict__'))
        with self.assertRaises(FrozenInstanceError):
            result.target_catalog = None
        catalog['extension'] = 1
        self.assertEqual(result.target_catalog['extension'], 1)
        self.assertIs(type(decode_page(page_input())), models.BattleReportAnalysisLoadResult)

    def test_retained_services_construct_and_annotate_only_canonical_models(self):
        panel = importlib.import_module('src.services.battle_report.marginal.panel')
        load = importlib.import_module('src.services.battle_report.analysis.load')
        inferred = importlib.import_module('src.services.battle_report.targets.inferred_condition')
        for module, names in [(panel, ('BattleMarginalPanelResult', 'CHARACTER_PANEL_DYNAMIC_PROPERTIES')),
                              (load, ('BattleReportAnalysisLoadResult', 'BattleMarginalPanelResult')),
                              (inferred, ('BattleInferredEncounter', 'BattleInferredTargetIdentity',
                                          'INFERRED_ENCOUNTER_ALGORITHM_VERSION'))]:
            for name in names:
                self.assertFalse(hasattr(module, name), (module.__name__, name))
        self.assertIs(get_type_hints(panel.BattleMarginalPanelService.calculate)['return'], models.BattleMarginalPanelResult)
        self.assertIs(get_type_hints(load.BattleReportAnalysisLoadService.load)['return'], models.BattleReportAnalysisLoadResult)
        self.assertIn(models.BattleInferredEncounter,
                      get_args(get_type_hints(inferred.BattleInferredTargetConditionService.infer)['return']))
        with patch.object(panel.BattleMarginalCalculationService, 'calculate', return_value=()) as calculate:
            result = panel.BattleMarginalPanelService.calculate(
                analysis=SimpleNamespace(), character_id=7, drive_units={'AtkUp': 2, 'CritBase': 0.032}, projection_memo=Mock())
        self.assertIs(type(result), models.BattleMarginalPanelResult)
        units = calculate.call_args.kwargs['units']
        self.assertEqual(units['AtkUp'], 2.0)
        self.assertEqual(units['CritBase'], 0.032)
        self.assertTrue(models.CHARACTER_PANEL_DYNAMIC_PROPERTIES <= units.keys())
        self.assertTrue(all(units[key] == 0.0 for key in
                            models.CHARACTER_PANEL_DYNAMIC_PROPERTIES - {'AtkUp', 'CritBase'}))
        baseline_field = next(field for field in fields(load.BattleReportAnalysisLoadRequest)
                              if field.name == 'comparison_baseline')
        self.assertFalse(baseline_field.compare)
        self.assertFalse(baseline_field.repr)

    def test_each_fresh_decode_succeeds_when_legacy_services_and_storage_are_blocked(self):
        # 子进程执行真实 decoder，不预加载旧 Service，不伪造模块或 DTO。
        code = '''
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
sys.path.extend(json.loads(sys.argv[2]))
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'src.services' or fullname.startswith(('src.services.', 'src.storage', 'src.optimizer', 'src.features', 'src.ui')):
            raise ImportError('blocked actual legacy import: ' + fullname)
sys.meta_path.insert(0, Boundary())
from src.integrations.native_battle_page_wire import decode_page, decode_derived_snapshot
raw = json.loads(sys.stdin.read())
if sys.argv[3] == 'page':
    result = decode_page(raw)
    assert type(result).__module__ == 'src.domain.battle_page_models'
else:
    result = decode_derived_snapshot(raw, battle_record_id=7, dataset_version='fixture-dataset')
    assert result['inferred_payload'] is raw['inferred_payload']
    assert result['inferred_payload']['identities'][0]['initial_max_hp'] is None
    assert result['algorithm_version'] == 'distinct-native-producer-revision'
assert not any(name == 'src.services' or name.startswith(('src.services.', 'src.storage')) for name in sys.modules)
print('actual-decode-boundary-ok')
'''
        root = Path(__file__).resolve().parents[3]
        dependencies = [path for path in sys.path if Path(path).name.casefold() == 'site-packages']
        for entry, raw in [('page', page_input()), ('derived', derived_input())]:
            with self.subTest(entry=entry):
                process = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', '-c', code,
                    str(root), json.dumps(dependencies), entry], input=json.dumps(raw, ensure_ascii=False),
                    capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stdout.strip(), 'actual-decode-boundary-ok')


if __name__ == '__main__':
    unittest.main()
